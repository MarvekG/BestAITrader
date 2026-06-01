from datetime import datetime, timedelta
import json

import pytest
from unittest.mock import AsyncMock

from app.ai.market_watch import audit
from app.ai.market_watch.audit import (
    MARKET_WATCH_EVENTS_CHANNEL,
    cleanup_old_events,
    is_in_cooldown,
    publish_market_watch_event,
    query_market_watch_events,
)
from app.models.market_watch import MarketWatchEvent
from app.models.session import Session as AnalysisSession
from app.models.user import User


def _create_user(db, user_id: int = 1) -> User:
    user = User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.com",
        password_hash="hash",
    )
    db.add(user)
    db.commit()
    return user


def _add_event(
    db,
    *,
    user_id: int,
    event_type: str = "scan",
    status: str = "success",
    debate_session_id: str | None = None,
    watch_ai_decision: dict | list[dict] | None = None,
    debate_parameters: dict | None = None,
    created_at: datetime | None = None,
) -> MarketWatchEvent:
    event = MarketWatchEvent(
        user_id=user_id,
        event_type=event_type,
        status=status,
        debate_session_id=debate_session_id,
        watch_ai_decision=watch_ai_decision,
        debate_parameters=debate_parameters,
        created_at=created_at or datetime.now(),
    )
    db.add(event)
    db.commit()
    return event


def _add_debate_session(
    db,
    *,
    user_id: int,
    stock_code: str,
    created_at: datetime | None = None,
) -> AnalysisSession:
    session = AnalysisSession(
        user_id=user_id,
        stock_code=stock_code,
        trading_frequency="日内交易 (Day Trading)",
        trading_strategy="趋势追踪 (Trend Following)",
        created_at=created_at or datetime.now(),
        updated_at=created_at or datetime.now(),
    )
    db.add(session)
    db.commit()
    return session


def test_is_in_cooldown_reads_successful_launch_events(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    _create_user(db)
    launch_session = _add_debate_session(
        db,
        user_id=1,
        stock_code="600519",
        created_at=datetime.now() - timedelta(minutes=10),
    )
    _add_event(
        db,
        user_id=1,
        event_type="debate_launched",
        status="success",
        debate_session_id=str(launch_session.session_id),
        created_at=datetime.now() - timedelta(minutes=10),
    )

    assert is_in_cooldown(user_id=1, stock_code="600519", cooldown_minutes=60) is True


def test_is_in_cooldown_ignores_failed_or_old_launches(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    _create_user(db)
    failed_session = _add_debate_session(
        db,
        user_id=1,
        stock_code="600519",
        created_at=datetime.now() - timedelta(minutes=10),
    )
    old_session = _add_debate_session(
        db,
        user_id=1,
        stock_code="600519",
        created_at=datetime.now() - timedelta(minutes=90),
    )
    _add_event(
        db,
        user_id=1,
        event_type="debate_launched",
        status="failed",
        debate_session_id=str(failed_session.session_id),
        created_at=datetime.now() - timedelta(minutes=10),
    )
    _add_event(
        db,
        user_id=1,
        event_type="debate_launched",
        status="success",
        debate_session_id=str(old_session.session_id),
        created_at=datetime.now() - timedelta(minutes=90),
    )

    assert is_in_cooldown(user_id=1, stock_code="600519", cooldown_minutes=60) is False


def test_cleanup_old_events_deletes_records_older_than_90_days(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    _create_user(db)
    _add_event(db, user_id=1, created_at=datetime.now() - timedelta(days=91))
    recent = _add_event(db, user_id=1, created_at=datetime.now() - timedelta(days=10))

    assert cleanup_old_events(retention_days=90) == 1
    assert db.query(MarketWatchEvent).count() == 1
    assert db.query(MarketWatchEvent).first().event_id == recent.event_id


def test_query_market_watch_events_defaults_to_recent_90_days_and_limit(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    _create_user(db)
    _add_event(db, user_id=1, event_type="scan", created_at=datetime.now() - timedelta(days=91))
    _add_event(db, user_id=1, event_type="scan", created_at=datetime.now() - timedelta(days=2))
    _add_event(
        db,
        user_id=1,
        event_type="debate_skipped",
        status="skipped",
        created_at=datetime.now() - timedelta(days=1),
    )

    events = query_market_watch_events(user_id=1, limit=1)

    assert len(events) == 1
    assert events[0].event_type == "debate_skipped"


def test_query_market_watch_events_filters_type(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    _create_user(db)
    _add_event(db, user_id=1, event_type="scan")
    matched = _add_event(db, user_id=1, event_type="debate_skipped", status="skipped")

    events = query_market_watch_events(user_id=1, event_type="debate_skipped")

    assert [event.event_id for event in events] == [matched.event_id]


def test_query_market_watch_events_rejects_invalid_limit(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    _create_user(db)

    with pytest.raises(ValueError, match="limit must be between 1 and 200"):
        query_market_watch_events(user_id=1, limit=201)


@pytest.mark.asyncio
async def test_publish_market_watch_event_pushes_latest_ai_decision(test_db, monkeypatch) -> None:
    session_factory = test_db
    db = session_factory()
    _create_user(db)
    event = _add_event(
        db,
        user_id=1,
        event_type="debate_skipped",
        status="skipped",
        watch_ai_decision={
            "stock_code": "600519",
            "action": "monitor",
            "confidence": 0.62,
        },
        debate_parameters={"trading_frequency": "day", "trading_strategy": "trend"},
    )
    publish = AsyncMock(return_value=1)
    monkeypatch.setattr(audit.redis_client, "publish", publish)

    assert await publish_market_watch_event(event) == 1

    publish.assert_awaited_once()
    channel, payload = publish.await_args.args
    assert channel == MARKET_WATCH_EVENTS_CHANNEL
    assert '"event_type": "debate_skipped"' in payload
    assert '"user_id": 1' in payload
    decoded = json.loads(payload)
    assert decoded["watch_ai_decision"]["stock_code"] == "600519"
    assert decoded["watch_ai_decision"]["action"] == "monitor"
    assert decoded["debate_parameters"]["trading_frequency"] == "day"
    assert "target_stock_code" not in decoded
    assert "target_stock_name" not in decoded
    assert "summary" not in decoded
