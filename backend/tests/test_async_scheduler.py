from app.tasks.async_scheduler import AsyncTaskScheduler
from app.tasks.scheduled_task_registry import ScheduledTask
from app.tasks.scheduled_task_registry import ScheduledTaskSnapshot


def test_async_scheduler_add_task_registers_interval_job() -> None:
    scheduler = AsyncTaskScheduler()

    async def sample_task() -> dict[str, str]:
        return {"status": "success"}

    scheduler.add_task(
        sample_task,
        "Sample Task",
        trigger_type="interval",
        job_id="sample_task",
        seconds=30,
        misfire_grace_time=30,
    )

    job = scheduler.get_job("sample_task")

    assert job is not None
    assert job.name == "Sample Task"
    assert job.trigger.interval.seconds == 30
    assert job.misfire_grace_time == 30


def test_async_scheduler_setup_auto_tasks_registers_registry_snapshot(monkeypatch) -> None:
    scheduler = AsyncTaskScheduler()

    async def sample_task() -> dict[str, str]:
        return {"status": "success"}

    monkeypatch.setattr(
        "app.tasks.async_scheduler.load_scheduled_tasks",
        lambda: ScheduledTaskSnapshot(
            tasks=[
                ScheduledTask(
                    task_func=sample_task,
                    task_name="Sample Registry Task",
                    trigger_type="interval",
                    job_id="sample_registry_task",
                    trigger_args={"seconds": 45},
                    misfire_grace_time=45,
                )
            ],
            disabled_job_ids=[],
        ),
    )

    scheduler.setup_auto_tasks()

    job = scheduler.get_job("sample_registry_task")
    assert job is not None
    assert job.name == "Sample Registry Task"
    assert job.trigger.interval.seconds == 45


def test_async_scheduler_setup_auto_tasks_removes_disabled_jobs(monkeypatch) -> None:
    scheduler = AsyncTaskScheduler()

    async def disabled_task() -> dict[str, str]:
        return {"status": "success"}

    scheduler.add_task(
        disabled_task,
        "Disabled Task",
        trigger_type="interval",
        job_id="disabled_task",
        seconds=30,
    )
    monkeypatch.setattr(
        "app.tasks.async_scheduler.load_scheduled_tasks",
        lambda: ScheduledTaskSnapshot(tasks=[], disabled_job_ids=["disabled_task"]),
    )

    scheduler.setup_auto_tasks()

    assert scheduler.get_job("disabled_task") is None
