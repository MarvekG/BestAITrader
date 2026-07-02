from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete

from app.core.config import settings
from app.core import database as database_module
from app.core.logger import get_logger
from app.models.async_task import AsyncTask
from app.tasks.scheduled_task_registry import ScheduledTask
from app.tasks.scheduled_task_registry import ScheduledTaskSnapshot

logger = get_logger(__name__)

ASYNC_TASK_CLEANUP_JOB_ID = "async_task_cleanup"
ASYNC_TASK_RETENTION_DAYS = 30
ASYNC_TASK_CLEANUP_HOUR = 4
ASYNC_TASK_CLEANUP_MINUTE = 0


def _now() -> datetime:
    """返回当前本地时间，用于计算异步任务保留窗口。

    Returns:
        当前本地时间。
    """
    return datetime.now()


def _coerce_bool(value: Any, default: bool) -> bool:
    """将配置值转换为布尔值。

    Args:
        value: 来自配置的原始值。
        default: 输入无法识别时使用的默认值。

    Returns:
        解析后的布尔值。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    """将配置值转换为带上下界的整数。

    Args:
        value: 来自配置的原始值。
        default: 输入无法解析时使用的默认值。
        min_value: 允许的最小值，包含边界。
        max_value: 允许的最大值，包含边界。

    Returns:
        解析并限制在上下界内的整数。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def get_async_task_cleanup_config() -> dict[str, Any]:
    """读取异步任务清理配置。

    Returns:
        已应用默认值和边界限制的清理配置。
    """
    return {
        "enabled": _coerce_bool(getattr(settings, "ASYNC_TASK_CLEANUP_ENABLED", True), True),
        "retention_days": _coerce_int(
            getattr(settings, "ASYNC_TASK_RETENTION_DAYS", ASYNC_TASK_RETENTION_DAYS),
            default=ASYNC_TASK_RETENTION_DAYS,
            min_value=1,
            max_value=3650,
        ),
        "schedule_hour": _coerce_int(
            getattr(settings, "ASYNC_TASK_CLEANUP_SCHEDULE_HOUR", ASYNC_TASK_CLEANUP_HOUR),
            default=ASYNC_TASK_CLEANUP_HOUR,
            min_value=0,
            max_value=23,
        ),
        "schedule_minute": _coerce_int(
            getattr(settings, "ASYNC_TASK_CLEANUP_SCHEDULE_MINUTE", ASYNC_TASK_CLEANUP_MINUTE),
            default=ASYNC_TASK_CLEANUP_MINUTE,
            min_value=0,
            max_value=59,
        ),
    }


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """返回中心调度器使用的异步任务清理任务定义。

    Returns:
        包含启用任务或禁用任务 ID 的调度快照。
    """
    config = get_async_task_cleanup_config()
    if not config["enabled"]:
        logger.info("async task cleanup scheduler is disabled")
        return ScheduledTaskSnapshot(tasks=[], disabled_job_ids=[ASYNC_TASK_CLEANUP_JOB_ID])

    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=cleanup_old_async_tasks,
                task_name="Async Task Cleanup",
                trigger_type="cron",
                job_id=ASYNC_TASK_CLEANUP_JOB_ID,
                trigger_args={
                    "hour": config["schedule_hour"],
                    "minute": config["schedule_minute"],
                },
                task_kwargs={"retention_days": config["retention_days"]},
                misfire_grace_time=3600,
            )
        ],
        disabled_job_ids=[],
    )


async def cleanup_old_async_tasks(*, retention_days: int | None = None) -> dict[str, int]:
    """删除超过保留窗口的异步任务记录。

    Args:
        retention_days: 任务保留天数；为空时从 config.py 读取。

    Returns:
        删除数量和实际使用的保留天数。
    """
    if retention_days is None:
        config = get_async_task_cleanup_config()
        retention_days = config["retention_days"]
    retention_days = _coerce_int(
        retention_days,
        default=ASYNC_TASK_RETENTION_DAYS,
        min_value=1,
        max_value=3650,
    )
    cutoff = _now() - timedelta(days=retention_days)
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(delete(AsyncTask).where(AsyncTask.created_at < cutoff))
        await db.commit()
        deleted = result.rowcount or 0

    if deleted:
        logger.info(
            "deleted old async task rows",
            extra={"deleted": int(deleted), "retention_days": retention_days},
        )
    return {"deleted": int(deleted or 0), "retention_days": retention_days}
