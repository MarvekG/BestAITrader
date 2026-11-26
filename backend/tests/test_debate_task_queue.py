from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.ai.llm_engine import debate_concurrency
from app.models.async_task import AsyncTask
from app.models.session import Session
from app.models.system_setting import SystemSetting
from app.models.user import User


def _debate_task(
    stock_code: str,
    *,
    created_at: datetime,
    session_id: str | None = None,
    **parameter_overrides,
) -> AsyncTask:
    parameters = {
        "session_id": session_id or str(uuid4()),
        "stock_code": stock_code,
        "trading_frequency": "daily",
        "trading_strategy": "value",
    }
    parameters.update(parameter_overrides)
    return AsyncTask(
        task_name=f"AI Analysis - {stock_code}",
        task_type="ai_analysis",
        status="pending",
        allow_concurrent=False,
        parameters=parameters,
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

    def _fake_submit_task(*, task_id, task_func, task_kwargs, respect_global_limit):
        assert respect_global_limit is False
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

    def _fake_submit_task(*, task_id, task_func, task_kwargs, respect_global_limit):
        assert respect_global_limit is False
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


@pytest.mark.asyncio
async def test_dispatch_skips_invalid_task_and_continues_queue(async_db_session, monkeypatch) -> None:
    """队首任务参数无效时标记失败，并继续调度后续有效任务。"""
    user = User(username="queue_invalid", email="queue_invalid@example.com", password_hash="hash")
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    base_time = datetime(2026, 1, 1, 9, 0, 0)
    invalid = _debate_task("000001.SZ", created_at=base_time, trading_frequency=None)
    valid = _debate_task("000002.SZ", created_at=base_time + timedelta(seconds=1))
    async_db_session.add_all([
        SystemSetting(key="ai_debate.max_concurrent", value=1, description="test"),
        invalid,
        valid,
    ])
    await async_db_session.commit()

    submitted: list[str] = []

    def _fake_submit_task(*, task_id, task_func, task_kwargs, respect_global_limit):
        submitted.append(task_id)
        return True

    monkeypatch.setattr(
        "app.tasks.async_task_runner.async_task_runner.submit_task",
        _fake_submit_task,
    )

    assert await debate_concurrency.dispatch_pending_debate_tasks() == 1
    assert submitted == [valid.task_id]

    await async_db_session.refresh(invalid)
    await async_db_session.refresh(valid)
    assert invalid.status == "failed"
    assert invalid.error_message
    assert valid.status == "pending"


@pytest.mark.asyncio
async def test_dispatch_marks_session_failed_when_submit_fails(async_db_session, monkeypatch) -> None:
    """提交运行器失败时，任务和会话都应标记为失败。"""
    user = User(username="queue_submit_fail", email="queue_submit_fail@example.com", password_hash="hash")
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    session = Session(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="daily",
        trading_strategy="value",
        status="active",
    )
    async_db_session.add(session)
    await async_db_session.commit()
    await async_db_session.refresh(session)

    async_db_session.add_all([
        SystemSetting(key="ai_debate.max_concurrent", value=1, description="test"),
        _debate_task("000001.SZ", created_at=datetime(2026, 1, 1, 9, 0, 0), session_id=str(session.session_id)),
    ])
    await async_db_session.commit()

    def _fake_submit_task(*, task_id, task_func, task_kwargs, respect_global_limit):
        return False

    monkeypatch.setattr(
        "app.tasks.async_task_runner.async_task_runner.submit_task",
        _fake_submit_task,
    )

    assert await debate_concurrency.dispatch_pending_debate_tasks() == 0

    await async_db_session.refresh(session)
    task = (
        await async_db_session.execute(select(AsyncTask))
    ).scalars().one()
    assert task.status == "failed"
    assert session.status == "failed"


@pytest.mark.asyncio
async def test_dispatch_ignores_dispatched_running_task_in_count(async_db_session, monkeypatch) -> None:
    """已进入 running 的任务不再占用调度集合，避免重复扣减并发名额。"""
    user = User(username="queue_running", email="queue_running@example.com", password_hash="hash")
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    base_time = datetime(2026, 1, 1, 9, 0, 0)
    running = _debate_task("000001.SZ", created_at=base_time)
    async_db_session.add_all([
        SystemSetting(key="ai_debate.max_concurrent", value=2, description="test"),
        running,
    ])
    await async_db_session.commit()

    submitted: list[str] = []

    def _fake_submit_task(*, task_id, task_func, task_kwargs, respect_global_limit):
        submitted.append(task_id)
        return True

    monkeypatch.setattr(
        "app.tasks.async_task_runner.async_task_runner.submit_task",
        _fake_submit_task,
    )

    assert await debate_concurrency.dispatch_pending_debate_tasks() == 1
    assert submitted == [running.task_id]

    # 模拟任务已开始运行，但调度集合尚未收到 running 通知。
    running.status = "running"
    await async_db_session.commit()
    pending = _debate_task("000002.SZ", created_at=base_time + timedelta(seconds=1))
    async_db_session.add(pending)
    await async_db_session.commit()

    assert await debate_concurrency.dispatch_pending_debate_tasks() == 1
    assert submitted[-1] == pending.task_id
