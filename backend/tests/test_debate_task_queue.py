from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.ai.llm_engine import debate_concurrency
from app.models.async_task import AsyncTask
from app.models.system_setting import SystemSetting
from app.models.user import User


def _debate_task(stock_code: str, *, created_at: datetime) -> AsyncTask:
    return AsyncTask(
        task_name=f"AI Analysis - {stock_code}",
        task_type="ai_analysis",
        status="pending",
        allow_concurrent=False,
        parameters={
            "session_id": f"session-{stock_code}",
            "stock_code": stock_code,
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_dispatch_starts_pending_tasks_up_to_running_limit(
    async_db_session,
    monkeypatch,
) -> None:
    """调度器按 running 上限启动任务，超出部分保持 pending。"""
    user = User(username="queue_limit", email="queue_limit@example.com", password_hash="hash")
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    base_time = datetime(2026, 1, 1, 9, 0, 0)
    async_db_session.add_all([
        SystemSetting(key="ai_debate.max_concurrent", value=2, description="test"),
        _debate_task("000001.SZ", created_at=base_time),
        _debate_task("000002.SZ", created_at=base_time + timedelta(seconds=1)),
        _debate_task("000003.SZ", created_at=base_time + timedelta(seconds=2)),
    ])
    await async_db_session.commit()

    submitted: list[str] = []

    def _fake_submit_task(*, task_id, task_func, task_kwargs):
        submitted.append(task_id)
        return True

    monkeypatch.setattr(
        "app.tasks.async_task_runner.async_task_runner.submit_task",
        _fake_submit_task,
    )

    dispatched = await debate_concurrency.dispatch_pending_debate_tasks()

    assert dispatched == 2
    tasks = (
        await async_db_session.execute(
            select(AsyncTask).order_by(AsyncTask.created_at.asc())
        )
    ).scalars().all()
    assert [task.task_id for task in tasks[:2]] == submitted
    assert tasks[2].status == "pending"


@pytest.mark.asyncio
async def test_dispatch_continues_after_task_completion(async_db_session, monkeypatch) -> None:
    """运行任务完成后，调度器继续按 FIFO 启动下一个排队任务。"""
    user = User(username="queue_fifo", email="queue_fifo@example.com", password_hash="hash")
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    base_time = datetime(2026, 1, 1, 9, 0, 0)
    first = _debate_task("000001.SZ", created_at=base_time)
    second = _debate_task("000002.SZ", created_at=base_time + timedelta(seconds=1))
    async_db_session.add_all([
        SystemSetting(key="ai_debate.max_concurrent", value=1, description="test"),
        first,
        second,
    ])
    await async_db_session.commit()

    submitted: list[str] = []

    def _fake_submit_task(*, task_id, task_func, task_kwargs):
        submitted.append(task_id)
        return True

    monkeypatch.setattr(
        "app.tasks.async_task_runner.async_task_runner.submit_task",
        _fake_submit_task,
    )

    assert await debate_concurrency.dispatch_pending_debate_tasks() == 1
    assert submitted == [first.task_id]

    first.status = "completed"
    await async_db_session.commit()

    await debate_concurrency.notify_debate_task_status(first.task_id, "completed")

    assert submitted == [first.task_id, second.task_id]
    await async_db_session.refresh(second)
    assert second.status == "pending"
