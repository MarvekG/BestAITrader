from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.crud.session import crud_session
from app.crud.user import create_user
from app.models.async_task import AsyncTask
from app.schemas.session import SessionCreate
from app.schemas.user import UserCreate


@pytest.mark.asyncio
async def test_run_debate_defaults_to_sync_before_analysis(client, test_db, monkeypatch):
    user_id, headers = await _create_authenticated_user(client, test_db, "debate_default_sync_user")
    analysis_session = await crud_session.create(
        obj_in=SessionCreate(
            user_id=user_id,
            stock_code="000001.SZ",
            stock_name="平安银行",
            trading_frequency="中线交易",
            trading_strategy="价值投资",
        )
    )

    submitted: dict = {}

    async def _fake_submit_task(**kwargs):
        submitted.update(kwargs)
        return {
            "task_id": "default-sync-task",
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
        headers=headers,
        json={
            "session_id": str(analysis_session.session_id),
            "stock_code": "000001.SZ",
            "trading_frequency": "中线交易",
            "trading_strategy": "价值投资",
        },
    )

    assert response.status_code == 201
    assert submitted["parameters"]["sync_before_analysis"] is True
    assert submitted["task_kwargs"]["sync_before_analysis"] is True


async def _create_authenticated_user(client, session_factory, username: str):
    password = "password123"
    async with session_factory() as db:
        user = await create_user(
            db,
            UserCreate(username=username, email=f"{username}@example.com", password=password),
        )
        user_id = user.id
    response = client.post("/api/v1/auth/login", data={"username": username, "password": password})
    token = response.json()["access_token"]
    return user_id, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_run_debate_reuses_running_session_task_when_sync_flag_differs(client, test_db):
    user_id, headers = await _create_authenticated_user(client, test_db, "debate_dedup_user")
    analysis_session = await crud_session.create(
        obj_in=SessionCreate(
            user_id=user_id,
            stock_code="000001.SZ",
            stock_name="平安银行",
            trading_frequency="中线交易",
            trading_strategy="价值投资",
        )
    )
    async with test_db() as db:
        db.add(
            AsyncTask(
                task_id="existing-debate-task",
                user_id=user_id,
                task_name="AI Analysis - 000001.SZ",
                task_type="ai_analysis",
                status="running",
                parameters={
                    "session_id": str(analysis_session.session_id),
                    "stock_code": "000001.SZ",
                    "trading_frequency": "中线交易",
                    "trading_strategy": "价值投资",
                },
            )
        )
        await db.commit()

    response = client.post(
        "/api/v1/debate/run",
        headers=headers,
        json={
            "session_id": str(analysis_session.session_id),
            "stock_code": "000001.SZ",
            "trading_frequency": "中线交易",
            "trading_strategy": "价值投资",
            "sync_before_analysis": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["task_id"] == "existing-debate-task"
    assert response.json()["new_task"] is False
    async with test_db() as db:
        count = (await db.execute(select(func.count()).select_from(AsyncTask).where(AsyncTask.task_type == "ai_analysis"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_run_debate_rejects_same_stock_even_for_different_user(client, test_db):
    owner_id, _headers = await _create_authenticated_user(client, test_db, "debate_dedup_owner_user")
    other_id, headers = await _create_authenticated_user(client, test_db, "debate_dedup_other_user")
    analysis_session = await crud_session.create(
        obj_in=SessionCreate(
            user_id=other_id,
            stock_code="000001.SZ",
            stock_name="平安银行",
            trading_frequency="中线交易",
            trading_strategy="价值投资",
        )
    )
    async with test_db() as db:
        db.add(
            AsyncTask(
                task_id="existing-stock-task",
                user_id=owner_id,
                task_name="AI Analysis - 000001.SZ",
                task_type="ai_analysis",
                status="running",
                parameters={"session_id": "11111111-1111-1111-1111-111111111111", "stock_code": "000001.SZ"},
            )
        )
        await db.commit()

    response = client.post(
        "/api/v1/debate/run",
        headers=headers,
        json={
            "session_id": str(analysis_session.session_id),
            "stock_code": "000001.SZ",
            "trading_frequency": "中线交易",
            "trading_strategy": "价值投资",
        },
    )

    assert response.status_code == 400
    assert "股票 000001.SZ 已有 AI 分析任务正在运行" in response.json()["detail"]
    async with test_db() as db:
        count = (await db.execute(select(func.count()).select_from(AsyncTask).where(AsyncTask.task_type == "ai_analysis"))).scalar_one()
    assert count == 1
