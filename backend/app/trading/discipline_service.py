from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import BackgroundTasks

from app.ai.llm_engine.debate_concurrency import (
    DebateConcurrencyLimitReached,
    DebateStockTaskAlreadyRunning,
    ensure_debate_launch_available,
    find_running_debate_task_for_stock,
    format_ai_analysis_task_name,
)
from app.ai.market_watch.schemas import DebateParameters, WatchAiDecision
from app.ai.llm_engine.runner import run_analysis_task
from app.ai.market_watch.service import trading_frequency_label, trading_frequency_to_code
from app.ai.market_watch.service import trading_strategy_label, trading_strategy_to_code
from app.core import database as database_module
from app.core.logger import get_logger
from app.crud.session import crud_session
from app.crud.system_setting import system_setting
from app.models.async_task import AsyncTask
from app.models.session import Session as AnalysisSession
from app.schemas.session import SessionCreate
from app.models.stock_warehouse import StockWarehouse
from app.tasks.task_manager import task_manager
from app.tasks.stock_analysis_scheduler import DEFAULT_TRADING_FREQUENCY, DEFAULT_TRADING_STRATEGY
from app.trading.discipline_settings import PositionDisciplineSettingsResponse
from app.trading.discipline_settings import get_position_discipline_settings
from app.trading.pm_rules import TRIGGER_STOP_LOSS, TRIGGER_TAKE_PROFIT, evaluate_position_disciplines

logger = get_logger(__name__)

PM_DISCIPLINE_TRIGGER_LABELS = {
    "stop_loss": "PM stop-loss trigger",
    "take_profit": "PM take-profit trigger",
    "horizon_expired": "PM holding horizon expired",
}

PM_DISCIPLINE_SESSION_SOURCES = {
    TRIGGER_STOP_LOSS: "stop_loss",
    TRIGGER_TAKE_PROFIT: "take_profit",
}
POSITION_DISCIPLINE_DEDUP_STATE_KEY = "position_discipline.dedup_state"
POSITION_DISCIPLINE_DEDUP_STATE_DESCRIPTION = "Per-user latest stop-loss/take-profit discipline trigger state"


def should_skip_position_discipline_scan(settings: PositionDisciplineSettingsResponse, now: datetime) -> str | None:
    """判断当前时间是否应跳过止损止盈扫描。

    Args:
        settings: 当前用户扫描设置。
        now: 上海时区本地无时区时间。

    Returns:
        跳过原因；无需跳过时返回 None。
    """
    from app.ai.market_watch.schemas import parse_market_watch_time

    if now.weekday() >= 5 and not settings.scan_non_trading_days:
        return "non_trading_day"
    current_time = now.time().replace(second=0, microsecond=0)
    if current_time < parse_market_watch_time(settings.scan_start_time):
        return "outside_scan_time_window"
    if current_time > parse_market_watch_time(settings.scan_end_time):
        return "outside_scan_time_window"
    return None


