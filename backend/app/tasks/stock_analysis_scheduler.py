import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

import pytz
from sqlalchemy.orm import Session

from app.ai.llm_engine.runner import run_analysis_task
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.core.request_context import get_or_create_request_id
from app.crud.session import crud_session
from app.data.market_utils import is_trading_day
from app.models.async_task import AsyncTask
from app.models.stock_warehouse import StockWarehouse
from app.schemas.session import SessionCreate
from app.tasks.async_task_runner import async_task_runner
from app.tasks.scheduled_task_registry import ScheduledTask
from app.tasks.scheduled_task_registry import ScheduledTaskSnapshot
from app.tasks.task_functions import sync_stock_data_func
from app.tasks.task_manager import task_manager

logger = get_logger(__name__)

DEFAULT_AUTO_ANALYSIS_TIME = "09:35"
DEFAULT_TRADING_FREQUENCY = "中长线持有 (Position Trading)"
DEFAULT_TRADING_STRATEGY = "价值投资 (Value Investing)"
AUTO_ANALYSIS_SOURCE = "stock_warehouse_auto_analysis"
AUTO_ANALYSIS_MAX_LAUNCHES_PER_TICK = 3
AUTO_ANALYSIS_TRIGGER_WINDOW_MINUTES = 5
STOCK_AUTO_ANALYSIS_JOB_ID = "stock_warehouse_auto_analysis_scan"
VALID_AUTO_ANALYSIS_FREQUENCIES = {"daily", "weekly", "monthly"}


@dataclass(frozen=True)
class AutoAnalysisCandidate:
    """A warehouse stock that is ready for automatic analysis."""

    stock_id: int
    stock_code: str
    user_id: int


def _shanghai_now() -> datetime:
    """Return current Shanghai-local time as a naive datetime."""
    timezone = pytz.timezone("Asia/Shanghai")
    return datetime.now(timezone).replace(tzinfo=None)


def _parse_schedule_time(value: str | None) -> time:
    """Parse an HH:MM schedule value, falling back to the default."""
    raw_value = value or DEFAULT_AUTO_ANALYSIS_TIME
    try:
        hour, minute = raw_value.split(":", 1)
        return time(hour=int(hour), minute=int(minute))
    except (TypeError, ValueError):
        logger.warning("Invalid auto analysis time %s, using default %s", raw_value, DEFAULT_AUTO_ANALYSIS_TIME)
        return time(hour=9, minute=35)


def _normalize_frequency(value: str | None) -> str:
    """Normalize auto-analysis frequency."""
    if value in VALID_AUTO_ANALYSIS_FREQUENCIES:
        return value
    return "daily"


def _same_iso_week(left: datetime, right: datetime) -> bool:
    """Return whether two datetimes fall in the same ISO week."""
    left_year, left_week, _ = left.isocalendar()
    right_year, right_week, _ = right.isocalendar()
    return left_year == right_year and left_week == right_week


def is_due_for_auto_analysis(stock: StockWarehouse, now: datetime | None = None) -> bool:
    """Return whether a warehouse stock should launch automatic analysis now."""
    current_time = now or _shanghai_now()
    if not stock.is_active or not stock.auto_analysis_enabled:
        return False

    # When run-immediately is enabled, skip all schedule gates
    if stock.auto_analysis_run_immediately:
        return True

    schedule_time = _parse_schedule_time(stock.auto_analysis_time)
    scheduled_at = datetime.combine(current_time.date(), schedule_time)
    trigger_window_end = scheduled_at + timedelta(minutes=AUTO_ANALYSIS_TRIGGER_WINDOW_MINUTES)
    if not scheduled_at <= current_time < trigger_window_end:
        return False

    last_run = stock.last_auto_analysis_at
    if last_run is None:
        return True

    frequency = _normalize_frequency(stock.auto_analysis_frequency)
    if frequency == "daily":
        return last_run.date() < current_time.date()
    if frequency == "weekly":
        return not _same_iso_week(last_run, current_time)
    if frequency == "monthly":
        return (last_run.year, last_run.month) != (current_time.year, current_time.month)
    return False


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """Return stock auto-analysis task definitions for the central async scheduler."""
    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=run_due_auto_analyses,
                task_name="Stock Warehouse Auto Analysis Scan",
                trigger_type="interval",
                job_id=STOCK_AUTO_ANALYSIS_JOB_ID,
                trigger_args={"minutes": 1},
                misfire_grace_time=300,
            )
        ],
        disabled_job_ids=[],
    )


