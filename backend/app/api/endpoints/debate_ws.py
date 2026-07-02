"""
辩论WebSocket端点
实现辩论过程的实时推送
"""
import asyncio
import logging
from typing import Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.security import get_current_user
from app.core.websocket_ticket import (
    WEBSOCKET_TICKET_TTL_SECONDS,
    consume_websocket_ticket,
    create_websocket_ticket,
)
from app.models.debate_message import DebateMessage
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket连接管理器"""

    def __init__(self):
        # 存储每个session的活跃连接
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, session_id: str):
        """接受新连接"""
        await websocket.accept()

        if session_id not in self.active_connections:
            self.active_connections[session_id] = []

        self.active_connections[session_id].append(websocket)
        logger.info(
            f"WebSocket connected for session {session_id}, "
            f"total connections: {len(self.active_connections[session_id])}"
        )

    def disconnect(self, websocket: WebSocket, session_id: str):
        """断开连接"""
        if session_id in self.active_connections:
            if websocket in self.active_connections[session_id]:
                self.active_connections[session_id].remove(websocket)
                logger.info(
                    f"WebSocket disconnected for session {session_id}, "
                    f"remaining: {len(self.active_connections[session_id])}"
                )

            # 如果没有连接了,删除session
            if len(self.active_connections[session_id]) == 0:
                del self.active_connections[session_id]

    async def send_message(self, session_id: str, message: dict):
        """发送消息到指定session的所有连接"""
        if session_id in self.active_connections:
            # 发送到所有连接
            disconnected = []
            for connection in self.active_connections[session_id]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    logger.error(f"Failed to send message to websocket: {e}")
                    disconnected.append(connection)

            # 移除断开的连接
            for connection in disconnected:
                self.disconnect(connection, session_id)

    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        for session_id in list(self.active_connections.keys()):
            await self.send_message(session_id, message)


# 创建全局连接管理器
manager = ConnectionManager()


@router.post("/ws-ticket/{session_id}")
async def create_debate_websocket_ticket(
    session_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, int | str]:
    """Create a short-lived ticket for a debate WebSocket connection."""
    return {
        "ticket": create_websocket_ticket(current_user.id, "debate", session_id),
        "expires_in": WEBSOCKET_TICKET_TTL_SECONDS,
    }


async def _authenticate_debate_websocket(
    db: AsyncSession,
    *,
    ticket: str | None,
    session_id: str,
) -> User | None:
    ticket_user_id = consume_websocket_ticket(ticket, "debate", session_id)
    if ticket_user_id is not None:
        result = await db.execute(select(User).where(User.id == ticket_user_id))
        return result.scalar_one_or_none()
    return None


@router.websocket("/ws/{session_id}")
async def debate_websocket(
    websocket: WebSocket,
    session_id: str,
    ticket: str | None = Query(None),
    db: AsyncSession = Depends(get_async_db)
):
    """
    辩论WebSocket端点

    客户端连接后会接收辩论过程中的实时消息
    """
    current_user = await _authenticate_debate_websocket(
        db,
        ticket=ticket,
        session_id=session_id,
    )
    if current_user is None:
        await websocket.close(code=1008)
        return

    await manager.connect(websocket, session_id)

    try:
        # 发送连接成功消息
        await websocket.send_json({
            "type": "connection",
            "status": "connected",
            "session_id": session_id,
            "message": "WebSocket连接成功"
        })

        # 发送历史消息(如果有)
        try:
            session_uuid = UUID(session_id)
            result = await db.execute(
                select(DebateMessage)
                .where(DebateMessage.session_id == session_uuid)
                .order_by(DebateMessage.created_at.asc())
            )
            history_messages = result.scalars().all()

            if history_messages:
                await websocket.send_json({
                    "type": "history",
                    "count": len(history_messages),
                    "messages": [msg.to_dict() for msg in history_messages]
                })

        except Exception as e:
            logger.error(f"Failed to load history: {e}")

        # 保持连接,等待客户端消息或断开
        while True:
            try:
                # 接收客户端消息(主要用于心跳)
                data = await websocket.receive_text()

                # 处理心跳
                if data == "ping":
                    await websocket.send_json({
                        "type": "pong",
                        "timestamp": asyncio.get_event_loop().time()
                    })

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                break

    except Exception as e:
        logger.error(f"WebSocket connection error: {e}")

    finally:
        manager.disconnect(websocket, session_id)
        logger.info(f"WebSocket connection closed for session {session_id}")


async def send_debate_message(session_id: str, message: dict):
    """
    发送辩论消息到WebSocket客户端

    Args:
        session_id: 会话ID
        message: 消息内容
    """
    # 检查是否有活跃连接
    if session_id not in manager.active_connections:
        logger.warning(f"⚠️ No active WebSocket connections for session {session_id}, message will be lost!")
        logger.warning(
            f"   Message type: {message.get('agent_role', 'unknown')}, "
            f"stage: {message.get('stage', 'unknown')}"
        )
        return

    connection_count = len(manager.active_connections[session_id])
    logger.debug(f"📤 Sending debate message to {connection_count} connection(s) for session {session_id}")

    await manager.send_message(session_id, {
        "type": "debate_message",
        "data": message
    })


async def send_debate_status(session_id: str, status: str, stage: str = None):
    """
    发送辩论状态更新

    Args:
        session_id: 会话ID
        status: 状态(started/in_progress/completed/error)
        stage: 当前阶段
    """
    await manager.send_message(session_id, {
        "type": "debate_status",
        "status": status,
        "stage": stage
    })
