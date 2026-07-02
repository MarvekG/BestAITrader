from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytz
from sqlalchemy import select

from app.ai.market_watch.audit import cleanup_old_events
from app.ai.market_watch.schemas import MIN_MARKET_WATCH_SCAN_INTERVAL_SECONDS
from app.ai.market_watch.service import scan_market_watch
from app.ai.market_watch.settings import get_market_watch_settings
from app.core import database as database_module
from app.core.config import settings
from app.core.logger import get_logger
from app.tasks.scheduled_task_registry import ScheduledTask
from app.tasks.scheduled_task_registry import ScheduledTaskSnapshot

logger = get_logger(__name__)

MARKET_WATCH_AUDIT_CLEANUP_JOB_ID = "market_watch_audit_cleanup"
MARKET_WATCH_SCAN_JOB_ID = "market_watch_auto_scan"
MARKET_WATCH_TICK_SECONDS = MIN_MARKET_WATCH_SCAN_INTERVAL_SECONDS
last_scan_at_by_user: dict[int, datetime] = {}
running_user_ids: set[int] = set()


def _shanghai_now() -> datetime:
    """Return current Shanghai-local time as a naive datetime."""
    timezone = pytz.timezone("Asia/Shanghai")
    return datetime.now(timezone).replace(tzinfo=None)


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """Return market watch task definitions for the central async scheduler."""
    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=run_due_scans,
                task_name="Market Watch Auto Scan",
                trigger_type="interval",
                job_id=MARKET_WATCH_SCAN_JOB_ID,
                trigger_args={"seconds": MARKET_WATCH_TICK_SECONDS},
                misfire_grace_time=MARKET_WATCH_TICK_SECONDS,
            ),
            ScheduledTask(
                task_func=run_audit_cleanup,
                task_name="Market Watch Audit Cleanup",
                trigger_type="interval",
                job_id=MARKET_WATCH_AUDIT_CLEANUP_JOB_ID,
                trigger_args={"days": 1},
                misfire_grace_time=300,
                run_immediately=True,
            ),
        ],
        disabled_job_ids=[],
    )


async def run_audit_cleanup() -> dict[str, int]:
    """
    删除超过配置保留窗口的盯盘审计事件。

    Returns:
        删除数量和实际使用的保留天数。
    """
    retention_days = settings.MARKET_WATCH_EVENT_RETENTION_DAYS
    deleted_count = await cleanup_old_events(retention_days=retention_days)
    if deleted_count:
        logger.info(
            "deleted old market watch audit events",
            extra={"deleted": deleted_count, "retention_days": retention_days},
        )
    return {"deleted": deleted_count, "retention_days": retention_days}


async def run_due_scans() -> dict[str, Any]:
    """Run market watch scans whose per-user intervals are due."""
    now = _shanghai_now()
    launched: list[dict[str, Any]] = []
    skipped = 0

    user_ids = await _load_active_user_ids()

    for user_id in user_ids:
        settings = await get_market_watch_settings(user_id)
        if not settings.auto_scan_enabled:
            skipped += 1
            continue
        if not _is_due(user_id, settings.scan_interval_seconds, now):
            skipped += 1
            continue
        if user_id in running_user_ids:
            skipped += 1
            continue

        last_scan_at_by_user[user_id] = now
        running_user_ids.add(user_id)
        try:
            result = await scan_market_watch(
                user_id,
                now=now,
                debate_launcher=_launch_debate_task,
            )
            launched.append(
                {
                    "user_id": user_id,
                    "stock_count": result.get("stock_count", 0),
                    "news_count": result.get("news_count", 0),
                    "debate_status": result.get("debate_launch", {}).get("status"),
                }
            )
        except Exception as exc:
            logger.exception("Market watch scheduled scan failed for user %s: %s", user_id, exc)
        finally:
            running_user_ids.discard(user_id)

    if launched:
        logger.info("Ran %s market watch scheduled scan(s): %s", len(launched), launched)
    return {"scanned": len(launched), "skipped": skipped, "items": launched}


async def _load_active_user_ids() -> list[int]:
    """Load active user IDs that may have market watch enabled."""
    from app.models.user import User

    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(select(User.id).where(User.is_active.is_(True)).order_by(User.id.asc()))
        return [int(user_id) for user_id in result.scalars().all()]


def _is_due(user_id: int, interval_seconds: int, now: datetime) -> bool:
    """Return whether the user scan interval has elapsed."""
    last_scan_at = last_scan_at_by_user.get(user_id)
    if last_scan_at is None:
        return True
    return (now - last_scan_at).total_seconds() >= interval_seconds


def _launch_debate_task(**launch_kwargs: Any) -> None:
    """Schedule an AI analysis task from a background market watch scan."""
    from app.ai.llm_engine.runner import run_analysis_task

    asyncio.create_task(
        run_analysis_task(**launch_kwargs),
        name=f"market-watch-analysis-{launch_kwargs['task_id']}",
    )
