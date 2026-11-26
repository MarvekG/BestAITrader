from uuid import UUID

import pytest
from sqlalchemy import select

from app.models.async_task import AsyncTask
from app.models.session import Session
from app.models.user import User


@pytest.mark.asyncio
async def test_session_list_exposes_active_task_status(client, auth_headers, async_db_session) -> None:
    """会话列表返回排队中或运行中的 AI 分析任务状态。"""
    user = (await async_db_session.execute(select(User))).scalars().first()

    queued_session = Session(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="daily",
        trading_strategy="value",
        status="active",
    )
    running_session = Session(
        user_id=user.id,
        stock_code="000002.SZ",
        trading_frequency="daily",
        trading_strategy="value",
        status="active",
    )
    idle_session = Session(
        user_id=user.id,
        stock_code="000003.SZ",
        trading_frequency="daily",
        trading_strategy="value",
        status="active",
    )
    async_db_session.add_all([queued_session, running_session, idle_session])
    await async_db_session.commit()
    for session in (queued_session, running_session, idle_session):
        await async_db_session.refresh(session)

    async_db_session.add_all([
        AsyncTask(
            task_name="AI Analysis - 000001.SZ",
            task_type="ai_analysis",
            status="pending",
            parameters={
                "session_id": str(queued_session.session_id),
                "stock_code": "000001.SZ",
            },
            user_id=user.id,
        ),
        AsyncTask(
            task_name="AI Analysis - 000002.SZ",
            task_type="ai_analysis",
            status="running",
            parameters={
                "session_id": str(running_session.session_id),
                "stock_code": "000002.SZ",
            },
            user_id=user.id,
        ),
    ])
    await async_db_session.commit()

    response = client.get("/api/v1/sessions/?paginated=true", headers=auth_headers)

    assert response.status_code == 200
    items = {item["session_id"]: item for item in response.json()["items"]}
    assert items[str(queued_session.session_id)]["task_status"] == "pending"
    assert items[str(running_session.session_id)]["task_status"] == "running"
    assert items[str(idle_session.session_id)]["task_status"] is None


@pytest.mark.asyncio
async def test_session_detail_exposes_active_task_status(client, auth_headers, async_db_session) -> None:
    """会话详情返回当前排队中的 AI 分析任务状态。"""
    user = (await async_db_session.execute(select(User))).scalars().first()
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

    async_db_session.add(
        AsyncTask(
            task_name="AI Analysis - 000001.SZ",
            task_type="ai_analysis",
            status="pending",
            parameters={
                "session_id": str(session.session_id),
                "stock_code": "000001.SZ",
            },
            user_id=user.id,
        )
    )
    await async_db_session.commit()

    response = client.get(f"/api/v1/sessions/{session.session_id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["task_status"] == "pending"
    assert UUID(response.json()["session_id"]) == session.session_id
