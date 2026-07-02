from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.models.position import Position
from app.trading.pm_rules import evaluate_position_disciplines, sync_pm_discipline_to_position


async def _create_user(db, async_create_user, async_ensure_account):
    user = await async_create_user(
        db,
        username="pmrules",
        email="pmrules@example.com",
        password="password123",
    )
    account = await async_ensure_account(db, user)
    return user, account


async def _create_position(db, account, *, stock_code="000001.SZ", current_price=Decimal("10.00")):
    position = Position(
        account_id=account.account_id,
        stock_code=stock_code,
        total_shares=100,
        available_shares=100,
        frozen_shares=0,
        avg_cost=Decimal("9.00"),
        current_price=current_price,
        market_value=Decimal("1000.00"),
        profit_loss=Decimal("100.00"),
        profit_loss_pct=Decimal("0.1000"),
        purchase_details={},
    )
    db.add(position)
    await db.commit()
    await db.refresh(position)
    return position


@pytest.mark.asyncio
async def test_sync_pm_discipline_to_position_writes_structured_fields(
    async_db_session,
    async_create_user,
    async_ensure_account,
):
    user, account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    position = await _create_position(async_db_session, account)

    synced = await sync_pm_discipline_to_position(
        session_id=None,
        user_id=user.id,
        stock_code=position.stock_code,
        decision={
            "decision": "hold",
            "stop_loss": 8.5,
            "take_profit": 12.0,
            "holding_horizon_days": 5,
        },
    )

    await async_db_session.refresh(position)
    assert synced is True
    assert position.stop_loss == Decimal("8.5")
    assert position.take_profit == Decimal("12.0")
    assert position.horizon_deadline is not None


@pytest.mark.asyncio
async def test_sync_pm_discipline_to_position_ignores_non_positive_trigger_prices(
    async_db_session,
    async_create_user,
    async_ensure_account,
):
    """PM 输出空仓目标时不把 0 写成持仓止损止盈触发线。"""
    user, account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    position = await _create_position(async_db_session, account)

    synced = await sync_pm_discipline_to_position(
        session_id=None,
        user_id=user.id,
        stock_code=position.stock_code,
        decision={
            "decision": "hold",
            "stop_loss": 0,
            "take_profit": 0,
            "holding_horizon_days": 5,
        },
    )

    await async_db_session.refresh(position)
    assert synced is True
    assert position.stop_loss is None
    assert position.take_profit is None
    assert position.horizon_deadline is not None


@pytest.mark.asyncio
async def test_evaluate_position_disciplines_detects_stop_loss(
    async_db_session,
    async_create_user,
    async_ensure_account,
    monkeypatch,
):
    user, account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    position = await _create_position(async_db_session, account)
    position.stop_loss = Decimal("9.50")
    await async_db_session.commit()
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        lambda stock_code: {"success": True, "latest_price": "9.40"},
    )

    triggered = await evaluate_position_disciplines(user_id=user.id)

    assert triggered[0]["stock_code"] == "000001.SZ"
    assert triggered[0]["trigger"] == "stop_loss"


@pytest.mark.asyncio
async def test_evaluate_position_disciplines_detects_take_profit(
    async_db_session,
    async_create_user,
    async_ensure_account,
    monkeypatch,
):
    user, account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    position = await _create_position(async_db_session, account)
    position.take_profit = Decimal("11.00")
    await async_db_session.commit()
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        lambda stock_code: {"success": True, "latest_price": "11.10"},
    )

    triggered = await evaluate_position_disciplines(user_id=user.id)

    assert triggered[0]["trigger"] == "take_profit"


@pytest.mark.asyncio
async def test_evaluate_position_disciplines_detects_horizon_expired(
    async_db_session,
    async_create_user,
    async_ensure_account,
    monkeypatch,
):
    user, account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    position = await _create_position(async_db_session, account)
    position.horizon_deadline = datetime.now() - timedelta(days=1)
    await async_db_session.commit()
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        lambda stock_code: {"success": False},
    )

    triggered = await evaluate_position_disciplines(user_id=user.id)

    assert triggered[0]["trigger"] == "horizon_expired"
