from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.llm_usage_log import LLMUsageLog
from app.tasks import llm_usage_cleanup_scheduler as scheduler_module


@pytest.fixture
def usage_session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    LLMUsageLog.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()


def test_llm_usage_cleanup_deletes_records_older_than_retention(usage_session_factory, monkeypatch) -> None:
    now = datetime(2026, 5, 19, 12, 0)
    with usage_session_factory() as db:
        db.add(
            LLMUsageLog(
                model="deepseek-test",
                role="old",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                created_at=now - timedelta(days=8),
            )
        )
        db.add(
            LLMUsageLog(
                model="deepseek-test",
                role="recent",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                created_at=now - timedelta(days=6),
            )
        )
        db.commit()

    monkeypatch.setattr(scheduler_module, "SessionLocal", usage_session_factory)
    monkeypatch.setattr(scheduler_module, "_now", lambda: now)

    result = scheduler_module.cleanup_old_llm_usage()

    with usage_session_factory() as db:
        remaining_roles = [role for role, in db.query(LLMUsageLog.role).order_by(LLMUsageLog.role).all()]
    assert result == {"deleted": 1, "retention_days": 7}
    assert remaining_roles == ["recent"]


def test_llm_usage_cleanup_scheduler_registers_daily_job() -> None:
    snapshot = scheduler_module.get_scheduled_tasks()

    assert snapshot.disabled_job_ids == []
    task = snapshot.tasks[0]
    assert task.job_id == scheduler_module.LLM_USAGE_CLEANUP_JOB_ID
    assert task.trigger_type == "cron"
    assert task.trigger_args == {"hour": 3, "minute": 0}
    assert task.run_immediately is False
