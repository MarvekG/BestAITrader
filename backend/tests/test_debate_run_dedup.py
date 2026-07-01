from __future__ import annotations

from app.crud.session import crud_session
from app.crud.user import create_user
from app.models.async_task import AsyncTask
from app.schemas.session import SessionCreate
from app.schemas.user import UserCreate


def _create_authenticated_user(client, db_session):
    username = "debate_dedup_user"
    password = "password123"
    user = create_user(
        db_session,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password=password,
        ),
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    return user, {"Authorization": f"Bearer {token}"}


def test_run_debate_reuses_running_session_task_when_sync_flag_differs(client, db_session):
    user, headers = _create_authenticated_user(client, db_session)
    analysis_session = crud_session.create(
        db_session,
        obj_in=SessionCreate(
            user_id=user.id,
            stock_code="000001.SZ",
            stock_name="平安银行",
            trading_frequency="中线交易",
            trading_strategy="价值投资",
        ),
    )
    existing_task = AsyncTask(
        task_id="existing-debate-task",
        user_id=user.id,
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
    db_session.add(existing_task)
    db_session.commit()

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
    assert db_session.query(AsyncTask).filter(AsyncTask.task_type == "ai_analysis").count() == 1


def test_run_debate_rejects_same_stock_even_for_different_user(client, db_session):
    owner, _headers = _create_authenticated_user(client, db_session)
    other = create_user(
        db_session,
        UserCreate(
            username="debate_dedup_other_user",
            email="debate_dedup_other_user@example.com",
            password="password123",
        ),
    )
    analysis_session = crud_session.create(
        db_session,
        obj_in=SessionCreate(
            user_id=other.id,
            stock_code="000001.SZ",
            stock_name="平安银行",
            trading_frequency="中线交易",
            trading_strategy="价值投资",
        ),
    )
    existing_task = AsyncTask(
        task_id="existing-stock-task",
        user_id=owner.id,
        task_name="AI Analysis - 000001.SZ",
        task_type="ai_analysis",
        status="running",
        parameters={
            "session_id": "11111111-1111-1111-1111-111111111111",
            "stock_code": "000001.SZ",
        },
    )
    db_session.add(existing_task)
    db_session.commit()
    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": "debate_dedup_other_user", "password": "password123"},
    )
    headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

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
    assert db_session.query(AsyncTask).filter(AsyncTask.task_type == "ai_analysis").count() == 1
