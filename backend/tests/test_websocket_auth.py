import pytest
from starlette.websockets import WebSocketDisconnect


def test_debate_websocket_rejects_missing_token(client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/api/v1/debate/ws/session-1"):
            pass

    assert exc_info.value.code == 1008


def test_debate_websocket_ticket_requires_auth(client):
    response = client.post("/api/v1/debate/ws-ticket/session-1")

    assert response.status_code == 401


def test_debate_websocket_rejects_token_query(client, auth_headers):
    token = auth_headers["Authorization"].removeprefix("Bearer ")

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/debate/ws/session-1?token={token}"):
            pass

    assert exc_info.value.code == 1008


def test_debate_websocket_accepts_one_time_ticket(client, auth_headers):
    ticket_response = client.post("/api/v1/debate/ws-ticket/session-1", headers=auth_headers)
    assert ticket_response.status_code == 200
    ticket = ticket_response.json()["ticket"]

    with client.websocket_connect(f"/api/v1/debate/ws/session-1?ticket={ticket}") as websocket:
        connected = websocket.receive_json()
        assert connected["type"] == "connection"
        assert connected["status"] == "connected"

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/debate/ws/session-1?ticket={ticket}"):
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
