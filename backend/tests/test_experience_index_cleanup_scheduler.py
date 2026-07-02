from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.experience_index import ExperienceIndex
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.session import Session as DebateSession
from app.models.user import User
from app.tasks import experience_index_cleanup_scheduler as scheduler_module
from app.tasks import scheduled_task_registry


async def _create_user_and_session(db):
    user = User(
        username="experience_index_cleanup",
        email="experience_index_cleanup@example.com",
        password_hash="hashed",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend",
        status="completed",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return user, session


async def _add_index(db, user, session, *, memory_observation_id: str, created_at: datetime) -> ExperienceIndex:
    row = ExperienceIndex(
        user_id=user.id,
        memory_observation_id=memory_observation_id,
        memory_source_id=f"source-{memory_observation_id}",
        review_run_id=f"review-{memory_observation_id}",
        session_id=session.session_id,
        stock_code=session.stock_code,
        stock_name="平安银行",
        industry="银行",
        strategy="trend",
        review_horizon="20d",
        outcome_label="profit",
        correctness="correct",
        importance="high",
        summary=f"summary-{memory_observation_id}",
        tags={},
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    await db.commit()
    return row


async def _add_review_event(db, user, session, *, review_run_id: str, created_at: datetime) -> ExperienceReviewEvent:
    row = ExperienceReviewEvent(
        review_run_id=review_run_id,
        session_id=session.session_id,
        user_id=user.id,
        stage="experience_review",
        status="completed",
        message_key="experience.live_messages.completed",
        payload={"review_run_id": review_run_id},
        created_at=created_at,
    )
    db.add(row)
    await db.commit()
    return row


def _settings(**overrides):
    values = {
        "EXPERIENCE_CLEANUP_ENABLED": True,
        "EXPERIENCE_INDEX_RETENTION_DAYS": 7,
        "EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS": 30,
        "EXPERIENCE_CLEANUP_SCHEDULE_HOUR": 3,
        "EXPERIENCE_CLEANUP_SCHEDULE_MINUTE": 30,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_get_experience_cleanup_config_uses_config_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler_module,
        "settings",
        _settings(
            EXPERIENCE_INDEX_RETENTION_DAYS=5,
            EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS=45,
            EXPERIENCE_CLEANUP_SCHEDULE_HOUR=4,
            EXPERIENCE_CLEANUP_SCHEDULE_MINUTE=15,
        ),
    )

    assert scheduler_module.get_experience_cleanup_config() == {
        "enabled": True,
        "index_retention_days": 5,
        "review_event_retention_days": 45,
        "schedule_hour": 4,
        "schedule_minute": 15,
    }


@pytest.mark.asyncio
async def test_experience_index_cleanup_deletes_records_older_than_configured_retention(async_db_session, monkeypatch) -> None:
    now = datetime(2026, 5, 22, 12, 0)
    user, session = await _create_user_and_session(async_db_session)
    await _add_index(async_db_session, user, session, memory_observation_id="old", created_at=now - timedelta(days=8))
    await _add_index(async_db_session, user, session, memory_observation_id="recent", created_at=now - timedelta(days=6))

    monkeypatch.setattr(scheduler_module, "_now", lambda: now)
    monkeypatch.setattr(scheduler_module, "settings", _settings(EXPERIENCE_INDEX_RETENTION_DAYS=7))

    result = await scheduler_module.cleanup_old_experience_indexes()

    remaining_observation_ids = list((await async_db_session.execute(
        select(ExperienceIndex.memory_observation_id).order_by(ExperienceIndex.memory_observation_id)
    )).scalars().all())
    assert result == {"deleted": 1, "retention_days": 7}
    assert remaining_observation_ids == ["recent"]


@pytest.mark.asyncio
async def test_experience_cleanup_deletes_review_events_older_than_month(async_db_session, monkeypatch) -> None:
    now = datetime(2026, 5, 22, 12, 0)
    user, session = await _create_user_and_session(async_db_session)
    await _add_review_event(async_db_session, user, session, review_run_id="old-review", created_at=now - timedelta(days=31))
    await _add_review_event(async_db_session, user, session, review_run_id="recent-review", created_at=now - timedelta(days=29))

    monkeypatch.setattr(scheduler_module, "_now", lambda: now)
    monkeypatch.setattr(scheduler_module, "settings", _settings(EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS=30))

    result = await scheduler_module.cleanup_old_experience_review_events()

    remaining_review_run_ids = list((await async_db_session.execute(
        select(ExperienceReviewEvent.review_run_id).order_by(ExperienceReviewEvent.review_run_id)
    )).scalars().all())
    assert result == {"deleted": 1, "retention_days": 30}
    assert remaining_review_run_ids == ["recent-review"]


@pytest.mark.asyncio
async def test_experience_cleanup_scheduler_deletes_indexes_and_review_events(async_db_session, monkeypatch) -> None:
    now = datetime(2026, 5, 22, 12, 0)
    user, session = await _create_user_and_session(async_db_session)
    await _add_index(async_db_session, user, session, memory_observation_id="old-index", created_at=now - timedelta(days=8))
    await _add_review_event(async_db_session, user, session, review_run_id="old-review", created_at=now - timedelta(days=31))

    monkeypatch.setattr(scheduler_module, "_now", lambda: now)
    monkeypatch.setattr(
        scheduler_module,
        "settings",
        _settings(EXPERIENCE_INDEX_RETENTION_DAYS=7, EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS=30),
    )

    result = await scheduler_module.cleanup_old_experience_records()

    assert result == {
        "experience_indexes_deleted": 1,
        "experience_index_retention_days": 7,
        "experience_review_events_deleted": 1,
        "experience_review_event_retention_days": 30,
    }


@pytest.mark.asyncio
async def test_experience_index_cleanup_uses_config_settings_retention_days(async_db_session, monkeypatch) -> None:
    now = datetime(2026, 5, 22, 12, 0)
    user, session = await _create_user_and_session(async_db_session)
    await _add_index(async_db_session, user, session, memory_observation_id="five-days", created_at=now - timedelta(days=5))
    await _add_index(async_db_session, user, session, memory_observation_id="two-days", created_at=now - timedelta(days=2))

    monkeypatch.setattr(scheduler_module, "_now", lambda: now)
    monkeypatch.setattr(scheduler_module, "settings", _settings(EXPERIENCE_INDEX_RETENTION_DAYS=3))

    result = await scheduler_module.cleanup_old_experience_indexes()

    remaining_observation_ids = list((await async_db_session.execute(
        select(ExperienceIndex.memory_observation_id).order_by(ExperienceIndex.memory_observation_id)
    )).scalars().all())
    assert result == {"deleted": 1, "retention_days": 3}
    assert remaining_observation_ids == ["two-days"]


def test_experience_index_cleanup_scheduler_registers_configured_daily_job(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler_module,
        "settings",
        _settings(
            EXPERIENCE_INDEX_RETENTION_DAYS=5,
            EXPERIENCE_REVIEW_EVENT_RETENTION_DAYS=45,
            EXPERIENCE_CLEANUP_SCHEDULE_HOUR=4,
            EXPERIENCE_CLEANUP_SCHEDULE_MINUTE=15,
        ),
    )

    snapshot = scheduler_module.get_scheduled_tasks()

    assert snapshot.disabled_job_ids == []
    task = snapshot.tasks[0]
    assert task.job_id == scheduler_module.EXPERIENCE_INDEX_CLEANUP_JOB_ID
    assert task.trigger_type == "cron"
    assert task.trigger_args == {"hour": 4, "minute": 15}
    assert task.task_kwargs == {"index_retention_days": 5, "review_event_retention_days": 45}


def test_scheduled_task_registry_includes_experience_index_cleanup_job() -> None:
    snapshot = scheduled_task_registry.load_scheduled_tasks()

    job_ids = {task.job_id for task in snapshot.tasks}
    assert scheduler_module.EXPERIENCE_INDEX_CLEANUP_JOB_ID in job_ids
