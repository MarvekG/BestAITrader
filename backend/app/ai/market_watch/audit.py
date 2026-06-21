from __future__ import annotations

from datetime import datetime, timedelta
import json
from typing import Any

from app.core import database as database_module
from app.core.redis_client import redis_client
from app.models.market_watch import MarketWatchEvent
from app.models.session import Session as AnalysisSession

MARKET_WATCH_EVENTS_CHANNEL = "market_watch_events"
MARKET_WATCH_DOCUMENTS_CHANNEL = "market_watch_documents"
DEFAULT_EVENT_RETENTION_DAYS = 90
DEFAULT_EVENT_LIMIT = 50
MAX_EVENT_LIMIT = 200


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


def cleanup_old_events(retention_days: int = DEFAULT_EVENT_RETENTION_DAYS) -> int:
    """
    Delete audit events older than the retention window.

    Args:
        retention_days: Number of recent days to retain.

    Returns:
        Number of deleted rows.
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
    Query recent market watch audit events.

    Args:
        user_id: Current user ID.
        limit: Maximum row count.
        event_type: Optional event type filter.
        since: Optional lower bound; defaults to the 90-day retention window.

    Returns:
        Events ordered newest first.
    """
    if limit < 1 or limit > MAX_EVENT_LIMIT:
        raise ValueError("limit must be between 1 and 200")

    lower_bound = since or (datetime.now() - timedelta(days=DEFAULT_EVENT_RETENTION_DAYS))
    with database_module.SessionLocal() as db:
        query = db.query(MarketWatchEvent).filter(
            MarketWatchEvent.user_id == user_id,
            MarketWatchEvent.created_at >= lower_bound,
        )
        if event_type:
            query = query.filter(MarketWatchEvent.event_type == event_type)

        return query.order_by(MarketWatchEvent.created_at.desc()).limit(limit).all()


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
