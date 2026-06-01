import json

import pytest
from starlette.websockets import WebSocketDisconnect

from app.websocket.manager import WebSocketManager


class _FakeWebSocket:
    def __init__(self, *, should_disconnect: bool = False):
        self.should_disconnect = should_disconnect
        self.messages = []

    async def send_text(self, payload: str):
        if self.should_disconnect:
            raise WebSocketDisconnect(code=1006)
        self.messages.append(payload)


@pytest.mark.asyncio
async def test_broadcast_prunes_disconnected_socket_and_keeps_live_socket():
    manager = WebSocketManager()
    dead_socket = _FakeWebSocket(should_disconnect=True)
    live_socket = _FakeWebSocket()

    manager.active_connections = {
        "stale-session": {dead_socket},
        "live-session": {live_socket},
    }

    await manager.broadcast({"type": "heartbeat"})

    assert "stale-session" not in manager.active_connections
    assert len(live_socket.messages) == 1
    assert json.loads(live_socket.messages[0])["type"] == "heartbeat"


def test_disconnect_keeps_subscriptions_until_last_socket_leaves():
    manager = WebSocketManager()
    first_socket = _FakeWebSocket()
    second_socket = _FakeWebSocket()

    manager.active_connections = {
        "shared-session": {first_socket, second_socket},
    }
    manager.subscriptions = {
        "shared-session": {"task_completed": {"*"}},
    }

    manager.disconnect(first_socket, "shared-session")

    assert manager.active_connections["shared-session"] == {second_socket}
    assert "shared-session" in manager.subscriptions

    manager.disconnect(second_socket, "shared-session")

    assert "shared-session" not in manager.active_connections
    assert "shared-session" not in manager.subscriptions


def test_disconnect_is_idempotent_for_already_pruned_socket():
    manager = WebSocketManager()
    socket = _FakeWebSocket()

    manager.active_connections = {"session": {socket}}

    manager.disconnect(socket, "session")
    manager.disconnect(socket, "session")

    assert "session" not in manager.active_connections
