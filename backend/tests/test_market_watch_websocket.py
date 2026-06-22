from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

from app.models.market_watch import MarketWatchEvent
from app.websocket.manager import WebSocketManager


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


def _bearer_token(auth_headers: dict[str, str]) -> str:
    return auth_headers["Authorization"].removeprefix("Bearer ")


@pytest.mark.asyncio
async def test_market_watch_event_push_is_filtered_by_user() -> None:
    manager = WebSocketManager()
    target_socket = _FakeWebSocket()
    other_socket = _FakeWebSocket()
    manager.active_connections = {
        "market-watch:7": {target_socket},
        "market-watch:8": {other_socket},
    }

    await manager.send_market_watch_event(
        {
            "user_id": 7,
            "event_type": "scan",
            "status": "success",
        }
    )

    assert len(target_socket.sent) == 1
    assert other_socket.sent == []
    payload = json.loads(target_socket.sent[0])
    assert payload["type"] == "market_watch_event"
    assert payload["event"]["event_type"] == "scan"


@pytest.mark.asyncio
async def test_source_documents_push_is_filtered_by_user() -> None:
    manager = WebSocketManager()
    target_socket = _FakeWebSocket()
    other_socket = _FakeWebSocket()
    non_market_socket = _FakeWebSocket()
    manager.active_connections = {
        "market-watch:7": {target_socket},
        "market-watch:8": {other_socket},
        "session:abc": {non_market_socket},
    }

    await manager.send_market_watch_documents(
        {
            "user_id": 7,
            "documents": [
                {
                    "id": "news-1",
                    "source_type": "news",
                    "url": "https://example.com/news",
                    "markdown": "# News",
                }
            ],
        }
    )

    assert len(target_socket.sent) == 1
    assert other_socket.sent == []
    assert non_market_socket.sent == []
    payload = json.loads(target_socket.sent[0])
    assert payload["type"] == "market_watch_documents"
    assert payload["documents"][0] == {
        "id": "news-1",
        "source_type": "news",
        "url": "https://example.com/news",
        "markdown": "# News",
    }


@pytest.mark.asyncio
async def test_source_documents_push_ignores_payload_without_user() -> None:
    manager = WebSocketManager()
    target_socket = _FakeWebSocket()
    manager.active_connections = {"market-watch:7": {target_socket}}

    await manager.send_market_watch_documents(
        {
            "documents": [
                {
                    "id": "news-1",
                    "source_type": "news",
                    "url": "https://example.com/news",
                    "markdown": "# News",
                }
            ],
        }
    )

    assert target_socket.sent == []


def test_market_watch_websocket_route_rejects_token_query(client, auth_headers) -> None:
    token = _bearer_token(auth_headers)

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(f"/api/v1/market-watch/ws?token={token}"):
            pass

    assert exc_info.value.code == 1008


def test_market_watch_websocket_ticket_requires_auth(client) -> None:
    response = client.post("/api/v1/market-watch/ws-ticket")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_market_watch_audit_cleanup_once_deletes_old_events(client, auth_headers, db_session) -> None:
    from app.tasks.market_watch_scheduler import run_audit_cleanup

    settings_response = client.get("/api/v1/market-watch/settings", headers=auth_headers)
    user_id = settings_response.json()["user_id"]
    old_event = MarketWatchEvent(
        user_id=user_id,
        event_type="scan",
        status="success",
        created_at=datetime.now() - timedelta(days=91),
    )
    recent_event = MarketWatchEvent(
        user_id=user_id,
        event_type="scan",
        status="success",
        created_at=datetime.now(),
    )
    db_session.add_all([old_event, recent_event])
    db_session.commit()

    cleanup_result = await run_audit_cleanup()

    assert cleanup_result == {"deleted": 1, "retention_days": 30}
    event_ids = {event.event_id for event in db_session.query(MarketWatchEvent).all()}
    assert event_ids == {recent_event.event_id}
