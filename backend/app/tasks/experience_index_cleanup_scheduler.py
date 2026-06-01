from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.experience_index import ExperienceIndex
from app.models.experience_review_event import ExperienceReviewEvent
from app.tasks.scheduled_task_registry import ScheduledTask
from app.tasks.scheduled_task_registry import ScheduledTaskSnapshot

logger = get_logger(__name__)

EXPERIENCE_INDEX_CLEANUP_JOB_ID = "experience_index_cleanup"
EXPERIENCE_INDEX_RETENTION_DAYS = 7
EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS = 30
EXPERIENCE_INDEX_CLEANUP_HOUR = 3
EXPERIENCE_INDEX_CLEANUP_MINUTE = 30


def _now() -> datetime:
    """返回当前本地时间，用于计算索引保留窗口。

    Returns:
        当前本地时间。
    """
    return datetime.now()


def _coerce_bool(value: Any, default: bool) -> bool:
    """将配置值转换为布尔值。

    Args:
        value: 来自持久化配置的原始值。
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
        value: 来自持久化配置的原始值。
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


def get_experience_cleanup_config() -> dict[str, Any]:
    """读取 config.py 中的经验记录清理配置。

    Returns:
        已应用默认值和边界限制的清理配置。
    """
    return {
        "enabled": _coerce_bool(getattr(settings, "EXPERIENCE_CLEANUP_ENABLED", True), True),
        "index_retention_days": _coerce_int(
            getattr(settings, "EXPERIENCE_INDEX_RETENTION_DAYS", EXPERIENCE_INDEX_RETENTION_DAYS),
            default=EXPERIENCE_INDEX_RETENTION_DAYS,
            min_value=1,
            max_value=3650,
        ),
        "review_event_retention_days": _coerce_int(
            getattr(settings, "EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS", EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS),
            default=EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS,
            min_value=1,
            max_value=3650,
        ),
        "schedule_hour": _coerce_int(
            getattr(settings, "EXPERIENCE_CLEANUP_SCHEDULE_HOUR", EXPERIENCE_INDEX_CLEANUP_HOUR),
            default=EXPERIENCE_INDEX_CLEANUP_HOUR,
            min_value=0,
            max_value=23,
        ),
        "schedule_minute": _coerce_int(
            getattr(settings, "EXPERIENCE_CLEANUP_SCHEDULE_MINUTE", EXPERIENCE_INDEX_CLEANUP_MINUTE),
            default=EXPERIENCE_INDEX_CLEANUP_MINUTE,
            min_value=0,
            max_value=59,
        ),
    }


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """返回中心调度器使用的经验索引清理任务定义。

    Returns:
        包含启用任务或禁用任务 ID 的调度快照。
    """
    config = get_experience_cleanup_config()

    if not config["enabled"]:
        logger.info("experience index cleanup scheduler is disabled")
        return ScheduledTaskSnapshot(tasks=[], disabled_job_ids=[EXPERIENCE_INDEX_CLEANUP_JOB_ID])

    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=cleanup_old_experience_records,
                task_name="Experience Index Cleanup",
                trigger_type="cron",
                job_id=EXPERIENCE_INDEX_CLEANUP_JOB_ID,
                trigger_args={
                    "hour": config["schedule_hour"],
                    "minute": config["schedule_minute"],
                },
                task_kwargs={
                    "index_retention_days": config["index_retention_days"],
                    "review_event_retention_days": config["review_event_retention_days"],
                },
                misfire_grace_time=3600,
            )
        ],
        disabled_job_ids=[],
    )


def cleanup_old_experience_records(
    *,
    index_retention_days: int | None = None,
    review_event_retention_days: int | None = None,
) -> dict[str, int]:
    """删除超过保留窗口的经验索引和复盘事件记录。

    Args:
        index_retention_days: 经验索引保留天数；为空时从 config.py 读取。
        review_event_retention_days: 复盘事件保留天数；为空时从 config.py 读取。

    Returns:
        各类记录删除数量和实际使用的保留天数。
    """
    index_result = cleanup_old_experience_indexes(retention_days=index_retention_days)
    review_event_result = cleanup_old_experience_review_events(retention_days=review_event_retention_days)
    return {
        "experience_indexes_deleted": index_result["deleted"],
        "experience_index_retention_days": index_result["retention_days"],
        "experience_review_events_deleted": review_event_result["deleted"],
        "experience_review_event_retention_days": review_event_result["retention_days"],
    }


def cleanup_old_experience_indexes(*, retention_days: int | None = None) -> dict[str, int]:
    """删除超过保留窗口的经验索引记录。

    只删除 `experience_indexes` 目录记录，不删除 Memory 经验正文或复盘事件。

    Args:
        retention_days: 调度器传入的保留天数；为空时从 config.py 读取。

    Returns:
        删除数量和实际使用的保留天数。
    """
    with SessionLocal() as db:
        if retention_days is None:
            config = get_experience_cleanup_config()
            retention_days = config["index_retention_days"]
        retention_days = _coerce_int(retention_days, default=EXPERIENCE_INDEX_RETENTION_DAYS, min_value=1, max_value=3650)
        cutoff = _now() - timedelta(days=retention_days)
        deleted = db.query(ExperienceIndex).filter(ExperienceIndex.created_at < cutoff).delete(synchronize_session=False)
        db.commit()

    if deleted:
        logger.info(
            "deleted old experience index rows",
            extra={"deleted": int(deleted), "retention_days": retention_days},
        )
    return {"deleted": int(deleted or 0), "retention_days": retention_days}


def cleanup_old_experience_review_events(*, retention_days: int | None = None) -> dict[str, int]:
    """删除超过一个月保留窗口的经验复盘事件记录。

    Args:
        retention_days: 复盘事件保留天数；为空时从 config.py 读取。

    Returns:
        删除数量和复盘事件保留天数。
    """
    if retention_days is None:
        config = get_experience_cleanup_config()
        retention_days = config["review_event_retention_days"]
    retention_days = _coerce_int(
        retention_days,
        default=EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS,
        min_value=1,
        max_value=3650,
    )
    cutoff = _now() - timedelta(days=retention_days)
    with SessionLocal() as db:
        deleted = (
            db.query(ExperienceReviewEvent)
            .filter(ExperienceReviewEvent.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.commit()

    if deleted:
        logger.info(
            "deleted old experience review event rows",
            extra={"deleted": int(deleted), "retention_days": retention_days},
        )
    return {"deleted": int(deleted or 0), "retention_days": retention_days}
