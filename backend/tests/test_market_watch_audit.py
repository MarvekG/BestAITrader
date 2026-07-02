from datetime import datetime, timedelta
import json

import pytest
from sqlalchemy import func, select
from unittest.mock import AsyncMock

from app.ai.market_watch import audit
from app.ai.market_watch.audit import (
    MARKET_WATCH_EVENTS_CHANNEL,
    cleanup_old_events,
    is_in_cooldown,
    publish_market_watch_event,
    query_market_watch_decisions,
    query_market_watch_events,
)
from app.models.market_watch import MarketWatchEvent
from app.models.session import Session as AnalysisSession
from app.models.user import User


async def _create_user(db, user_id: int = 1) -> User:
    user = User(
        id=user_id,
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.com",
        password_hash="hash",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _add_event(
    db,
    *,
    user_id: int,
    event_type: str = "scan",
    status: str = "success",
    debate_session_id: str | None = None,
    task_id: str | None = None,
    watch_ai_decision: dict | list[dict] | None = None,
    debate_parameters: dict | None = None,
    reason: str | None = None,
    created_at: datetime | None = None,
) -> MarketWatchEvent:
    event = MarketWatchEvent(
        user_id=user_id,
        event_type=event_type,
        status=status,
        reason=reason,
        debate_session_id=debate_session_id,
        task_id=task_id,
        watch_ai_decision=watch_ai_decision,
        debate_parameters=debate_parameters,
        created_at=created_at or datetime.now(),
    )
    db.add(event)
    await db.commit()
    await db.refresh(event)
    return event


async def _add_debate_session(
    db,
    *,
    user_id: int,
    stock_code: str,
    created_at: datetime | None = None,
    status: str = "active",
) -> AnalysisSession:
    session = AnalysisSession(
        user_id=user_id,
        stock_code=stock_code,
        trading_frequency="日内交易 (Day Trading)",
        trading_strategy="趋势追踪 (Trend Following)",
        status=status,
        created_at=created_at or datetime.now(),
        updated_at=created_at or datetime.now(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


@pytest.mark.asyncio
async def test_is_in_cooldown_reads_successful_launch_events(async_db_session) -> None:
    await _create_user(async_db_session)
    launch_session = await _add_debate_session(
        async_db_session,
        user_id=1,
        stock_code="600519",
        created_at=datetime.now() - timedelta(minutes=10),
    )
    await _add_event(
        async_db_session,
        user_id=1,
        event_type="debate_launched",
        status="success",
        debate_session_id=str(launch_session.session_id),
        created_at=datetime.now() - timedelta(minutes=10),
    )

    assert await is_in_cooldown(user_id=1, stock_code="600519", cooldown_minutes=60) is True


@pytest.mark.asyncio
async def test_is_in_cooldown_ignores_failed_or_old_launches(async_db_session) -> None:
    await _create_user(async_db_session)
    failed_session = await _add_debate_session(
        async_db_session,
        user_id=1,
        stock_code="600519",
        created_at=datetime.now() - timedelta(minutes=10),
    )
    old_session = await _add_debate_session(
        async_db_session,
        user_id=1,
        stock_code="600519",
        created_at=datetime.now() - timedelta(minutes=90),
    )
    await _add_event(
        async_db_session,
        user_id=1,
        event_type="debate_launched",
        status="failed",
        debate_session_id=str(failed_session.session_id),
        created_at=datetime.now() - timedelta(minutes=10),
    )
    await _add_event(
        async_db_session,
        user_id=1,
        event_type="debate_launched",
        status="success",
        debate_session_id=str(old_session.session_id),
        created_at=datetime.now() - timedelta(minutes=90),
    )

    assert await is_in_cooldown(user_id=1, stock_code="600519", cooldown_minutes=60) is False


@pytest.mark.asyncio
async def test_is_in_cooldown_ignores_failed_debate_session(async_db_session) -> None:
    await _create_user(async_db_session)
    failed_session = await _add_debate_session(
        async_db_session,
        user_id=1,
        stock_code="600519",
        status="failed",
        created_at=datetime.now() - timedelta(minutes=10),
    )
    await _add_event(
        async_db_session,
        user_id=1,
        event_type="debate_launched",
        status="success",
        debate_session_id=str(failed_session.session_id),
        created_at=datetime.now() - timedelta(minutes=10),
    )

    assert await is_in_cooldown(user_id=1, stock_code="600519", cooldown_minutes=60) is False


@pytest.mark.asyncio
async def test_cleanup_old_events_deletes_records_older_than_90_days(async_db_session) -> None:
    await _create_user(async_db_session)
    await _add_event(async_db_session, user_id=1, created_at=datetime.now() - timedelta(days=91))
    recent = await _add_event(async_db_session, user_id=1, created_at=datetime.now() - timedelta(days=10))

    assert await cleanup_old_events(retention_days=90) == 1
    count = await async_db_session.scalar(select(func.count()).select_from(MarketWatchEvent))
    first = (await async_db_session.execute(select(MarketWatchEvent))).scalars().first()
    assert count == 1
    assert first.event_id == recent.event_id


@pytest.mark.asyncio
async def test_query_market_watch_events_uses_configured_retention(async_db_session, monkeypatch) -> None:
    await _create_user(async_db_session)
    monkeypatch.setattr(audit.settings, "MARKET_WATCH_EVENT_RETENTION_DAYS", 30)
    await _add_event(async_db_session, user_id=1, event_type="scan", created_at=datetime.now() - timedelta(days=31))
    matched = await _add_event(async_db_session, user_id=1, event_type="scan", created_at=datetime.now() - timedelta(days=29))

    events = await query_market_watch_events(user_id=1)

    assert [event.event_id for event in events] == [matched.event_id]


@pytest.mark.asyncio
async def test_query_market_watch_events_defaults_to_recent_configured_days_and_limit(async_db_session) -> None:
    await _create_user(async_db_session)
    await _add_event(async_db_session, user_id=1, event_type="scan", created_at=datetime.now() - timedelta(days=91))
    await _add_event(async_db_session, user_id=1, event_type="scan", created_at=datetime.now() - timedelta(days=2))
    await _add_event(
        async_db_session,
        user_id=1,
        event_type="debate_skipped",
        status="skipped",
        created_at=datetime.now() - timedelta(days=1),
    )

    events = await query_market_watch_events(user_id=1, limit=1)

    assert len(events) == 1
    assert events[0].event_type == "debate_skipped"


@pytest.mark.asyncio
async def test_query_market_watch_events_filters_type(async_db_session) -> None:
    await _create_user(async_db_session)
    await _add_event(async_db_session, user_id=1, event_type="scan")
    matched = await _add_event(async_db_session, user_id=1, event_type="debate_skipped", status="skipped")

    events = await query_market_watch_events(user_id=1, event_type="debate_skipped")

    assert [event.event_id for event in events] == [matched.event_id]


@pytest.mark.asyncio
async def test_query_market_watch_events_rejects_invalid_limit(async_db_session) -> None:
    await _create_user(async_db_session)

    with pytest.raises(ValueError, match="limit must be between 1 and 200"):
        await query_market_watch_events(user_id=1, limit=201)


@pytest.mark.asyncio
async def test_query_market_watch_decisions_returns_paginated_ai_rounds(async_db_session) -> None:
    await _create_user(async_db_session)
    await _create_user(async_db_session, user_id=2)
    oldest = await _add_event(
        async_db_session,
        user_id=1,
        event_type="ai_decision",
        watch_ai_decision={"stock_code": "000001"},
        created_at=datetime.now() - timedelta(minutes=3),
    )
    middle = await _add_event(
        async_db_session,
        user_id=1,
        event_type="ai_decision",
        watch_ai_decision={"stock_code": "000002"},
        created_at=datetime.now() - timedelta(minutes=2),
    )
    newest = await _add_event(
        async_db_session,
        user_id=1,
        event_type="ai_decision",
        watch_ai_decision={"stock_code": "000003"},
        created_at=datetime.now() - timedelta(minutes=1),
    )
    await _add_event(async_db_session, user_id=1, event_type="scan")
    await _add_event(async_db_session, user_id=2, event_type="ai_decision")

    first_page, total = await query_market_watch_decisions(user_id=1, page=1, page_size=2)
    second_page, second_total = await query_market_watch_decisions(user_id=1, page=2, page_size=2)

    assert total == 3
    assert second_total == 3
    assert [event.event_id for event in first_page] == [newest.event_id, middle.event_id]
    assert [event.event_id for event in second_page] == [oldest.event_id]


@pytest.mark.asyncio
async def test_query_market_watch_decisions_rejects_invalid_pagination(async_db_session) -> None:
    await _create_user(async_db_session)

    with pytest.raises(ValueError, match="page must be greater than or equal to 1"):
        await query_market_watch_decisions(user_id=1, page=0)
    with pytest.raises(ValueError, match="page_size must be between 1 and 50"):
        await query_market_watch_decisions(user_id=1, page_size=51)


@pytest.mark.asyncio
async def test_publish_market_watch_event_pushes_latest_ai_decision(async_db_session, monkeypatch) -> None:
    await _create_user(async_db_session)
    event = await _add_event(
        async_db_session,
        user_id=1,
        event_type="debate_skipped",
        status="skipped",
        reason="cooldown",
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
    assert decoded["reason"] == "cooldown"
    assert decoded["watch_ai_decision"]["stock_code"] == "600519"
    assert decoded["watch_ai_decision"]["action"] == "monitor"
    assert decoded["debate_parameters"]["trading_frequency"] == "day"
    assert "target_stock_code" not in decoded
    assert "target_stock_name" not in decoded
    assert "summary" not in decoded
