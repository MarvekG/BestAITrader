import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.core.websocket_ticket import create_websocket_ticket
from app.crud.user import get_password_hash
from app.models.debate_message import DebateMessage
from app.models.session import Session as DebateSession
from app.models.user import User


def _login(client, username: str, password: str = "password123") -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _create_user(test_db, username: str, password: str = "password123") -> User:
    async with test_db() as db:
        user = User(
            username=username,
            email=f"{username}@example.com",
            password_hash=get_password_hash(password),
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def _create_debate_session(test_db, user_id: int) -> DebateSession:
    async with test_db() as db:
        session = DebateSession(
            user_id=user_id,
            stock_code="000001.SZ",
            trading_frequency="swing",
            trading_strategy="trend",
            status="completed",
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session


async def _create_debate_message(test_db, session_id):
    async with test_db() as db:
        db.add(
            DebateMessage(
                session_id=session_id,
                stage="portfolio_manager",
                round_number=1,
                agent_name="PM",
                agent_role="portfolio_manager",
                decision="buy",
                reasoning="owner history",
            )
        )
        await db.commit()


def test_debate_websocket_rejects_missing_token(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/debate/ws/session-1"):
            pass

    assert exc_info.value.code == 1008


def test_debate_websocket_ticket_requires_auth(client):
    response = client.post("/api/v1/debate/ws-ticket/session-1")

    assert response.status_code == 401


def test_debate_websocket_ticket_rejects_invalid_session_uuid(client, auth_headers):
    response = client.post("/api/v1/debate/ws-ticket/not-a-uuid", headers=auth_headers)

    assert response.status_code == 404


def test_debate_websocket_rejects_token_query(client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/debate/ws/session-1?token={token}"):
            pass

    assert exc_info.value.code == 1008


def test_debate_websocket_accepts_one_time_ticket(client, test_db, run_async):
    username = f"ws_owner_{uuid.uuid4().hex[:8]}"
    user = run_async(_create_user(test_db, username))
    session = run_async(_create_debate_session(test_db, user.id))
    run_async(_create_debate_message(test_db, session.session_id))
    headers = _login(client, username)

    ticket_response = client.post(f"/api/v1/debate/ws-ticket/{session.session_id}", headers=headers)
    assert ticket_response.status_code == 200
    ticket = ticket_response.json()["ticket"]

    with client.websocket_connect(f"/api/v1/debate/ws/{session.session_id}?ticket={ticket}") as websocket:
        connected = websocket.receive_json()
        assert connected["type"] == "connection"
        assert connected["status"] == "connected"
        history = websocket.receive_json()
        assert history["type"] == "history"
        assert history["count"] == 1
        assert history["messages"][0]["reasoning"] == "owner history"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/debate/ws/{session.session_id}?ticket={ticket}"):
            pass

    assert exc_info.value.code == 1008


def test_debate_websocket_ticket_rejects_cross_user_session(client, test_db, run_async):
    owner = run_async(_create_user(test_db, f"ws_owner_{uuid.uuid4().hex[:8]}"))
    other_username = f"ws_other_{uuid.uuid4().hex[:8]}"
    other = run_async(_create_user(test_db, other_username))
    session = run_async(_create_debate_session(test_db, owner.id))
    headers = _login(client, other.username)

    response = client.post(f"/api/v1/debate/ws-ticket/{session.session_id}", headers=headers)

    assert other.id != owner.id
    assert response.status_code == 404


def test_debate_websocket_rejects_cross_user_ticket_before_history(client, test_db, run_async):
    owner = run_async(_create_user(test_db, f"ws_owner_{uuid.uuid4().hex[:8]}"))
    other = run_async(_create_user(test_db, f"ws_other_{uuid.uuid4().hex[:8]}"))
    session = run_async(_create_debate_session(test_db, owner.id))
    run_async(_create_debate_message(test_db, session.session_id))
    ticket = create_websocket_ticket(other.id, "debate", str(session.session_id))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/debate/ws/{session.session_id}?ticket={ticket}"):
            pass

    assert exc_info.value.code == 1008


def test_global_websocket_rejects_missing_token(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/session-1"):
            pass

    assert exc_info.value.code == 1008


def test_global_websocket_ticket_requires_auth(client):
    response = client.post("/ws-ticket/session-1")

    assert response.status_code == 401


def test_global_websocket_rejects_token_query(client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/session-1?token={token}"):
            pass

    assert exc_info.value.code == 1008


def test_global_websocket_ticket_rejects_cross_user_session(client, test_db, run_async):
    owner = run_async(_create_user(test_db, f"ws_owner_{uuid.uuid4().hex[:8]}"))
    other = run_async(_create_user(test_db, f"ws_other_{uuid.uuid4().hex[:8]}"))
    session = run_async(_create_debate_session(test_db, owner.id))
    headers = _login(client, other.username)

    response = client.post(f"/ws-ticket/{session.session_id}", headers=headers)

    assert other.id != owner.id
    assert response.status_code == 404


def test_global_websocket_rejects_cross_user_ticket(client, test_db, run_async):
    owner = run_async(_create_user(test_db, f"ws_owner_{uuid.uuid4().hex[:8]}"))
    other = run_async(_create_user(test_db, f"ws_other_{uuid.uuid4().hex[:8]}"))
    session = run_async(_create_debate_session(test_db, owner.id))
    ticket = create_websocket_ticket(other.id, "global", str(session.session_id))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/ws/{session.session_id}?ticket={ticket}"):
            pass

    assert exc_info.value.code == 1008


def test_global_websocket_status_rejects_cross_user_session(client, test_db, run_async):
    owner = run_async(_create_user(test_db, f"ws_owner_{uuid.uuid4().hex[:8]}"))
    other = run_async(_create_user(test_db, f"ws_other_{uuid.uuid4().hex[:8]}"))
    session = run_async(_create_debate_session(test_db, owner.id))
    headers = _login(client, other.username)

    response = client.get(f"/ws/status/{session.session_id}", headers=headers)

    assert other.id != owner.id
    assert response.status_code == 404
