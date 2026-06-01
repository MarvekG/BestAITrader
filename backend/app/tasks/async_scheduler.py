from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.logger import get_logger
from app.tasks.scheduled_task_registry import load_scheduled_tasks

logger = get_logger(__name__)

ASYNC_SCHEDULER_TIMEZONE = ZoneInfo("Asia/Shanghai")


class AsyncTaskScheduler:
    """Central scheduler for async in-process periodic tasks."""

    def __init__(self) -> None:
        self.scheduler: AsyncIOScheduler | None = None
        self.init_scheduler()

    def init_scheduler(self) -> None:
        """Initialize the underlying APScheduler instance."""
        self.scheduler = AsyncIOScheduler(timezone=ASYNC_SCHEDULER_TIMEZONE)

    def start(self) -> None:
        """Start the scheduler and install registered system tasks."""
        if self.scheduler and not self.scheduler.running:
            self.scheduler.start()
            self.setup_auto_tasks()
            logger.info("Async task scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Async task scheduler stopped")

    def add_task(
        self,
        task_func: Callable[..., Any],
        task_name: str,
        trigger_type: str = "cron",
        task_kwargs: dict[str, Any] | None = None,
        job_id: str | None = None,
        coalesce: bool = True,
        max_instances: int = 1,
        misfire_grace_time: int = 300,
        run_immediately: bool = False,
        **trigger_args: Any,
    ) -> None:
        """
        Add an async scheduled task.

        Args:
            task_func: Task function. Coroutine functions are awaited.
            task_name: Human-readable task name.
            trigger_type: APScheduler trigger type, either ``cron`` or ``interval``.
            task_kwargs: Keyword arguments passed to the task function.
            job_id: Stable APScheduler job ID.
            coalesce: Whether missed runs should be coalesced.
            max_instances: Maximum concurrent instances for the same job.
            misfire_grace_time: Seconds a missed run is still allowed to execute.
            run_immediately: Whether to run once as soon as the scheduler starts.
            **trigger_args: Trigger-specific arguments.
        """
        if self.scheduler is None:
            return
        if task_kwargs is None:
            task_kwargs = {}

        async def job_wrapper() -> Any:
            logger.debug("Triggering scheduled async task: %s", task_name)
            result = task_func(**task_kwargs)
            if inspect.isawaitable(result):
                result = await result
            logger.debug("Scheduled async task completed: %s", task_name)
            return result

        trigger = self._build_trigger(trigger_type, trigger_args)
        scheduler_kwargs: dict[str, Any] = {}
        if run_immediately:
            scheduler_kwargs["next_run_time"] = datetime.now(ASYNC_SCHEDULER_TIMEZONE)

        self.scheduler.add_job(
            job_wrapper,
            trigger=trigger,
            id=job_id or f"async_{task_name.lower().replace(' ', '_')}",
            name=task_name,
            replace_existing=True,
            coalesce=coalesce,
            max_instances=max_instances,
            misfire_grace_time=misfire_grace_time,
            **scheduler_kwargs,
        )
        logger.debug("Async task scheduled: %s via %s %s", task_name, trigger_type, trigger_args)

    def get_job(self, job_id: str) -> Any | None:
        """Return a scheduled job by ID."""
        if self.scheduler is None:
            return None
        return self.scheduler.get_job(job_id)

    def remove_job(self, job_id: str) -> None:
        """Remove a scheduled job by ID when present."""
        if self.scheduler is None:
            return
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def setup_auto_tasks(self) -> None:
        """Install all non-data-refresh periodic tasks."""
        task_snapshot = load_scheduled_tasks()
        for job_id in task_snapshot.disabled_job_ids:
            self.remove_job(job_id)
        for task in task_snapshot.tasks:
            self.add_task(
                task.task_func,
                task.task_name,
                trigger_type=task.trigger_type,
                task_kwargs=task.task_kwargs,
                job_id=task.job_id,
                coalesce=task.coalesce,
                max_instances=task.max_instances,
                misfire_grace_time=task.misfire_grace_time,
                run_immediately=task.run_immediately,
                **task.trigger_args,
            )
        logger.info("Async system tasks scheduled and active")

    def refresh_schedule(self) -> None:
        """Refresh dynamic async schedules from persisted configuration."""
        if self.scheduler and self.scheduler.running:
            self.setup_auto_tasks()

    def _build_trigger(
        self,
        trigger_type: str,
        trigger_args: dict[str, Any],
    ) -> CronTrigger | IntervalTrigger:
        """Build an APScheduler trigger."""
        if trigger_type == "cron":
            return CronTrigger(timezone=ASYNC_SCHEDULER_TIMEZONE, **trigger_args)
        if trigger_type == "interval":
            return IntervalTrigger(**trigger_args)
        raise ValueError(f"Unsupported trigger_type: {trigger_type}")


async_task_scheduler = AsyncTaskScheduler()
