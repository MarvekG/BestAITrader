from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.ai.market_watch.schemas import MarketWatchSettingsResponse
from app.models.user import User
from app.tasks import market_watch_scheduler as scheduler_module
from app.tasks.market_watch_scheduler import (
    MARKET_WATCH_AUDIT_CLEANUP_JOB_ID,
    MARKET_WATCH_SCAN_JOB_ID,
    MARKET_WATCH_TICK_SECONDS,
)


def test_market_watch_scheduler_returns_minimum_frequency_tick() -> None:
    snapshot = scheduler_module.get_scheduled_tasks()
    captured_jobs = {task.job_id: task for task in snapshot.tasks}

    scan_job = captured_jobs[MARKET_WATCH_SCAN_JOB_ID]
    assert MARKET_WATCH_TICK_SECONDS == 30
    assert scan_job.trigger_type == "interval"
    assert scan_job.trigger_args == {"seconds": MARKET_WATCH_TICK_SECONDS}

    cleanup_job = captured_jobs[MARKET_WATCH_AUDIT_CLEANUP_JOB_ID]
    assert cleanup_job.trigger_type == "interval"
    assert cleanup_job.trigger_args == {"days": 1}
    assert cleanup_job.run_immediately is True


def test_market_watch_scheduler_due_check_uses_user_interval() -> None:
    now = datetime(2026, 5, 15, 10, 0)
    scheduler_module.last_scan_at_by_user.clear()

    assert scheduler_module._is_due(7, 45, now) is True

    scheduler_module.last_scan_at_by_user[7] = now - timedelta(seconds=44)
    assert scheduler_module._is_due(7, 45, now) is False

    scheduler_module.last_scan_at_by_user[7] = now - timedelta(seconds=45)
    assert scheduler_module._is_due(7, 45, now) is True


@pytest.mark.asyncio
async def test_market_watch_scheduler_runs_due_enabled_users(monkeypatch, sqlite_session_factory, db_session) -> None:
    user = User(id=7, username="watcher", email="watcher@example.com", password_hash="hash", is_active=True)
    db_session.add(user)
    db_session.commit()
    calls: list[dict[str, object]] = []

    def fake_settings(user_id: int) -> MarketWatchSettingsResponse:
        return MarketWatchSettingsResponse(user_id=user_id, scan_interval_seconds=45)

    async def fake_scan_market_watch(user_id: int, **kwargs):
        calls.append({"user_id": user_id, **kwargs})
        return {
            "stock_count": 2,
            "news_count": 1,
            "debate_launch": {"status": "not_started"},
        }

    monkeypatch.setattr(scheduler_module, "SessionLocal", sqlite_session_factory)
    monkeypatch.setattr(scheduler_module, "get_market_watch_settings", fake_settings)
    monkeypatch.setattr(scheduler_module, "scan_market_watch", fake_scan_market_watch)
    monkeypatch.setattr(scheduler_module, "_shanghai_now", lambda: datetime(2026, 5, 15, 10, 0))
    scheduler_module.last_scan_at_by_user.clear()
    scheduler_module.running_user_ids.clear()

    first = await scheduler_module.run_due_scans()
    second = await scheduler_module.run_due_scans()

    assert first["scanned"] == 1
    assert second["scanned"] == 0
    assert second["skipped"] == 1
    assert len(calls) == 1
    assert calls[0]["user_id"] == 7
    assert calls[0]["now"] == datetime(2026, 5, 15, 10, 0)
    assert calls[0]["debate_launcher"] is scheduler_module._launch_debate_task


@pytest.mark.asyncio
async def test_market_watch_scheduler_skips_disabled_users(monkeypatch, sqlite_session_factory, db_session) -> None:
    user = User(id=8, username="disabled", email="disabled@example.com", password_hash="hash", is_active=True)
    db_session.add(user)
    db_session.commit()

    def fake_settings(user_id: int) -> MarketWatchSettingsResponse:
        return MarketWatchSettingsResponse(user_id=user_id, auto_scan_enabled=False)

    async def fake_scan_market_watch(*args, **kwargs):
        raise AssertionError("scan should not run for disabled market watch settings")

    monkeypatch.setattr(scheduler_module, "SessionLocal", sqlite_session_factory)
    monkeypatch.setattr(scheduler_module, "get_market_watch_settings", fake_settings)
    monkeypatch.setattr(scheduler_module, "scan_market_watch", fake_scan_market_watch)
    scheduler_module.last_scan_at_by_user.clear()
    scheduler_module.running_user_ids.clear()

    result = await scheduler_module.run_due_scans()

    assert result == {"scanned": 0, "skipped": 1, "items": []}