async def run_due_auto_analyses() -> dict[str, Any]:
    """Scan enabled warehouse stocks and launch due analysis jobs."""
    if not is_trading_day():
        return {"launched": 0, "skipped": "not_trading_day"}

    now = _shanghai_now()
    launched: list[dict[str, Any]] = []

    with SessionLocal() as db:
        candidates = _load_due_auto_analysis_candidates(db, now)

    for candidate in candidates:
        launch_info = await _launch_analysis(candidate.stock_id, now)
        if launch_info:
            launched.append(launch_info)

    if launched:
        logger.info("Launched %s stock auto-analysis task(s): %s", len(launched), launched)
    return {"launched": len(launched), "items": launched}


def _load_enabled_stocks(db: Session) -> list[StockWarehouse]:
    """Load active stocks with auto-analysis enabled."""
    return (
        db.query(StockWarehouse)
        .filter(
            StockWarehouse.is_active.is_(True),
            StockWarehouse.auto_analysis_enabled.is_(True),
        )
        .order_by(StockWarehouse.last_auto_analysis_at.asc().nullsfirst(), StockWarehouse.id.asc())
        .all()
    )


def _load_due_auto_analysis_candidates(db: Session, now: datetime) -> list[AutoAnalysisCandidate]:
    """Load due auto-analysis candidates without leaking ORM objects outside the session."""
    candidates: list[AutoAnalysisCandidate] = []
    for stock in _load_enabled_stocks(db):
        if len(candidates) >= AUTO_ANALYSIS_MAX_LAUNCHES_PER_TICK:
            break
        if not is_due_for_auto_analysis(stock, now):
            continue
        if _has_running_analysis_task(db, stock):
            continue
        candidates.append(
            AutoAnalysisCandidate(
                stock_id=stock.id,
                stock_code=stock.stock_code,
                user_id=stock.user_id,
            )
        )
    return candidates


def _has_running_analysis_task(db: Session, stock: StockWarehouse) -> bool:
    """Return whether the same user's stock already has a running analysis task."""
    running_tasks = (
        db.query(AsyncTask)
        .filter(
            AsyncTask.task_name == f"AI Analysis - {stock.stock_code}",
            AsyncTask.task_type == "ai_analysis",
            AsyncTask.status.in_(["pending", "running"]),
        )
        .all()
    )
    for task in running_tasks:
        parameters = task.parameters if isinstance(task.parameters, dict) else {}
        task_user_id = parameters.get("user_id")
        if task_user_id is None or int(task_user_id) == stock.user_id:
            return True
    return False


async def _sync_stock_data_before_analysis(stock_code: str) -> bool:
    """Submit a data-sync task for the stock and wait for it to complete."""
    from app.core.i18n import i18n_service

    sync_task_name = i18n_service.t("tasks.names.data_sync") + f" ({stock_code})"
    with SessionLocal() as db:
        sync_result = task_manager.submit_task(
            db=db,
            task_name=sync_task_name,
            task_type="db_sync",
            parameters={"stock_code": stock_code},
            allow_concurrent=False,
        )

    sync_task_id = sync_result["task_id"]

    # If a new task was created, submit it to the async task runner.
    if sync_result.get("new_task", True):
        submitted = async_task_runner.submit_task(
            task_id=sync_task_id,
            task_func=sync_stock_data_func,
            task_kwargs={
                "stock_code": stock_code,
                "task_id": sync_task_id,
                "allow_concurrent": False,
            },
            task_name=sync_task_name,
            request_id=get_or_create_request_id(),
        )
        if not submitted:
            logger.warning("Failed to submit data-sync async task for %s", stock_code)
            return False

    # Poll for completion (max ~5 minutes, 5-second intervals)
    for _ in range(60):
        await asyncio.sleep(5)
        with SessionLocal() as db:
            task_row = (
                db.query(AsyncTask.status, AsyncTask.error_message)
                .filter(AsyncTask.task_id == sync_task_id)
                .first()
            )
        if task_row and task_row.status in ("completed", "failed"):
            if task_row.status == "completed":
                logger.info("Data sync completed for %s before analysis", stock_code)
            else:
                logger.warning(
                    "Data sync failed for %s (continuing with analysis): %s",
                    stock_code,
                    task_row.error_message,
                )
            return True

    logger.warning("Data sync for %s timed out, continuing with analysis", stock_code)
    return True


