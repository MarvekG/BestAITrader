from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.async_task import AsyncTask
from app.tasks import scheduled_task_registry
from app.tasks import async_task_cleanup_scheduler as scheduler_module


@pytest.fixture
def async_task_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AsyncTask.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def _settings(**overrides):
    values = {
        "ASYNC_TASK_CLEANUP_ENABLED": True,
        "ASYNC_TASK_RETENTION_DAYS": 30,
        "ASYNC_TASK_CLEANUP_SCHEDULE_HOUR": 4,
        "ASYNC_TASK_CLEANUP_SCHEDULE_MINUTE": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _add_task(db, *, task_id: str, created_at: datetime) -> None:
    db.add(
        AsyncTask(
            task_id=task_id,
            task_name=f"Task {task_id}",
            task_type="stock_analysis",
            status="completed",
            allow_concurrent=True,
            parameters={"question": task_id},
            created_at=created_at,
        )
    )


def test_get_async_task_cleanup_config_uses_config_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler_module,
        "settings",
        _settings(
            ASYNC_TASK_RETENTION_DAYS=45,
            ASYNC_TASK_CLEANUP_SCHEDULE_HOUR=2,
            ASYNC_TASK_CLEANUP_SCHEDULE_MINUTE=15,
        ),
    )

    assert scheduler_module.get_async_task_cleanup_config() == {
        "enabled": True,
        "retention_days": 45,
        "schedule_hour": 2,
        "schedule_minute": 15,
    }


def test_async_task_cleanup_deletes_tasks_older_than_configured_retention(
    async_task_session_factory,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 27, 12, 0)
    with async_task_session_factory() as db:
        _add_task(db, task_id="old", created_at=now - timedelta(days=31))
        _add_task(db, task_id="recent", created_at=now - timedelta(days=29))
        db.commit()

    monkeypatch.setattr(scheduler_module, "_now", lambda: now)
    monkeypatch.setattr(scheduler_module, "SessionLocal", async_task_session_factory)
    monkeypatch.setattr(scheduler_module, "settings", _settings(ASYNC_TASK_RETENTION_DAYS=30))

    result = scheduler_module.cleanup_old_async_tasks()

    with async_task_session_factory() as db:
        remaining_task_ids = [task_id for task_id, in db.query(AsyncTask.task_id).order_by(AsyncTask.task_id).all()]
    assert result == {"deleted": 1, "retention_days": 30}
    assert remaining_task_ids == ["recent"]


def test_async_task_cleanup_scheduler_registers_configured_daily_job(monkeypatch) -> None:
    monkeypatch.setattr(
        scheduler_module,
        "settings",
        _settings(
            ASYNC_TASK_RETENTION_DAYS=60,
            ASYNC_TASK_CLEANUP_SCHEDULE_HOUR=5,
            ASYNC_TASK_CLEANUP_SCHEDULE_MINUTE=30,
        ),
    )

    snapshot = scheduler_module.get_scheduled_tasks()

    assert snapshot.disabled_job_ids == []
    task = snapshot.tasks[0]
    assert task.job_id == scheduler_module.ASYNC_TASK_CLEANUP_JOB_ID
    assert task.trigger_type == "cron"
    assert task.trigger_args == {"hour": 5, "minute": 30}
    assert task.task_kwargs == {"retention_days": 60}


def test_scheduled_task_registry_includes_async_task_cleanup_job(db_session, monkeypatch) -> None:
    from app.tasks import experience_review_scheduler

    monkeypatch.setattr(experience_review_scheduler, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(scheduler_module, "SessionLocal", lambda: db_session)

    snapshot = scheduled_task_registry.load_scheduled_tasks()

    job_ids = {task.job_id for task in snapshot.tasks}
    assert scheduler_module.ASYNC_TASK_CLEANUP_JOB_ID in job_ids
