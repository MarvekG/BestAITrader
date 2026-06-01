from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect

from app.core import database as database_module
from app.core.security import get_current_user
from app.core.websocket_ticket import (
    WEBSOCKET_TICKET_TTL_SECONDS,
    consume_websocket_ticket,
    create_websocket_ticket,
)
from app.ai.market_watch.audit import (
    MARKET_WATCH_DOCUMENTS_CHANNEL,
    MARKET_WATCH_EVENTS_CHANNEL,
    query_market_watch_events,
)
from app.ai.market_watch.schemas import (
    MarketWatchEventSchema,
    MarketWatchSettingsResponse,
    MarketWatchSettingsUpdate,
)
from app.ai.market_watch.service import scan_market_watch
from app.ai.market_watch.settings import get_market_watch_settings, upsert_market_watch_settings
from app.models.user import User
from app.tasks.async_scheduler import async_task_scheduler
from app.websocket.manager import ws_manager

router = APIRouter()


@router.get("/settings", response_model=MarketWatchSettingsResponse)
def read_market_watch_settings(
    current_user: User = Depends(get_current_user),
) -> MarketWatchSettingsResponse:
    """Return current user's market watch settings."""
    return get_market_watch_settings(current_user.id)


@router.put("/settings", response_model=MarketWatchSettingsResponse)
def update_market_watch_settings(
    payload: MarketWatchSettingsUpdate,
    current_user: User = Depends(get_current_user),
) -> MarketWatchSettingsResponse:
    """Persist current user's market watch settings update."""
    try:
        updated = upsert_market_watch_settings(current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async_task_scheduler.refresh_schedule()
    return updated


@router.post("/scan")
async def scan_market_watch_once(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Run one market watch scan for current user."""
    return await scan_market_watch(
        current_user.id,
        background_tasks=background_tasks,
    )


@router.get("/events", response_model=list[MarketWatchEventSchema])
def read_market_watch_events(
    limit: int = Query(50, ge=1, le=200),
    event_type: str | None = None,
    since: datetime | None = None,
    current_user: User = Depends(get_current_user),
) -> list[MarketWatchEventSchema]:
    """Return current user's recent market watch audit events."""
    return query_market_watch_events(
        user_id=current_user.id,
        limit=limit,
        event_type=event_type,
        since=since,
    )


@router.post("/ws-ticket")
def create_market_watch_websocket_ticket(
    current_user: User = Depends(get_current_user),
) -> dict[str, int | str]:
    """Create a short-lived ticket for the market watch WebSocket connection."""
    return {
        "ticket": create_websocket_ticket(current_user.id, "market_watch"),
        "expires_in": WEBSOCKET_TICKET_TTL_SECONDS,
    }


def _authenticate_market_watch_ws(ticket: str | None) -> User | None:
    ticket_user_id = consume_websocket_ticket(ticket, "market_watch")
    with database_module.SessionLocal() as db:
        if ticket_user_id is not None:
            return db.query(User).filter(User.id == ticket_user_id).first()
        return None


@router.websocket("/ws")
async def market_watch_websocket(
    websocket: WebSocket,
    ticket: str | None = Query(None),
) -> None:
    """Push market watch events to the authenticated user."""
    user = _authenticate_market_watch_ws(ticket)
    if user is None:
        await websocket.close(code=1008)
        return

    connection_id = await ws_manager.connect_market_watch(websocket, user.id)
    try:
        await websocket.send_json(
            {
                "type": "connection",
                "status": "connected",
                "channel": MARKET_WATCH_EVENTS_CHANNEL,
                "documents_channel": MARKET_WATCH_DOCUMENTS_CHANNEL,
            }
        )

        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json(
                    {
                        "type": "pong",
                        "timestamp": datetime.now().isoformat(),
                    }
                )
    except WebSocketDisconnect:
        pass
    finally:
        ws_manager.disconnect(websocket, connection_id)
