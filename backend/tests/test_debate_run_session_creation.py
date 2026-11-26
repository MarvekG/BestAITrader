from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.async_task import AsyncTask
from app.models.session import Session
from app.models.user import User


async def _get_session_count(db) -> int:
    return int((await db.execute(select(func.count()).select_from(Session))).scalar_one() or 0)


@pytest.mark.asyncio
async def test_run_debate_queues_and_creates_session_when_limit_reached(
    client,
    auth_headers,
    async_db_session,
) -> None:
    """运行并发达到上限时，不带 session_id 的启动请求仍创建会话并把任务放入队列。"""
    client.put(
        "/api/v1/general/runtime-settings",
        headers=auth_headers,
        json={"ai_debate_max_concurrent": 1},
    )
    user = (await async_db_session.execute(select(User))).scalars().first()
    async_db_session.add(
        AsyncTask(
            task_name="AI Analysis - 000001.SZ",
            task_type="ai_analysis",
            status="running",
            allow_concurrent=False,
            parameters={"stock_code": "000001.SZ"},
            user_id=user.id,
        )
    )
    await async_db_session.commit()
    session_count_before = await _get_session_count(async_db_session)

    response = client.post(
        "/api/v1/debate/run",
        headers=auth_headers,
        json={
            "stock_code": "000002.SZ",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["queued"] is True
    assert await _get_session_count(async_db_session) == session_count_before + 1

    queued_task = (
        await async_db_session.execute(
            select(AsyncTask).where(AsyncTask.status == "pending")
        )
    ).scalars().one()
    assert queued_task.parameters["stock_code"] == "000002.SZ"


@pytest.mark.asyncio
async def test_run_debate_rejects_before_creating_session_when_stock_is_running(
    client,
    auth_headers,
    async_db_session,
) -> None:
    """股票已有运行中任务时，不带 session_id 的启动请求不应创建 active 会话。"""
    user = (await async_db_session.execute(select(User))).scalars().first()
    async_db_session.add(
        AsyncTask(
            task_name="AI Analysis - 000001.SZ",
            task_type="ai_analysis",
            status="running",
            allow_concurrent=False,
            parameters={"stock_code": "000001.SZ"},
            user_id=user.id,
        )
    )
    await async_db_session.commit()
    session_count_before = await _get_session_count(async_db_session)

    response = client.post(
        "/api/v1/debate/run",
        headers=auth_headers,
        json={
            "stock_code": "000001.SZ",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
    )

    assert response.status_code == 400
    assert "000001.SZ" in response.json()["detail"]
    assert await _get_session_count(async_db_session) == session_count_before


@pytest.mark.asyncio
async def test_run_debate_rejects_when_same_stock_task_is_pending(
    client,
    auth_headers,
    async_db_session,
) -> None:
    """同一股票已有排队中任务时，新的启动请求不应再次入队。"""
    user = (await async_db_session.execute(select(User))).scalars().first()
    async_db_session.add(
        AsyncTask(
            task_name="AI Analysis - 000001.SZ",
            task_type="ai_analysis",
            status="pending",
            allow_concurrent=False,
            parameters={"stock_code": "000001.SZ"},
            user_id=user.id,
        )
    )
    await async_db_session.commit()
    session_count_before = await _get_session_count(async_db_session)
    task_count_before = int(
        (await async_db_session.execute(select(func.count()).select_from(AsyncTask))).scalar_one() or 0
    )

    response = client.post(
        "/api/v1/debate/run",
        headers=auth_headers,
        json={
            "stock_code": "000001.SZ",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
    )

    assert response.status_code == 400
    assert "000001.SZ" in response.json()["detail"]
    assert await _get_session_count(async_db_session) == session_count_before
    assert (
        int((await async_db_session.execute(select(func.count()).select_from(AsyncTask))).scalar_one() or 0)
        == task_count_before
    )


@pytest.mark.asyncio
async def test_run_debate_creates_session_after_launch_check_passes(
    client,
    auth_headers,
    async_db_session,
    monkeypatch,
) -> None:
    """股票互斥校验通过后，不带 session_id 的启动请求会创建会话并返回会话信息。"""
    submitted: dict = {}

    async def _fake_submit_task(**kwargs):
        submitted.update(kwargs)
        return {
            "task_id": "new-session-task",
            "status": "pending",
            "message": "started",
            "new_task": True,
        }

    async def _fake_send_debate_status(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.api.endpoints.debate.task_manager.submit_task", _fake_submit_task)
    monkeypatch.setattr("app.api.endpoints.debate.send_debate_status", _fake_send_debate_status)

    response = client.post(
        "/api/v1/debate/run",
        headers=auth_headers,
        json={
            "stock_code": "600519.SH",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["session_id"] == submitted["parameters"]["session_id"]
    assert payload["session"]["stock_code"] == "600519.SH"
    assert payload["session"]["status"] == "active"
    assert payload["session"]["task_status"] == "pending"
    assert await _get_session_count(async_db_session) == 1


@pytest.mark.asyncio
async def test_run_debate_discards_session_when_task_submission_fails(
    client,
    auth_headers,
    async_db_session,
    monkeypatch,
) -> None:
    """任务提交失败时，应清理刚创建的会话，不留下 active 空会话。"""

    async def _failing_submit_task(**_kwargs):
        raise RuntimeError("runner unavailable")

    async def _fake_send_debate_status(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.api.endpoints.debate.task_manager.submit_task", _failing_submit_task)
    monkeypatch.setattr("app.api.endpoints.debate.send_debate_status", _fake_send_debate_status)

    response = client.post(
        "/api/v1/debate/run",
        headers=auth_headers,
        json={
            "stock_code": "600519.SH",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
    )

    assert response.status_code == 500
    assert await _get_session_count(async_db_session) == 0


@pytest.mark.asyncio
async def test_run_debate_keeps_queued_task_when_dispatch_fails(
    client,
    auth_headers,
    async_db_session,
    monkeypatch,
) -> None:
    """调度触发异常时不应让已提交任务变成孤立记录，请求仍返回排队状态。"""

    async def _failing_dispatch():
        raise RuntimeError("dispatch unavailable")

    monkeypatch.setattr(
        "app.ai.llm_engine.debate_concurrency.dispatch_pending_debate_tasks",
        _failing_dispatch,
    )

    response = client.post(
        "/api/v1/debate/run",
        headers=auth_headers,
        json={
            "stock_code": "600519.SH",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["queued"] is True

    task = (await async_db_session.execute(select(AsyncTask))).scalars().one()
    assert task.status == "pending"
    session = (await async_db_session.execute(select(Session))).scalars().one()
    assert session.status == "active"
    assert task.parameters["session_id"] == str(session.session_id)
