import pytest

from app.data.refresh_scheduler import DataRefreshScheduler
from app.tasks.task_functions import cleanup_stock_realtime_market_history


def test_setup_auto_tasks_schedules_realtime_quotes_every_minute_and_cleanup_hourly(monkeypatch) -> None:
    calls: list[dict] = []

    def _capture_task(
        self,
        task_func,
        task_name: str,
        trigger_type: str = "cron",
        task_kwargs: dict | None = None,
        trading_time_only: bool | None = None,
        **trigger_args,
    ) -> None:
        _ = self
        calls.append(
            {
                "task_func": task_func,
                "task_name": task_name,
                "trigger_type": trigger_type,
                "task_kwargs": task_kwargs,
                "trading_time_only": trading_time_only,
                "trigger_args": trigger_args,
            }
        )

    monkeypatch.setattr(DataRefreshScheduler, "add_task", _capture_task)

    scheduler = DataRefreshScheduler.__new__(DataRefreshScheduler)
    scheduler.setup_auto_tasks()

    realtime_task = next(call for call in calls if call["task_name"] == "Realtime Quoter 1m")
    assert realtime_task["trigger_type"] == "interval"
    assert realtime_task["trigger_args"] == {"minutes": 1}
    assert realtime_task["task_kwargs"] == {"tables": ["realtime"]}
    assert realtime_task["trading_time_only"] is True

    cleanup_task = next(call for call in calls if call["task_func"] is cleanup_stock_realtime_market_history)
    assert cleanup_task["trigger_type"] == "interval"
    assert cleanup_task["trigger_args"] == {"hours": 1}
    assert cleanup_task["trading_time_only"] is False


@pytest.mark.asyncio
async def test_scheduled_task_submission_skips_status_persistence(monkeypatch) -> None:
    submitted: list[dict] = []

    class _FakeScheduler:
        def add_job(self, func, *args, **kwargs) -> None:
            del args, kwargs
            self.func = func

    class _FakeRunner:
        def submit_task(self, **kwargs) -> bool:
            submitted.append(kwargs)
            return True

    async def _task_func() -> dict[str, str]:
        return {"status": "ok"}

    monkeypatch.setattr("app.data.refresh_scheduler.async_task_runner", _FakeRunner())
    monkeypatch.setattr("app.data.refresh_scheduler.is_trading_time", lambda: True)

    scheduler = DataRefreshScheduler.__new__(DataRefreshScheduler)
    scheduler.scheduler = _FakeScheduler()
    scheduler.add_task(_task_func, "Realtime Quoter 1m", trigger_type="interval", minutes=1)

    await scheduler.scheduler.func()

    assert submitted
    assert submitted[0]["persist_status"] is False
