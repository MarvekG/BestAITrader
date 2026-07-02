from datetime import date
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.data_storage import IndexDaily
from app.models.user import User
from app.tasks import account_equity_snapshot_scheduler as scheduler_module


def test_account_equity_snapshot_scheduler_registers_daily_job() -> None:
    """账户净值快照调度器应注册每日收盘后任务。"""
    snapshot = scheduler_module.get_scheduled_tasks()
    jobs = {task.job_id: task for task in snapshot.tasks}

    job = jobs[scheduler_module.ACCOUNT_EQUITY_SNAPSHOT_JOB_ID]
    assert job.trigger_type == "cron"
    assert job.trigger_args == {"hour": 16, "minute": 10}
    assert job.misfire_grace_time == 3600


@pytest.mark.asyncio
async def test_generate_daily_account_equity_snapshots_creates_snapshot(
    monkeypatch,
    async_db_session,
) -> None:
    """每日快照任务应为所有有账户的用户生成快照。"""
    user = User(id=201, username="snapshot_user", email="snapshot@example.com", password_hash="hash", is_active=True)
    account = Account(
        user_id=201,
        total_assets=Decimal("1000000.0000"),
        available_cash=Decimal("900000.0000"),
        frozen_cash=Decimal("0.0000"),
        market_value=Decimal("100000.0000"),
        initial_capital=Decimal("1000000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=0,
    )
    async_db_session.add(user)
    async_db_session.add(account)
    async_db_session.add(IndexDaily(index_code="000300.SH", trade_date=date(2026, 5, 22), close=4000.0))
    await async_db_session.commit()
    monkeypatch.setattr(scheduler_module, "_today", lambda: date(2026, 5, 22))

    result = await scheduler_module.generate_daily_account_equity_snapshots()

    assert result == {"status": "success", "created": 1, "failed": 0}
