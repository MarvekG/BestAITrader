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


def test_pubsub_redis_options_disable_read_timeout():
    options = WebSocketManager._redis_pubsub_options()

    assert options["socket_timeout"] is None
    assert options["socket_connect_timeout"] == 5
    assert options["health_check_interval"] == 30


@pytest.mark.asyncio
async def test_trading_notification_methods_swallow_broadcast_errors(monkeypatch):
    manager = WebSocketManager()

    async def _raise_broadcast_error(*_args, **_kwargs):
        raise RuntimeError("broadcast unavailable")

    monkeypatch.setattr(manager, "broadcast_to_session", _raise_broadcast_error)

    await manager.send_order_status("session", {"order_id": "order-1"})
    await manager.send_position_update("session", {"stock_code": "000001.SZ"})
    await manager.send_trade_executed("session", {"trade_id": "trade-1"})


@pytest.mark.asyncio
async def test_interactive_stock_picker_update_is_scoped_to_owner_session(monkeypatch):
    """交互式选股更新只推送给 run 所属用户的订阅会话。

    Args:
        monkeypatch: pytest monkeypatch fixture。
    """
    manager = WebSocketManager()
    manager.subscriptions = {
        "owner-session": {"interactive_stock_picker": {"*"}},
        "other-session": {"interactive_stock_picker": {"*"}},
    }
    manager.session_user_ids = {
        "owner-session": 1,
        "other-session": 2,
    }
    sent_sessions = []

    async def fake_broadcast_to_session(_message, session_id):
        """记录收到推送的 session。

        Args:
            _message: 推送消息。
            session_id: 目标 WebSocket session。
        """
        sent_sessions.append(session_id)

    monkeypatch.setattr(manager, "broadcast_to_session", fake_broadcast_to_session)

    await manager.send_interactive_stock_picker_update(
        run_id="run-1",
        stage="planning",
        status="awaiting_plan_approval",
        message="plan ready",
        user_id=1,
        payload={"run": {"user_id": 1}},
    )

    assert sent_sessions == ["owner-session"]