async def _launch_analysis(
    stock_id: int,
    launched_at: datetime,
) -> dict[str, Any] | None:
    """Create a session, submit an async task record, and run the analysis workflow."""
    stock_code = ""
    try:
        with SessionLocal() as db:
            stock = _load_launchable_stock(db, stock_id, launched_at)
            if not stock:
                return None
            stock_code = stock.stock_code

        # Sync stock data before launching AI analysis
        await _sync_stock_data_before_analysis(stock_code)

        with SessionLocal() as db:
            stock = _load_launchable_stock(db, stock_id, launched_at)
            if not stock:
                return None

            session = crud_session.create(
                db=db,
                obj_in=SessionCreate(
                    user_id=stock.user_id,
                    stock_code=stock.stock_code,
                    trading_frequency=stock.auto_analysis_trading_frequency or DEFAULT_TRADING_FREQUENCY,
                    trading_strategy=stock.auto_analysis_trading_strategy or DEFAULT_TRADING_STRATEGY,
                ),
            )
            task_info = task_manager.submit_task(
                db=db,
                task_name=f"AI Analysis - {stock.stock_code}",
                task_type="ai_analysis",
                parameters={
                    "session_id": str(session.session_id),
                    "stock_code": stock.stock_code,
                    "trading_frequency": session.trading_frequency,
                    "trading_strategy": session.trading_strategy,
                    "source": AUTO_ANALYSIS_SOURCE,
                    "warehouse_id": stock.id,
                    "user_id": stock.user_id,
                },
                allow_concurrent=False,
            )
            if not task_info.get("new_task", True):
                return None

            stock.last_auto_analysis_at = launched_at
            stock.last_auto_analysis_session_id = str(session.session_id)
            stock.last_auto_analysis_task_id = task_info["task_id"]
            stock.last_auto_analysis_error = None
            stock.auto_analysis_run_immediately = False
            db.add(stock)
            db.commit()
            launch_kwargs = {
                "task_id": task_info["task_id"],
                "session_id": str(session.session_id),
                "stock_code": stock.stock_code,
                "trading_frequency": session.trading_frequency,
                "trading_strategy": session.trading_strategy,
            }
            launch_info = {
                "stock_code": stock.stock_code,
                "user_id": stock.user_id,
                "session_id": str(session.session_id),
                "task_id": task_info["task_id"],
            }

        asyncio.create_task(
            run_analysis_task(**launch_kwargs),
            name=f"auto-analysis-{launch_kwargs['task_id']}",
        )
        return launch_info
    except Exception as exc:
        logger.exception("Failed to launch auto analysis for %s", stock_code or stock_id)
        _record_launch_error(stock_id, exc)
        return None


def _load_launchable_stock(db: Session, stock_id: int, launched_at: datetime) -> StockWarehouse | None:
    """Load a stock if it is still due and has no running analysis task."""
    stock = db.query(StockWarehouse).filter(StockWarehouse.id == stock_id).first()
    if not stock:
        return None
    if not is_due_for_auto_analysis(stock, launched_at):
        return None
    if _has_running_analysis_task(db, stock):
        return None
    return stock


def _record_launch_error(stock_id: int, exc: Exception) -> None:
    """Persist the latest auto-analysis launch error for a warehouse stock."""
    with SessionLocal() as db:
        stock = db.query(StockWarehouse).filter(StockWarehouse.id == stock_id).first()
        if not stock:
            return
        stock.last_auto_analysis_error = str(exc)[:1000]
        db.add(stock)
        db.commit()
