from unittest.mock import AsyncMock, patch
from uuid import UUID

from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER
from app.models.async_task import AsyncTask
from app.models.data_storage import StockBasic
from app.models.debate_message import DebateMessage
from app.models.session import Session as SessionModel


def _seed_stock_basic(db_session, stock_code: str, name: str) -> None:
    db_session.add(
        StockBasic(
            stock_code=stock_code,
            name=name,
            industry="Banking",
            market="SZSE",
            data_source="test",
        )
    )
    db_session.commit()


def _create_session(client, auth_headers, stock_code: str) -> str:
    response = client.post(
        "/api/v1/sessions/",
        json={
            "stock_code": stock_code,
            "trading_frequency": "swing",
            "trading_strategy": "trend_following",
        },
        headers=auth_headers,
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def test_create_session_returns_source(client, auth_headers, db_session):
    stock_code = "000063.SZ"
    _seed_stock_basic(db_session, stock_code, "ZTE")

    response = client.post(
        "/api/v1/sessions/",
        json={
            "stock_code": stock_code,
            "trading_frequency": "swing",
            "trading_strategy": "trend_following",
            "source": "manual",
        },
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.json()["source"] == "manual"


def _build_run_payload(session_id: str, stock_code: str) -> dict:
    return {
        "session_id": session_id,
        "stock_code": stock_code,
        "trading_frequency": "swing",
        "trading_strategy": "trend_following",
    }


def test_smoke_debate_run_flow(client, auth_headers, db_session, sqlite_session_factory):
    stock_code = "000001.SZ"
    _seed_stock_basic(db_session, stock_code, "Ping An Bank")
    session_id = _create_session(client, auth_headers, stock_code)

    async def _fake_run_analysis_task(task_id, stock_code, trading_frequency, trading_strategy, session_id=None):
        db = sqlite_session_factory()
        try:
            db.add(
                DebateMessage(
                    session_id=UUID(session_id),
                    stage="final",
                    round_number=1,
                    agent_name="Portfolio Manager",
                    agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
                    decision="hold",
                    reasoning="Mocked portfolio manager conclusion",
                )
            )
            db.query(SessionModel).filter(SessionModel.session_id == UUID(session_id)).update({"status": "completed"})
            db.query(AsyncTask).filter(AsyncTask.task_id == task_id).update({"status": "completed"})
            db.commit()
        finally:
            db.close()

    with patch(
        "app.api.endpoints.debate.run_analysis_task",
        new=_fake_run_analysis_task,
    ), patch(
        "app.api.endpoints.debate.send_debate_status",
        new=AsyncMock(return_value=None),
    ):
        run_response = client.post(
            "/api/v1/debate/run",
            json=_build_run_payload(session_id, stock_code),
            headers=auth_headers,
        )

    assert run_response.status_code == 201
    payload = run_response.json()
    assert payload["status"] == "started"
    assert "task_id" in payload

    history_response = client.get(f"/api/v1/debate/history/{session_id}", headers=auth_headers)
    assert history_response.status_code == 200
    history = history_response.json()
    assert len(history) == 1
    assert history[0]["agent_role"] == AGENT_ROLE_PORTFOLIO_MANAGER


def test_run_debate_reuses_existing_task_without_scheduling_duplicate_work(client, auth_headers, db_session):
    stock_code = "000002.SZ"
    _seed_stock_basic(db_session, stock_code, "Vanke A")
    session_id = _create_session(client, auth_headers, stock_code)
    parameters = {
        "session_id": session_id,
        "stock_code": stock_code,
        "trading_frequency": "swing",
        "trading_strategy": "trend_following",
    }
    existing_task = AsyncTask(
        task_name=f"AI Analysis - {stock_code}",
        task_type="ai_analysis",
        status="running",
        allow_concurrent=False,
        parameters=parameters,
    )
    db_session.add(existing_task)
    db_session.commit()

    mock_run_analysis_task = AsyncMock(return_value=None)
    mock_send_status = AsyncMock(return_value=None)
    with patch("app.api.endpoints.debate.run_analysis_task", new=mock_run_analysis_task), patch(
        "app.api.endpoints.debate.send_debate_status",
        new=mock_send_status,
    ):
        response = client.post(
            "/api/v1/debate/run",
            json=_build_run_payload(session_id, stock_code),
            headers=auth_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == existing_task.task_id
    assert payload["status"] == "running"
    assert payload["new_task"] is False
    assert mock_run_analysis_task.await_count == 0
    assert mock_send_status.await_count == 0


def test_run_debate_blocks_same_stock_for_different_sessions(client, auth_headers, db_session):
    stock_code = "000333.SZ"
    _seed_stock_basic(db_session, stock_code, "Midea Group")
    first_session_id = _create_session(client, auth_headers, stock_code)
    second_session_id = _create_session(client, auth_headers, stock_code)
    db_session.add(
        AsyncTask(
            task_name=f"AI Analysis - {stock_code}",
            task_type="ai_analysis",
            status="running",
            allow_concurrent=False,
            parameters={
                "session_id": first_session_id,
                "stock_code": stock_code,
                "trading_frequency": "swing",
                "trading_strategy": "trend_following",
            },
        )
    )
    db_session.commit()

    mock_run_analysis_task = AsyncMock(return_value=None)
    mock_send_status = AsyncMock(return_value=None)
    with patch("app.api.endpoints.debate.run_analysis_task", new=mock_run_analysis_task), patch(
        "app.api.endpoints.debate.send_debate_status",
        new=mock_send_status,
    ):
        response = client.post(
            "/api/v1/debate/run",
            json=_build_run_payload(second_session_id, stock_code),
            headers=auth_headers,
        )

    assert response.status_code == 400
    payload = response.json()
    assert "already running" in payload["detail"]
    assert mock_run_analysis_task.await_count == 0
    assert mock_send_status.await_count == 0


def test_get_history_empty(client, auth_headers, db_session):
    stock_code = "600519.SH"
    _seed_stock_basic(db_session, stock_code, "Kweichow Moutai")
    session_id = _create_session(client, auth_headers, stock_code)

    response = client.get(f"/api/v1/debate/history/{session_id}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == []
