from datetime import datetime, timedelta
import secrets


WEBSOCKET_TICKET_TTL_SECONDS = 30
_websocket_tickets: dict[str, dict[str, object]] = {}


def create_websocket_ticket(user_id: int, scope: str, resource_id: str | None = None) -> str:
    """Create a short-lived one-time ticket for a WebSocket connection."""
    _remove_expired_tickets()
    ticket = secrets.token_urlsafe(32)
    _websocket_tickets[ticket] = {
        "user_id": user_id,
        "scope": scope,
        "resource_id": resource_id,
        "expires_at": datetime.now() + timedelta(seconds=WEBSOCKET_TICKET_TTL_SECONDS),
    }
    return ticket


def consume_websocket_ticket(ticket: str | None, scope: str, resource_id: str | None = None) -> int | None:
    """Consume a WebSocket ticket and return its user id when it is valid."""
    if not ticket:
        return None

    _remove_expired_tickets()
    payload = _websocket_tickets.pop(ticket, None)
    if not payload:
        return None

    expires_at = payload.get("expires_at")
    if not isinstance(expires_at, datetime) or expires_at <= datetime.now():
        return None
    if payload.get("scope") != scope:
        return None
    if payload.get("resource_id") != resource_id:
        return None

    user_id = payload.get("user_id")
    return user_id if isinstance(user_id, int) else None


def _remove_expired_tickets() -> None:
    now = datetime.now()
    expired_tickets = [
        ticket
        for ticket, payload in _websocket_tickets.items()
        if not isinstance(payload.get("expires_at"), datetime) or payload["expires_at"] <= now
    ]
    for ticket in expired_tickets:
        _websocket_tickets.pop(ticket, None)
