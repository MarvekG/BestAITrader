from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.async_task import AsyncTask
from app.models.session import Session
from app.models.user import User


async def _get_session_count(db) -> int:
    return int((await db.execute(select(func.count()).select_from(Session))).scalar_one() or 0)


@pytest.mark.asyncio
async def test_run_debate_rejects_before_creating_session_when_limit_reached(
    client,
    auth_headers,
    async_db_session,
) -> None:
    """并发达到上限时，不带 session_id 的启动请求不应创建 active 会话。"""
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

    assert response.status_code == 429
    assert await _get_session_count(async_db_session) == session_count_before


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
async def test_run_debate_creates_session_after_launch_check_passes(
    client,
    auth_headers,
    async_db_session,
    monkeypatch,
) -> None:
    """并发校验通过后，不带 session_id 的启动请求会创建会话并返回会话信息。"""
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
