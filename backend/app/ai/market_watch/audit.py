from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any

from app.core.config import settings
from app.core import database as database_module
from app.core.redis_client import redis_client
from app.models.market_watch import MarketWatchEvent
from app.models.session import Session as AnalysisSession

MARKET_WATCH_EVENTS_CHANNEL = "market_watch_events"
MARKET_WATCH_DOCUMENTS_CHANNEL = "market_watch_documents"
DEFAULT_EVENT_RETENTION_DAYS = 90
DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 200
DEFAULT_DECISION_PAGE_SIZE = 5
MAX_DECISION_PAGE_SIZE = 50


def is_in_cooldown(user_id: int, stock_code: str, cooldown_minutes: int) -> bool:
    """
    Return whether a stock is inside the persisted automatic launch cooldown window.

    Args:
        user_id: Current user ID.
        stock_code: Target stock code.
        cooldown_minutes: Cooldown window in minutes.

    Returns:
        Whether a successful debate launch exists within the cooldown window.
    """
    if cooldown_minutes <= 0:
        return False

    cutoff = datetime.now() - timedelta(minutes=cooldown_minutes)
    with database_module.SessionLocal() as db:
        session_ids = [
            str(session_id)
            for session_id, in (
                db.query(AnalysisSession.session_id)
                .filter(
                    AnalysisSession.user_id == user_id,
                    AnalysisSession.stock_code == stock_code,
                    AnalysisSession.status != "failed",
                    AnalysisSession.created_at >= cutoff,
                )
                .all()
            )
        ]
        if not session_ids:
            return False

        event = (
            db.query(MarketWatchEvent)
            .filter(
                MarketWatchEvent.user_id == user_id,
                MarketWatchEvent.event_type == "debate_launched",
                MarketWatchEvent.status == "success",
                MarketWatchEvent.debate_session_id.in_(session_ids),
                MarketWatchEvent.created_at >= cutoff,
            )
            .order_by(MarketWatchEvent.created_at.desc())
            .first()
        )
    return event is not None


def cleanup_old_events(retention_days: int) -> int:
    """
    删除超过保留窗口的盯盘审计事件。

    Args:
        retention_days: 保留最近多少天的事件。

    Returns:
        删除的事件行数。

    Raises:
        ValueError: 保留天数小于或等于 0。
    """
    if retention_days <= 0:
        raise ValueError("retention_days must be greater than 0")

    cutoff = datetime.now() - timedelta(days=retention_days)
    with database_module.SessionLocal() as db:
        deleted = db.query(MarketWatchEvent).filter(MarketWatchEvent.created_at < cutoff).delete()
        db.commit()
    return int(deleted)


def query_market_watch_events(
    *,
    user_id: int,
    limit: int = DEFAULT_EVENT_LIMIT,
    event_type: str | None = None,
    since: datetime | None = None,
) -> list[MarketWatchEvent]:
    """
    查询用户最近的盯盘审计事件。

    Args:
        user_id: 当前用户 ID。
        limit: 最大返回行数。
        event_type: 可选事件类型过滤条件。
        since: 可选时间下界；为空时使用配置的保留窗口。

    Returns:
        按创建时间倒序排列的事件列表。

    Raises:
        ValueError: 返回数量超出允许范围。
    """
    if limit < 1 or limit > MAX_EVENT_LIMIT:
        raise ValueError("limit must be between 1 and 200")

    lower_bound = since or (datetime.now() - timedelta(days=settings.MARKET_WATCH_EVENT_RETENTION_DAYS))
    with database_module.SessionLocal() as db:
        query = db.query(MarketWatchEvent).filter(
            MarketWatchEvent.user_id == user_id,
            MarketWatchEvent.created_at >= lower_bound,
        )
        if event_type:
            query = query.filter(MarketWatchEvent.event_type == event_type)

        return query.order_by(MarketWatchEvent.created_at.desc()).limit(limit).all()


def query_market_watch_decisions(
    *,
    user_id: int,
    page: int = 1,
    page_size: int = DEFAULT_DECISION_PAGE_SIZE,
) -> tuple[list[MarketWatchEvent], int]:
    """
    分页查询用户的 AI 决策轮次。

    Args:
        user_id: 当前用户 ID。
        page: 页码，从 1 开始。
        page_size: 每页返回的决策轮次数量。

    Returns:
        按时间倒序排列的决策事件列表和符合条件的总轮次数。

    Raises:
        ValueError: 页码或每页数量超出允许范围。
    """
    if page < 1:
        raise ValueError("page must be greater than or equal to 1")
    if page_size < 1 or page_size > MAX_DECISION_PAGE_SIZE:
        raise ValueError("page_size must be between 1 and 50")

    lower_bound = datetime.now() - timedelta(days=settings.MARKET_WATCH_EVENT_RETENTION_DAYS)
    with database_module.SessionLocal() as db:
        query = db.query(MarketWatchEvent).filter(
            MarketWatchEvent.user_id == user_id,
            MarketWatchEvent.event_type == "ai_decision",
            MarketWatchEvent.created_at >= lower_bound,
        )
        total = query.count()
        items = (
            query.order_by(MarketWatchEvent.created_at.desc(), MarketWatchEvent.event_id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
    return items, total


async def publish_market_watch_event(event: MarketWatchEvent) -> int:
    """
    Publish a compact market watch event for WebSocket forwarding.

    Args:
        event: Persisted market watch event.

    Returns:
        Redis subscriber count, or 0 if Redis is unavailable.
    """
    return await publish_market_watch_event_payload(
        {
            "event_id": event.event_id,
            "user_id": event.user_id,
            "event_type": event.event_type,
            "status": event.status,
            "reason": event.reason,
            "watch_ai_decision": event.watch_ai_decision,
            "debate_parameters": event.debate_parameters,
            "debate_session_id": event.debate_session_id,
            "task_id": event.task_id,
            "error_message": event.error_message,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
    )


async def publish_market_watch_event_payload(payload: dict[str, Any]) -> int:
    """
    Publish a compact market watch event payload for WebSocket forwarding.

    Args:
        payload: Already materialized event payload.

    Returns:
        Redis subscriber count, or 0 if Redis is unavailable.
    """
    return await redis_client.publish(MARKET_WATCH_EVENTS_CHANNEL, json.dumps(payload, ensure_ascii=False))


async def publish_market_watch_documents_payload(payload: dict[str, Any]) -> int:
    """
    Publish freshly rendered market-watch source documents for WebSocket forwarding.

    Args:
        payload: Source document payload scoped to one user.

    Returns:
        Redis subscriber count, or 0 if Redis is unavailable.
    """
    return await redis_client.publish(MARKET_WATCH_DOCUMENTS_CHANNEL, json.dumps(payload, ensure_ascii=False))
