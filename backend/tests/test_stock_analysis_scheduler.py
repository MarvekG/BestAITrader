from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.models.async_task import AsyncTask
from app.models.stock_warehouse import StockWarehouse
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.tasks import stock_analysis_scheduler
from app.tasks.stock_analysis_scheduler import is_due_for_auto_analysis


def _warehouse_stock(**overrides):
    values = {
        "stock_code": "600519.SH",
        "user_id": 1,
        "is_active": True,
        "auto_analysis_enabled": True,
        "auto_analysis_frequency": "daily",
        "auto_analysis_time": "09:35",
        "last_auto_analysis_at": None,
    }
    values.update(overrides)
    return StockWarehouse(**values)


def test_auto_analysis_waits_until_configured_time():
    stock = _warehouse_stock(auto_analysis_time="09:40")

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 39)) is False
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 40)) is True


def test_auto_analysis_does_not_catch_up_after_configured_time():
    stock = _warehouse_stock(auto_analysis_time="09:40")

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 45)) is False


def test_auto_analysis_uses_short_trigger_window_after_configured_time():
    stock = _warehouse_stock(auto_analysis_time="09:40")

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 44)) is True


def test_auto_analysis_daily_frequency_runs_once_per_day():
    stock = _warehouse_stock(last_auto_analysis_at=datetime(2026, 5, 11, 9, 45))

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 10, 0)) is False
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 12, 9, 35)) is True


def test_auto_analysis_run_immediately_skips_time_gate():
    stock = _warehouse_stock(
        auto_analysis_time="09:40",
        auto_analysis_run_immediately=True,
    )

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 39)) is True


def test_auto_analysis_run_immediately_false_requires_trigger_window():
    stock = _warehouse_stock(
        auto_analysis_time="09:40",
        auto_analysis_run_immediately=False,
    )

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 39)) is False
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 40)) is True
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 44)) is True
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 45)) is False


def test_auto_analysis_run_immediately_runs_even_if_already_run():
    stock = _warehouse_stock(
        auto_analysis_time="09:40",
        auto_analysis_run_immediately=True,
        last_auto_analysis_at=datetime(2026, 5, 11, 9, 45),
    )

    # Even though it already ran today at 09:45, run-immediately should trigger again
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 10, 0)) is True


def test_auto_analysis_weekly_and_monthly_frequency():
    weekly = _warehouse_stock(
        auto_analysis_frequency="weekly",
        last_auto_analysis_at=datetime(2026, 5, 11, 9, 45),
    )
    monthly = _warehouse_stock(
        auto_analysis_frequency="monthly",
        last_auto_analysis_at=datetime(2026, 5, 11, 9, 45),
    )

    assert is_due_for_auto_analysis(weekly, datetime(2026, 5, 15, 10, 0)) is False
    assert is_due_for_auto_analysis(weekly, datetime(2026, 5, 18, 9, 35)) is True
    assert is_due_for_auto_analysis(monthly, datetime(2026, 5, 18, 10, 0)) is False
    assert is_due_for_auto_analysis(monthly, datetime(2026, 6, 1, 9, 35)) is True


@pytest.mark.asyncio
async def test_auto_analysis_task_records_owner_user_id(async_db_session, monkeypatch):
    user = User(username="auto_analysis_owner", email="auto_analysis_owner@example.com", password_hash="hash")
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    stock = _warehouse_stock(auto_analysis_run_immediately=True, user_id=user.id)
    async_db_session.add(stock)
    await async_db_session.commit()
    await async_db_session.refresh(stock)

    async def _noop_sync(_stock_code):
        return True

    async def _noop_analysis(**_kwargs):
        return None

    monkeypatch.setattr(stock_analysis_scheduler, "sync_stock_data_before_analysis", _noop_sync)
    monkeypatch.setattr(stock_analysis_scheduler, "run_analysis_task", _noop_analysis)

    launch_info = await stock_analysis_scheduler._launch_analysis(stock.id, datetime(2026, 5, 11, 9, 40))

    task = await async_db_session.scalar(select(AsyncTask).where(AsyncTask.task_id == launch_info["task_id"]))
    assert task is not None
    assert task.user_id == stock.user_id


@pytest.mark.asyncio
async def test_auto_analysis_respects_global_debate_concurrency_limit(async_db_session, monkeypatch):
    user = User(username="auto_analysis_limit", email="auto_analysis_limit@example.com", password_hash="hash")
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    stock = _warehouse_stock(auto_analysis_run_immediately=True, user_id=user.id, stock_code="000002.SZ")
    async_db_session.add_all([
        stock,
        SystemSetting(key="ai_debate.max_concurrent", value=1, description="test"),
        AsyncTask(
            task_name="AI Analysis - 000001.SZ",
            task_type="ai_analysis",
            status="running",
            allow_concurrent=False,
            parameters={"stock_code": "000001.SZ"},
            user_id=user.id,
        ),
    ])
    await async_db_session.commit()
    await async_db_session.refresh(stock)

    async def _noop_sync(_stock_code):
        return True

    monkeypatch.setattr(stock_analysis_scheduler, "sync_stock_data_before_analysis", _noop_sync)

    launch_info = await stock_analysis_scheduler._launch_analysis(stock.id, datetime(2026, 5, 11, 9, 40))

    assert launch_info is None
    assert await async_db_session.scalar(select(func.count()).select_from(AsyncTask)) == 1
    await async_db_session.refresh(stock)
    assert "AI投研辩论并发数已达上限" in stock.last_auto_analysis_error
