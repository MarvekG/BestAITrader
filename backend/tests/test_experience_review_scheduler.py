from datetime import date, datetime, timedelta
import uuid

import pytest

from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER
from app.models.data_storage import KlineData, StockBasic
from app.models.debate_message import DebateMessage
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.pm_decision import PMDecisionRecord
from app.models.session import Session as DebateSession
from app.models.user import User
from app.tasks import experience_review_scheduler as scheduler_module
from app.tasks.experience_review_scheduler import (
    get_experience_review_scheduler_config,
    update_experience_review_scheduler_config,
)


async def _create_user(async_db_session) -> User:
    user = User(
        username=f"scheduled_experience_{uuid.uuid4().hex[:8]}",
        email=f"scheduled_experience_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)
    return user


async def _create_stock(async_db_session, stock_code: str = "000001.SZ") -> None:
    async_db_session.add(
        StockBasic(
            stock_code=stock_code,
            name="Ping An Bank",
            industry="Bank",
            market="SZSE",
        )
    )
    await async_db_session.commit()


async def _create_completed_session(
    async_db_session,
    user: User,
    stock_code: str = "000001.SZ",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> DebateSession:
    session = DebateSession(
        user_id=user.id,
        stock_code=stock_code,
        trading_frequency="swing",
        trading_strategy="trend",
        status="completed",
    )
    if created_at is not None:
        session.created_at = created_at
    if updated_at is not None:
        session.updated_at = updated_at
    async_db_session.add(session)
    await async_db_session.commit()
    await async_db_session.refresh(session)
    return session


async def _create_pm_message(async_db_session, session: DebateSession, created_at: datetime) -> DebateMessage:
    async_db_session.add(
        PMDecisionRecord(
            session_id=session.session_id,
            user_id=session.user_id,
            stock_code=session.stock_code,
            target_position=0.5,
            confidence_score=80,
            created_at=created_at,
        )
    )
    message = DebateMessage(
        session_id=session.session_id,
        stage="portfolio_manager",
        round_number=1,
        agent_name="PM",
        agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
        decision="buy",
        reasoning="PM decision",
        created_at=created_at,
    )
    async_db_session.add(message)
    await async_db_session.commit()
    await async_db_session.refresh(message)
    return message


async def _create_daily_klines(
    async_db_session,
    *,
    stock_code: str = "000001.SZ",
    start_date: date = date(2026, 1, 1),
    count: int = scheduler_module.EXPERIENCE_REVIEW_MIN_MARKET_DAYS,
) -> None:
    rows = [
        KlineData(
            stock_code=stock_code,
            date=start_date + timedelta(days=index),
            freq="D",
            open=10 + index,
            close=10.5 + index,
            high=11 + index,
            low=9.5 + index,
        )
        for index in range(count)
    ]
    async_db_session.add_all(rows)
    await async_db_session.commit()


@pytest.mark.asyncio
async def test_load_due_sessions_requires_completed_session_pm_message_and_market_data(async_db_session):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    pm_created_at = datetime(2026, 1, 1, 15, 0)
    await _create_pm_message(async_db_session, session, pm_created_at)
    await _create_daily_klines(async_db_session)

    async with scheduler_module.database_module.AsyncSessionLocal() as db:
        candidates = await scheduler_module._load_due_sessions(db, limit=10)

    assert len(candidates) == 1
    assert candidates[0].session_id == session.session_id
    assert candidates[0].user_id == user.id
    assert candidates[0].market_day_count == scheduler_module.EXPERIENCE_REVIEW_MIN_MARKET_DAYS


@pytest.mark.asyncio
async def test_load_due_sessions_returns_all_due_review_horizons(async_db_session):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    await _create_pm_message(async_db_session, session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session, count=61)

    async with scheduler_module.database_module.AsyncSessionLocal() as db:
        candidates = await scheduler_module._load_due_sessions(db, limit=10)

    assert [candidate.review_horizon for candidate in candidates] == ["5d", "20d", "60d"]
    assert {candidate.session_id for candidate in candidates} == {session.session_id}


@pytest.mark.asyncio
async def test_load_due_sessions_returns_missing_horizons_when_one_review_exists(async_db_session):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    await _create_pm_message(async_db_session, session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session, count=61)
    async_db_session.add(
        ExperienceReviewEvent(
            review_run_id=str(uuid.uuid4()),
            session_id=session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
            payload={"review_horizon": "20d"},
        )
    )
    await async_db_session.commit()

    async with scheduler_module.database_module.AsyncSessionLocal() as db:
        candidates = await scheduler_module._load_due_sessions(db, limit=10)

    assert [candidate.review_horizon for candidate in candidates] == ["5d", "60d"]


@pytest.mark.asyncio
async def test_scheduler_config_defaults_to_disabled(test_db):
    config = await get_experience_review_scheduler_config()

    assert config["enabled"] is False
    assert "min_market_days" not in config


@pytest.mark.asyncio
async def test_load_due_sessions_ignores_review_events_without_horizon(async_db_session):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    await _create_pm_message(async_db_session, session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session, count=21)
    async_db_session.add(
        ExperienceReviewEvent(
            review_run_id=str(uuid.uuid4()),
            session_id=session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
        )
    )
    await async_db_session.commit()

    async with scheduler_module.database_module.AsyncSessionLocal() as db:
        candidates = await scheduler_module._load_due_sessions(db, limit=10)

    assert [candidate.review_horizon for candidate in candidates] == ["5d", "20d"]


@pytest.mark.asyncio
async def test_load_due_sessions_skips_duplicate_existing_review_horizon(async_db_session):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    await _create_pm_message(async_db_session, session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session)
    async_db_session.add(
        ExperienceReviewEvent(
            review_run_id=str(uuid.uuid4()),
            session_id=session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
            payload={"review_horizon": "5d"},
        )
    )
    await async_db_session.commit()

    async with scheduler_module.database_module.AsyncSessionLocal() as db:
        assert await scheduler_module._load_due_sessions(db, limit=10) == []


@pytest.mark.asyncio
async def test_load_due_sessions_filters_reviewed_sessions_before_candidate_lookback(async_db_session):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    reviewed_session = await _create_completed_session(
        async_db_session,
        user,
        created_at=datetime(2026, 1, 1, 10, 0),
        updated_at=datetime(2026, 1, 1, 10, 0),
    )
    due_session = await _create_completed_session(
        async_db_session,
        user,
        created_at=datetime(2026, 1, 2, 10, 0),
        updated_at=datetime(2026, 1, 2, 10, 0),
    )
    await _create_pm_message(async_db_session, reviewed_session, datetime(2026, 1, 1, 15, 0))
    await _create_pm_message(async_db_session, due_session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session)
    async_db_session.add(
        ExperienceReviewEvent(
            review_run_id=str(uuid.uuid4()),
            session_id=reviewed_session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
            payload={"review_horizon": "5d"},
        )
    )
    await async_db_session.commit()

    async with scheduler_module.database_module.AsyncSessionLocal() as db:
        candidates = await scheduler_module._load_due_sessions(db, limit=1, candidate_lookback=1)

    assert len(candidates) == 1
    assert candidates[0].session_id == due_session.session_id


@pytest.mark.asyncio
async def test_load_due_sessions_waits_for_enough_market_data(async_db_session):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    await _create_pm_message(async_db_session, session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session, count=scheduler_module.EXPERIENCE_REVIEW_MIN_MARKET_DAYS - 1)

    async with scheduler_module.database_module.AsyncSessionLocal() as db:
        assert await scheduler_module._load_due_sessions(db, limit=10) == []


@pytest.mark.asyncio
async def test_get_scheduled_tasks_returns_cron_definition_when_enabled(
    test_db,
    monkeypatch,
):
    await update_experience_review_scheduler_config(
        {
            "enabled": True,
            "schedule_hour": 18,
            "schedule_minute": 30,
        },
    )
    snapshot = scheduler_module.get_scheduled_tasks()
    task = snapshot.tasks[0]

    assert snapshot.disabled_job_ids == []
    assert task.job_id == scheduler_module.EXPERIENCE_REVIEW_JOB_ID
    assert task.trigger_type == "cron"
    assert task.trigger_args == {"hour": 18, "minute": 30}


@pytest.mark.asyncio
async def test_get_scheduled_tasks_marks_experience_job_disabled(
    test_db,
    monkeypatch,
):
    await update_experience_review_scheduler_config({"enabled": False})
    snapshot = scheduler_module.get_scheduled_tasks()

    assert snapshot.tasks[0].job_id == scheduler_module.EXPERIENCE_REVIEW_JOB_ID
    assert snapshot.disabled_job_ids == []


@pytest.mark.asyncio
async def test_run_due_reviews_skips_when_scheduler_is_disabled(
    async_db_session,
    monkeypatch,
):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    await _create_pm_message(async_db_session, session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session)
    await update_experience_review_scheduler_config({"enabled": False})
    calls = []

    async def fake_analyze(*, user_id, session_id, review_horizon=None):
        calls.append((user_id, session_id, review_horizon))
        return {"review_run_id": "review-1"}

    monkeypatch.setattr(scheduler_module.experience_service, "analyze", fake_analyze)

    result = await scheduler_module.run_due_reviews()

    assert result["launched"] == 0
    assert result["skipped"] == "disabled"
    assert calls == []


@pytest.mark.asyncio
async def test_run_due_reviews_invokes_experience_service_for_due_session(
    async_db_session,
    monkeypatch,
):
    user = await _create_user(async_db_session)
    await _create_stock(async_db_session)
    session = await _create_completed_session(async_db_session, user)
    await _create_pm_message(async_db_session, session, datetime(2026, 1, 1, 15, 0))
    await _create_daily_klines(async_db_session)
    await update_experience_review_scheduler_config({"enabled": True})
    calls = []

    async def fake_analyze(*, user_id, session_id, review_horizon=None):
        calls.append((user_id, session_id, review_horizon))
        return {"review_run_id": "review-1"}

    monkeypatch.setattr(scheduler_module.experience_service, "analyze", fake_analyze)

    result = await scheduler_module.run_due_reviews()

    assert result["launched"] == 1
    assert result["items"][0]["review_run_id"] == "review-1"
    assert calls == [(user.id, session.session_id, "5d")]


def test_scheduler_config_api_defaults_and_updates(client, auth_headers):
    response = client.get("/api/v1/experience/scheduler-config", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["enabled"] is False

    update_response = client.put(
        "/api/v1/experience/scheduler-config",
        headers=auth_headers,
        json={
            "enabled": True,
            "schedule_hour": 19,
            "schedule_minute": 5,
            "min_market_days": 30,
            "candidate_lookback": 300,
            "max_runs_per_tick": 3,
        },
    )

    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["enabled"] is True
    assert payload["schedule_hour"] == 19
    assert payload["schedule_minute"] == 5
    assert "min_market_days" not in payload
    assert payload["candidate_lookback"] == 300
    assert payload["max_runs_per_tick"] == 3
