import json
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.logger import get_logger
from app.core.security import get_current_user
from app.core.websocket_ticket import (
    WEBSOCKET_TICKET_TTL_SECONDS,
    consume_websocket_ticket,
    create_websocket_ticket,
)
from app.models.session import Session as AnalysisSession
from app.models.user import User
from app.websocket.manager import ws_manager

# 获取日志记录器
logger = get_logger(__name__)

router = APIRouter()


@router.post("/ws-ticket/{session_id}")
async def create_global_websocket_ticket(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int | str]:
    """Create a short-lived ticket for the global WebSocket endpoint."""
    session_uuid = _parse_session_uuid(session_id)
    if session_uuid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    if not await _user_owns_session(db, session_id=session_uuid, user_id=current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return {
        "ticket": create_websocket_ticket(current_user.id, "global", str(session_uuid)),
        "expires_in": WEBSOCKET_TICKET_TTL_SECONDS,
    }


def _parse_session_uuid(session_id: str) -> UUID | None:
    try:
        return UUID(session_id)
    except ValueError:
        return None


async def _user_owns_session(db: AsyncSession, *, session_id: UUID, user_id: int) -> bool:
    result = await db.execute(
        select(AnalysisSession.session_id).where(
            AnalysisSession.session_id == session_id,
            AnalysisSession.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _authenticate_websocket(
    db: AsyncSession,
    ticket: str | None,
    scope: str,
    resource_id: str | None,
) -> User | None:
    if scope == "global":
        session_uuid = _parse_session_uuid(resource_id or "")
        if session_uuid is None:
            return None
        resource_id = str(session_uuid)

    ticket_user_id = consume_websocket_ticket(ticket, scope, resource_id)
    if ticket_user_id is not None:
        if scope == "global" and not await _user_owns_session(
            db,
            session_id=UUID(resource_id),
            user_id=ticket_user_id,
        ):
            return None
        result = await db.execute(select(User).where(User.id == ticket_user_id))
        return result.scalar_one_or_none()
    return None


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    ticket: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db),
):
    session_id_str = session_id
    current_user = await _authenticate_websocket(
        db,
        ticket=ticket,
        scope="global",
        resource_id=session_id_str,
    )
    if current_user is None:
        await websocket.close(code=1008)
        return

    await ws_manager.connect(websocket, session_id_str, current_user.id)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                msg_type = message.get("type")

                if msg_type == "subscribe":
                    event_type = message.get("event_type")
                    resource_id = message.get("resource_id", "*")
                    if event_type:
                        ws_manager.subscribe(session_id_str, event_type, resource_id)
                        await ws_manager.send_personal_message({
                            "type": "subscribed",
                            "event_type": event_type,
                            "resource_id": resource_id
                        }, websocket)

                elif msg_type == "unsubscribe":
                    event_type = message.get("event_type")
                    resource_id = message.get("resource_id", "*")
                    if event_type:
                        ws_manager.unsubscribe(session_id_str, event_type, resource_id)
                        await ws_manager.send_personal_message({
                            "type": "unsubscribed",
                            "event_type": event_type,
                            "resource_id": resource_id
                        }, websocket)

                # 兼容旧的价格订阅方式
                elif msg_type == "subscribe_price":
                    stock_code = message.get("stock_code")
                    if stock_code:
                        ws_manager.subscribe(session_id_str, "price", stock_code)
                        await ws_manager.send_personal_message({
                            "type": "subscribed",
                            "event_type": "price",
                            "stock_code": stock_code
                        }, websocket)

                elif msg_type == "unsubscribe_price":
                    stock_code = message.get("stock_code")
                    if stock_code:
                        ws_manager.unsubscribe(session_id_str, "price", stock_code)
                        await ws_manager.send_personal_message({
                            "type": "unsubscribed",
                            "event_type": "price",
                            "stock_code": stock_code
                        }, websocket)

                elif msg_type == "ping":
                    await ws_manager.send_personal_message({
                        "type": "pong",
                        "timestamp": message.get("timestamp")
                    }, websocket)

                else:
                    await ws_manager.broadcast_to_session({
                        "type": "message",
                        "content": f"Received: {data}",
                        "timestamp": message.get("timestamp")
                    }, session_id_str)

            except json.JSONDecodeError:
                await ws_manager.broadcast_to_session({
                    "type": "error",
                    "message": "Invalid JSON format"
                }, session_id_str)

    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, session_id_str)
    except Exception as e:
        ws_manager.disconnect(websocket, session_id_str)
        logger.error(f"WebSocket error: {e}")


@router.get("/ws/status/{session_id}")
async def get_ws_status(
    session_id: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    session_uuid = _parse_session_uuid(session_id)
    if session_uuid is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    if not await _user_owns_session(db, session_id=session_uuid, user_id=current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    session_id = str(session_uuid)
    # 获取当前会话的订阅信息
    subscriptions = ws_manager.subscriptions.get(session_id, {})

    # 转换为前端友好的格式
    formatted_subscriptions = {}
    for event_type, resource_ids in subscriptions.items():
        formatted_subscriptions[event_type] = list(resource_ids)

    return {
        "session_id": session_id,
        "connected": session_id in ws_manager.active_connections,
        "subscriptions": formatted_subscriptions,
        "total_connections": sum(len(conns) for conns in ws_manager.active_connections.values())
    }
