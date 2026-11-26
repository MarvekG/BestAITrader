import pytest

from app.tasks import debate_dispatch_scheduler as scheduler_module
from app.tasks import scheduled_task_registry
from app.tasks.debate_dispatch_scheduler import DEBATE_DISPATCH_JOB_ID


def test_debate_dispatch_scheduler_registers_interval_job() -> None:
    """补偿调度任务按分钟轮询，兜底恢复 pending 队列。"""
    snapshot = scheduler_module.get_scheduled_tasks()

    assert snapshot.disabled_job_ids == []
    task = snapshot.tasks[0]
    assert task.job_id == DEBATE_DISPATCH_JOB_ID
    assert task.trigger_type == "interval"
    assert task.trigger_args == {"seconds": scheduler_module.DEBATE_DISPATCH_INTERVAL_SECONDS}


def test_scheduled_task_registry_includes_debate_dispatch_job() -> None:
    snapshot = scheduled_task_registry.load_scheduled_tasks()

    assert DEBATE_DISPATCH_JOB_ID in {task.job_id for task in snapshot.tasks}


@pytest.mark.asyncio
async def test_dispatch_queued_debates_uses_safe_dispatcher(monkeypatch) -> None:
    """补偿调度通过安全包装触发队列调度。"""
    calls: list[bool] = []

    async def _fake_dispatch() -> int:
        calls.append(True)
        return 2

    monkeypatch.setattr(
        "app.ai.llm_engine.debate_concurrency.dispatch_pending_debate_tasks_safely",
        _fake_dispatch,
    )

    assert await scheduler_module.dispatch_queued_debates() == 2
    assert calls == [True]
