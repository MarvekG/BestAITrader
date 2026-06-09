from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ScheduledTask:
    """Definition for one centrally scheduled async task."""

    task_func: Callable[..., Any]
    task_name: str
    trigger_type: str
    job_id: str
    trigger_args: dict[str, Any]
    task_kwargs: dict[str, Any] | None = None
    coalesce: bool = True
    max_instances: int = 1
    misfire_grace_time: int = 300
    run_immediately: bool = False


@dataclass(frozen=True)
class ScheduledTaskSnapshot:
    """Current scheduled task definitions and disabled job IDs."""

    tasks: list[ScheduledTask]
    disabled_job_ids: list[str]


def load_scheduled_tasks() -> ScheduledTaskSnapshot:
    """
    Load current non-data-refresh scheduled task definitions.

    Returns:
        A snapshot containing enabled task definitions plus job IDs that should
        be removed from the central scheduler.
    """
    from app.tasks import account_equity_snapshot_scheduler
    from app.tasks import async_task_cleanup_scheduler
    from app.tasks import experience_review_scheduler
    from app.tasks import experience_index_cleanup_scheduler
    from app.tasks import llm_usage_cleanup_scheduler
    from app.tasks import market_watch_scheduler
    from app.tasks import pending_order_match_scheduler
    from app.tasks import stock_analysis_scheduler

    tasks: list[ScheduledTask] = []
    disabled_job_ids: list[str] = []

    for module in (
        async_task_cleanup_scheduler,
        stock_analysis_scheduler,
        market_watch_scheduler,
        experience_review_scheduler,
        experience_index_cleanup_scheduler,
        llm_usage_cleanup_scheduler,
        account_equity_snapshot_scheduler,
        pending_order_match_scheduler,
    ):
        module_snapshot = module.get_scheduled_tasks()
        tasks.extend(module_snapshot.tasks)
        disabled_job_ids.extend(module_snapshot.disabled_job_ids)

    return ScheduledTaskSnapshot(tasks=tasks, disabled_job_ids=disabled_job_ids)
