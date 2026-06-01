from __future__ import annotations

from datetime import datetime
from datetime import timedelta

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.llm_usage_log import LLMUsageLog
from app.tasks.scheduled_task_registry import ScheduledTask
from app.tasks.scheduled_task_registry import ScheduledTaskSnapshot

logger = get_logger(__name__)

LLM_USAGE_CLEANUP_JOB_ID = "llm_usage_cleanup"
LLM_USAGE_RETENTION_DAYS = 7
LLM_USAGE_CLEANUP_HOUR = 3
LLM_USAGE_CLEANUP_MINUTE = 0


def _now() -> datetime:
    """Return current local datetime for retention calculations."""

    return datetime.now()


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """Return LLM usage cleanup task definitions for the central async scheduler."""

    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=cleanup_old_llm_usage,
                task_name="LLM Usage Cleanup",
                trigger_type="cron",
                job_id=LLM_USAGE_CLEANUP_JOB_ID,
                trigger_args={
                    "hour": LLM_USAGE_CLEANUP_HOUR,
                    "minute": LLM_USAGE_CLEANUP_MINUTE,
                },
                misfire_grace_time=3600,
            )
        ],
        disabled_job_ids=[],
    )


def cleanup_old_llm_usage(*, retention_days: int = LLM_USAGE_RETENTION_DAYS) -> dict[str, int]:
    """Delete backend LLM usage records older than the retention window."""

    cutoff = _now() - timedelta(days=retention_days)
    with SessionLocal() as db:
        deleted = db.query(LLMUsageLog).filter(LLMUsageLog.created_at < cutoff).delete(synchronize_session=False)
        db.commit()
    if deleted:
        logger.info("Deleted %s old LLM usage log(s)", deleted)
    return {"deleted": int(deleted or 0), "retention_days": retention_days}
