from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.account import Account
from app.models.order import Order
from app.models.position import Position
from app.models.trade_record import TradeRecord
from app.models.user import User
from app.trading.service import TradingService


async def _seed_account(db, *, user_id: int = 42, cash: str = "100000") -> Account:
    db.add(
        User(
            id=user_id,
            username=f"trading_service_{user_id}",
            email=f"trading_service_{user_id}@example.com",
            password_hash="test",
            is_active=True,
        )
    )
    await db.flush()
    account = Account(
        account_id=uuid4(),
        user_id=user_id,
        available_cash=Decimal(cash),
        frozen_cash=Decimal("0"),
        total_assets=Decimal(cash),
        market_value=Decimal("0"),
        total_profit_loss=Decimal("0"),
        profit_loss_pct=Decimal("0"),
        total_trades=0,
        win_rate=Decimal("0"),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


@pytest.fixture(autouse=True)
def patch_service_async_session(monkeypatch, test_db):
    del test_db
    monkeypatch.setattr("app.trading.service.is_trading_time", lambda: True)


@pytest.fixture(autouse=True)
def patch_risk_and_websocket(monkeypatch):
    async def _allow_order(*_args, **_kwargs):
        return {
            "enabled": True,
            "passed": True,
            "severity": "none",
            "accepted": [],
            "blocks": [],
            "metrics": {},
        }

    monkeypatch.setattr("app.trading.service.portfolio_risk_control_service.evaluate_order", _allow_order)
    monkeypatch.setattr("app.trading.service.ws_manager.send_order_status", AsyncMock())
    monkeypatch.setattr("app.trading.service.ws_manager.send_position_update", AsyncMock())
    monkeypatch.setattr("app.trading.service.ws_manager.send_trade_executed", AsyncMock())


@pytest.mark.asyncio
async def test_service_creates_pending_limit_buy_and_freezes_cash(async_db_session):
    service = TradingService()
    service.engine.execute_order = AsyncMock()
    account = await _seed_account(async_db_session)
    account_id = account.account_id

    result = await service.execute_order_and_update_db(
        session_id=None,
        account_id=account.account_id,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=12.8,
        order_type="limit",
    )

    async_db_session.expire_all()
    persisted_order = (
        await async_db_session.execute(select(Order).where(Order.account_id == account_id))
    ).scalar_one()
    persisted_account = await async_db_session.get(Account, account_id)

    assert result["success"] is True
    assert result["status"] == "pending"
    assert persisted_order.status == "pending"
    assert float(persisted_account.available_cash) < 100000
    assert float(persisted_account.frozen_cash) > 0
    service.engine.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_service_cancel_pending_limit_buy_releases_cash(async_db_session):
    service = TradingService()
    account = await _seed_account(async_db_session, user_id=7, cash="98894.98")
    account_id = account.account_id
    user_id = account.user_id
    account.frozen_cash = Decimal("1105.02")
    order = Order(
        order_id=uuid4(),
        account_id=account.account_id,
        stock_code="000001.SZ",
        action="buy",
        order_type="limit",
        price=Decimal("11.00"),
        shares=100,
        filled_shares=0,
        status="pending",
    )
    order_id = order.order_id
    async_db_session.add(order)
    await async_db_session.commit()

    result = await service.cancel_order(order_id, user_id=user_id)

    async_db_session.expire_all()
    persisted_order = await async_db_session.get(Order, order_id)
    persisted_account = await async_db_session.get(Account, account_id)
    assert result["success"] is True
    assert persisted_order.status == "cancelled"
    assert float(persisted_account.available_cash) == pytest.approx(100000.0)
    assert float(persisted_account.frozen_cash) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_service_rejects_limit_buy_stop_loss_above_limit_price(async_db_session):
    service = TradingService()
    service.engine.execute_order = AsyncMock()
    account = await _seed_account(async_db_session)

    result = await service.execute_order_and_update_db(
        session_id=None,
        account_id=account.account_id,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=8.0,
        order_type="limit",
        stop_loss=8.1,
    )

    assert result["success"] is False
    assert result["reason"] == "invalid_buy_stop_loss"
    assert result["price"] == 8.0
    order_count = (await async_db_session.execute(select(func.count()).select_from(Order))).scalar_one()
    assert order_count == 0
    service.engine.execute_order.assert_not_called()


@pytest.mark.asyncio
async def test_service_updates_market_order_price_to_actual_fill(monkeypatch, async_db_session):
    service = TradingService()
    account = await _seed_account(async_db_session)
    service.engine.execute_order = AsyncMock(
        return_value={
            "success": True,
            "message": "Order executed successfully",
            "trade_record": {
                "id": uuid4(),
                "price": 10.23,
                "shares": 100,
                "turnover": 1023.0,
                "commission": 5.0,
                "stamp_duty": 0.0,
                "transfer_fee": 0.02,
                "total_fee": 5.02,
                "net_amount": 1028.02,
            },
            "updated_account": {
                "cash_balance": 98971.98,
                "total_assets": 99994.98,
                "market_value": 1023.0,
                "total_profit_loss": 0.0,
            },
            "updated_position": {
                "current_shares": 100,
                "available_shares": 0,
                "frozen_shares": 100,
                "avg_cost": 10.2802,
                "market_value": 1023.0,
                "unrealized_pnl": -5.02,
                "purchase_details": {"ledger": [{"time": datetime.now().isoformat(), "shares": 100, "price": 10.23}]},
            },
            "executed_shares": 100,
            "remaining_shares": 0,
            "realized_pnl": Decimal("0.00"),
            "order_status": "filled",
        }
    )
    monkeypatch.setattr(
        "app.trading.service.data_storage_service.get_stock_realtime_market",
        AsyncMock(return_value={"latest_price": 10.23}),
    )

    result = await service.execute_order_and_update_db(
        session_id=None,
        account_id=account.account_id,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=0.0,
        order_type="market",
    )

    persisted_order = (
        await async_db_session.execute(select(Order).where(Order.account_id == account.account_id))
    ).scalar_one()
    persisted_trade = (
        await async_db_session.execute(select(TradeRecord).where(TradeRecord.account_id == account.account_id))
    ).scalar_one()
    persisted_position = (
        await async_db_session.execute(select(Position).where(Position.account_id == account.account_id))
    ).scalar_one()

    assert result["success"] is True
    assert float(persisted_order.price) == 10.23
    assert float(persisted_order.avg_fill_price) == 10.23
    assert persisted_order.filled_at is not None
    assert float(persisted_trade.fill_price) == 10.23
    assert persisted_position.purchase_details["ledger"][0]["price"] == 10.23


@pytest.mark.asyncio
async def test_service_blocks_by_risk_control_before_creating_order(monkeypatch, async_db_session):
    service = TradingService()
    service.engine.execute_order = AsyncMock()
    account = await _seed_account(async_db_session)
    risk_result = {
        "enabled": True,
        "passed": False,
        "severity": "block",
        "accepted": [],
        "blocks": [{"rule": "require_stop_loss", "message": "blocked"}],
        "metrics": {},
    }

    async def _block_order(*_args, **_kwargs):
        return risk_result

    monkeypatch.setattr("app.trading.service.portfolio_risk_control_service.evaluate_order", _block_order)

    result = await service.execute_order_and_update_db(
        session_id=None,
        account_id=account.account_id,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=10.0,
        order_type="limit",
    )

    assert result["success"] is False
    assert result["reason"] == "risk_control_blocked"
    assert result["risk_control"] == risk_result
    order_count = (await async_db_session.execute(select(func.count()).select_from(Order))).scalar_one()
    assert order_count == 0
    service.engine.execute_order.assert_not_called()