async def scan_position_disciplines(
    user_id: int,
    *,
    settings: PositionDisciplineSettingsResponse | None = None,
    debate_launcher: Any | None = None,
    background_tasks: BackgroundTasks | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """独立扫描用户持仓的 PM 止损/止盈/持有期纪律。

    Args:
        user_id: 当前用户 ID。
        settings: 可选设置注入；为空时读取持久化设置。
        debate_launcher: 测试或调度器注入的辩论启动器。
        background_tasks: API 手动扫描时使用的后台任务。
        now: 可选当前时间，便于测试。

    Returns:
        扫描结果摘要。
    """
    current_settings = settings or get_position_discipline_settings(user_id)
    scan_now = now or datetime.now()
    if not current_settings.enabled:
        return {"scanned_at": scan_now.isoformat(), "status": "skipped", "reason": "disabled", "triggered": []}
    skip_reason = should_skip_position_discipline_scan(current_settings, scan_now)
    if skip_reason:
        return {"scanned_at": scan_now.isoformat(), "status": "skipped", "reason": skip_reason, "triggered": []}

    try:
        with database_module.SessionLocal() as db:
            triggered = evaluate_position_disciplines(db, user_id=user_id)
    except Exception as exc:
        logger.exception("Position discipline evaluation failed", extra={"user_id": user_id})
        return {"scanned_at": scan_now.isoformat(), "status": "failed", "error": str(exc), "triggered": []}

    launches: list[dict[str, Any]] = []
    for item in triggered:
        launches.append(
            await _handle_position_discipline_trigger(
                user_id=user_id,
                settings=current_settings,
                item=item,
                debate_launcher=debate_launcher,
                background_tasks=background_tasks,
            )
        )
    return {
        "scanned_at": scan_now.isoformat(),
        "status": "success",
        "triggered": triggered,
        "launched_debate_count": sum(1 for item in launches if item.get("status") == "launched"),
        "debate_launches": launches,
    }


async def _handle_position_discipline_trigger(
    *,
    user_id: int,
    settings: PositionDisciplineSettingsResponse,
    item: dict[str, Any],
    debate_launcher: Any | None,
    background_tasks: BackgroundTasks | None,
) -> dict[str, Any]:
    """处理单个持仓纪律触发事件。

    Args:
        user_id: 当前用户 ID。
        settings: 止损止盈扫描设置。
        item: `evaluate_position_disciplines` 返回的触发项。
        debate_launcher: 测试或调度器注入的辩论启动器。
        background_tasks: API 后台任务。

    Returns:
        辩论启动结果。
    """
    label = PM_DISCIPLINE_TRIGGER_LABELS.get(item["trigger"], item["trigger"])
    latest_price = item["latest_price"] or "unknown"
    pm_session_id = item["pm_session_id"] or "unknown"
    trading_frequency, trading_strategy = _resolve_stock_debate_preferences(
        user_id=user_id,
        stock_code=item["stock_code"],
    )
    decision = WatchAiDecision(
        stock_code=item["stock_code"],
        stock_name=item["stock_code"],
        action="start_debate",
        confidence=1.0,
        urgency="high",
        trigger_reason=f"{label}: threshold {item['threshold']}, latest price {latest_price}",
        evidence_summary=(
            f"PM position discipline triggered deterministically from session {pm_session_id}. "
            "This is a system-monitored risk review trigger; decide whether to hold, trim, sell, or rebalance "
            "based on updated evidence instead of liquidating mechanically."
        ),
        debate_parameters=DebateParameters(
            trading_frequency=trading_frequency_to_code(trading_frequency),
            trading_strategy=trading_strategy_to_code(trading_strategy),
            debate_focus=[label],
            risk_notes=[
                f"{label} triggered; compare holding, staged trimming, and liquidation before deciding"
            ],
        ),
    )
    if _is_duplicate_discipline_trigger(user_id=user_id, item=item):
        return {
            "status": "skipped",
            "reason": "duplicate_discipline_trigger",
            "stock_code": item["stock_code"],
            "trigger": item["trigger"],
        }

    if _find_existing_stock_task(item["stock_code"]):
        return {"status": "skipped", "reason": "existing_task", "stock_code": item["stock_code"]}

    if not settings.auto_launch_debate:
        return {"status": "skipped", "reason": "auto_launch_disabled", "stock_code": item["stock_code"]}

    discipline_trigger = {
        "trigger_type": item["trigger"],
        "threshold": str(item["threshold"]),
        "latest_price": str(item["latest_price"]) if item["latest_price"] is not None else None,
        "source_pm_session_id": str(item["pm_session_id"]) if item["pm_session_id"] is not None else None,
        "source": "position_discipline",
    }
    try:
        launch = await _create_and_schedule_position_discipline_debate(
            user_id=user_id,
            decision=decision,
            debate_launcher=debate_launcher,
            background_tasks=background_tasks,
            session_source=PM_DISCIPLINE_SESSION_SOURCES.get(item["trigger"], "market_watch"),
            discipline_trigger=discipline_trigger,
        )
    except DebateStockTaskAlreadyRunning as exc:
        return {
            "status": "skipped",
            "reason": "existing_task",
            "stock_code": item["stock_code"],
            "task_id": exc.task_id,
        }
    except DebateConcurrencyLimitReached as exc:
        return {
            "status": "skipped",
            "reason": "concurrency_limit",
            "stock_code": item["stock_code"],
            "error": str(exc),
        }
    if launch.get("status") == "failed":
        return {"stock_code": item["stock_code"], **launch}

    _mark_discipline_trigger_seen(
        user_id=user_id,
        item=item,
        session_id=launch["session_id"],
        task_id=launch["task_id"],
    )
    return {"status": "launched", "stock_code": item["stock_code"], **launch}


def _find_existing_stock_task(stock_code: str) -> bool:
    """检查指定股票是否已有待执行或运行中的 AI 分析任务。

    Args:
        stock_code: 标准股票代码。

    Returns:
        存在同股票待执行或运行中任务时返回 True。
    """
    with database_module.SessionLocal() as db:
        return find_running_debate_task_for_stock(db, stock_code) is not None


async def _create_and_schedule_position_discipline_debate(
    *,
    user_id: int,
    decision: WatchAiDecision,
    debate_launcher: Any | None,
    background_tasks: BackgroundTasks | None,
    session_source: str,
    discipline_trigger: dict[str, Any],
) -> dict[str, Any]:
    """创建止损止盈复议会话和任务，并调度后台分析。

    Args:
        user_id: 当前用户 ID。
        decision: 由确定性纪律触发构造的复议决策。
        debate_launcher: 测试或调度器注入的任务启动器。
        background_tasks: API 手动扫描时使用的后台任务。
        session_source: 新建会话来源。
        discipline_trigger: 持仓纪律扫描生成的结构化触发上下文。

    Returns:
        新建会话 ID 和任务 ID。
    """
    parameters = decision.debate_parameters
    stock_code = decision.stock_code
    trading_frequency = trading_frequency_label(parameters.trading_frequency)
    trading_strategy = trading_strategy_label(parameters.trading_strategy)
    with database_module.SessionLocal() as db:
        ensure_debate_launch_available(db, stock_code)
        session = crud_session.create(
            db,
            obj_in=SessionCreate(
                user_id=user_id,
                stock_code=stock_code,
                trading_frequency=trading_frequency,
                trading_strategy=trading_strategy,
                source=session_source,
            ),
        )
        session_id = str(session.session_id)
        task_parameters = {
            "session_id": session_id,
            "stock_code": stock_code,
            "trading_frequency": trading_frequency,
            "trading_strategy": trading_strategy,
            "discipline_trigger": discipline_trigger,
        }
        task_info = task_manager.submit_task(
            db=db,
            task_name=format_ai_analysis_task_name(stock_code),
            task_type="ai_analysis",
            parameters=task_parameters,
            allow_concurrent=False,
            user_id=user_id,
        )
        task_id = task_info["task_id"]

    launch_kwargs = {
        "task_id": task_id,
        "session_id": session_id,
        "stock_code": stock_code,
        "trading_frequency": trading_frequency,
        "trading_strategy": trading_strategy,
        "trigger_reason": decision.trigger_reason,
        "evidence_summary": decision.evidence_summary,
        "discipline_trigger": discipline_trigger,
    }
    try:
        await _schedule_position_discipline_debate_task(
            launch_kwargs=launch_kwargs,
            debate_launcher=debate_launcher,
            background_tasks=background_tasks,
        )
    except Exception as exc:
        error_message = str(exc)
        logger.exception(
            "Position discipline debate scheduling failed",
            extra={"user_id": user_id, "stock_code": stock_code, "session_id": session_id, "task_id": task_id},
        )
        _mark_launch_records_failed(session_id=session_id, task_id=task_id, error_message=error_message)
        return {
            "status": "failed",
            "reason": "launch_failed",
            "session_id": session_id,
            "task_id": task_id,
            "error": error_message,
        }
    return {"session_id": session_id, "task_id": task_id}


def _mark_launch_records_failed(*, session_id: str, task_id: str, error_message: str) -> None:
    """把已创建但未成功调度的止损止盈复议记录标记为失败。

    Args:
        session_id: 已创建的复议会话 ID。
        task_id: 已创建的异步任务 ID。
        error_message: 调度失败原因。
    """
    with database_module.SessionLocal() as db:
        task = db.query(AsyncTask).filter(AsyncTask.task_id == task_id).first()
        if task is not None:
            task.status = "failed"
            task.error_message = error_message
            task.completed_at = datetime.now(timezone.utc)

        session = db.query(AnalysisSession).filter(AnalysisSession.session_id == UUID(session_id)).first()
        if session is not None:
            session.status = "failed"
        db.commit()


async def _schedule_position_discipline_debate_task(
    *,
    launch_kwargs: dict[str, Any],
    debate_launcher: Any | None,
    background_tasks: BackgroundTasks | None,
) -> None:
    """调度止损止盈复议分析任务。

    Args:
        launch_kwargs: `run_analysis_task` 所需参数。
        debate_launcher: 测试或调度器注入的任务启动器。
        background_tasks: API 手动扫描时使用的后台任务。
    """
    if debate_launcher is not None:
        result = debate_launcher(**launch_kwargs)
        if inspect.isawaitable(result):
            await result
        return
    if background_tasks is not None:
        background_tasks.add_task(run_analysis_task, **launch_kwargs)
        return
    raise RuntimeError("position discipline debate scheduler is unavailable")


def _discipline_trigger_state(item: dict[str, Any], *, session_id: str | None, task_id: str | None) -> dict[str, Any]:
    """构造单只股票最新纪律触发状态。

    Args:
        item: `evaluate_position_disciplines` 返回的触发项。
        session_id: 本次复议会话 ID。
        task_id: 本次复议任务 ID。

    Returns:
        可写入 system_settings 的 JSON 兼容状态。
    """
    return {
        "trigger": str(item["trigger"]),
        "threshold": str(item["threshold"]),
        "pm_session_id": str(item.get("pm_session_id") or ""),
        "session_id": session_id,
        "task_id": task_id,
        "triggered_at": int(datetime.now().timestamp()),
        "latest_price": str(item.get("latest_price") or ""),
    }


def _is_same_discipline_trigger(existing: dict[str, Any], item: dict[str, Any]) -> bool:
    """判断已记录状态是否对应同一次 PM 纪律触发。

    Args:
        existing: system_settings 中当前股票的最新触发状态。
        item: `evaluate_position_disciplines` 返回的触发项。

    Returns:
        trigger、threshold 和 pm_session_id 都一致时返回 True。
    """
    return (
        str(existing.get("trigger") or "") == str(item["trigger"])
        and str(existing.get("threshold") or "") == str(item["threshold"])
        and str(existing.get("pm_session_id") or "") == str(item.get("pm_session_id") or "")
    )


def _load_discipline_dedup_state(user_id: int) -> dict[str, Any]:
    """读取当前用户的止损止盈扫描去重状态。

    Args:
        user_id: 当前用户 ID。

    Returns:
        包含 `stocks` 映射的状态字典。
    """
    with database_module.SessionLocal() as db:
        row = system_setting.get_by_key(db, POSITION_DISCIPLINE_DEDUP_STATE_KEY, user_id=user_id)
        value = row.value if row is not None and isinstance(row.value, dict) else {}
    stocks = value.get("stocks") if isinstance(value.get("stocks"), dict) else {}
    return {"stocks": stocks}


def _save_discipline_dedup_state(user_id: int, state: dict[str, Any]) -> None:
    """保存当前用户的止损止盈扫描去重状态。

    Args:
        user_id: 当前用户 ID。
        state: 包含 `stocks` 映射的状态字典。
    """
    with database_module.SessionLocal() as db:
        system_setting.set_value(
            db,
            key=POSITION_DISCIPLINE_DEDUP_STATE_KEY,
            value=state,
            description=POSITION_DISCIPLINE_DEDUP_STATE_DESCRIPTION,
            user_id=user_id,
        )


def _is_duplicate_discipline_trigger(*, user_id: int, item: dict[str, Any]) -> bool:
    """判断本次纪律触发是否已按股票最新状态处理过。

    Args:
        user_id: 当前用户 ID。
        item: `evaluate_position_disciplines` 返回的触发项。

    Returns:
        同一股票最新状态与本次 trigger、threshold、pm_session_id 一致时返回 True。
    """
    state = _load_discipline_dedup_state(user_id)
    existing = state["stocks"].get(str(item["stock_code"]))
    if not isinstance(existing, dict):
        return False
    return _is_same_discipline_trigger(existing, item)


def _mark_discipline_trigger_seen(
    *,
    user_id: int,
    item: dict[str, Any],
    session_id: str | None,
    task_id: str | None,
) -> None:
    """记录当前股票最新一次已成功启动复议的纪律触发。

    Args:
        user_id: 当前用户 ID。
        item: `evaluate_position_disciplines` 返回的触发项。
        session_id: 本次复议会话 ID。
        task_id: 本次复议任务 ID。
    """
    state = _load_discipline_dedup_state(user_id)
    state["stocks"][str(item["stock_code"])] = _discipline_trigger_state(
        item,
        session_id=session_id,
        task_id=task_id,
    )
    _save_discipline_dedup_state(user_id, state)


def _resolve_stock_debate_preferences(*, user_id: int, stock_code: str) -> tuple[str, str]:
    """读取股票仓库中的复议辩论偏好。

    Args:
        user_id: 当前用户 ID。
        stock_code: 触发纪律的股票代码。

    Returns:
        交易频率和交易策略；仓库缺失时返回股票自动分析默认值。
    """
    with database_module.SessionLocal() as db:
        stock = db.query(StockWarehouse).filter(
            StockWarehouse.user_id == user_id,
            StockWarehouse.stock_code == stock_code,
        ).first()
        if stock is None:
            return DEFAULT_TRADING_FREQUENCY, DEFAULT_TRADING_STRATEGY
        return (
            stock.auto_analysis_trading_frequency or DEFAULT_TRADING_FREQUENCY,
            stock.auto_analysis_trading_strategy or DEFAULT_TRADING_STRATEGY,
        )
