from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.llm_usage_log import LLMUsageLog
from app.tasks import llm_usage_cleanup_scheduler as scheduler_module


@pytest.mark.asyncio
async def test_llm_usage_cleanup_deletes_records_older_than_retention(
    async_db_session,
    monkeypatch,
) -> None:
    now = datetime(2026, 5, 19, 12, 0)
    async_db_session.add_all(
        [
            LLMUsageLog(
                model="deepseek-test",
                role="old",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                created_at=now - timedelta(days=8),
            ),
            LLMUsageLog(
                model="deepseek-test",
                role="recent",
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                created_at=now - timedelta(days=6),
            ),
        ]
    )
    await async_db_session.commit()

    monkeypatch.setattr(scheduler_module, "_now", lambda: now)

    result = await scheduler_module.cleanup_old_llm_usage()

    remaining_roles = (
        await async_db_session.execute(
            select(LLMUsageLog.role).order_by(LLMUsageLog.role)
        )
    ).scalars().all()
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
