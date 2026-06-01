from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.account import Account
from app.models.order import Order
from app.models.position import Position
from app.models.trade_record import TradeRecord
from app.trading.service import TradingService


class _FakeQuery:
    def __init__(self, *, first_result=None, scalar_result=None):
        self._first_result = first_result
        self._scalar_result = scalar_result

    def filter(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._first_result

    def scalar(self):
        return self._scalar_result


class _FakeSession:
    def __init__(self, *, account, position_results, total_mv=0):
        self.account = account
        self.position_results = list(position_results)
        self.total_mv = total_mv
        self.added = []
        self.deleted = []
        self.committed = False

    def query(self, entity, *_args, **_kwargs):
        entity_name = getattr(entity, "__name__", "")
        if entity_name == "Account":
            return _FakeQuery(first_result=self.account)
        if entity_name == "Position":
            result = self.position_results.pop(0) if self.position_results else None
            return _FakeQuery(first_result=result)
        if entity_name == "DebateMessage":
            return _FakeQuery(first_result=None)
        return _FakeQuery(scalar_result=self.total_mv)

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Order) and not getattr(obj, "order_id", None):
            obj.order_id = uuid4()
        if isinstance(obj, TradeRecord) and not getattr(obj, "trade_id", None):
            obj.trade_id = uuid4()

    def flush(self):
        return None

    def refresh(self, _obj):
        return None

    def commit(self):
        self.committed = True

    def delete(self, obj):
        self.deleted.append(obj)


@pytest.mark.asyncio
async def test_service_passes_limit_price_to_engine(monkeypatch):
    service = TradingService()
    service.engine.execute_order = AsyncMock(return_value={"success": False, "message": "rejected"})
    session_id = uuid4()

    account = Account(
        account_id=uuid4(),
        user_id=42,
        available_cash=Decimal("100000"),
        total_assets=Decimal("100000"),
        market_value=Decimal("0"),
        total_profit_loss=Decimal("0"),
    )
    db = _FakeSession(account=account, position_results=[None])

    send_order_status = AsyncMock()
    monkeypatch.setattr("app.trading.service.ws_manager.send_order_status", send_order_status)

    result = await service.execute_order_and_update_db(
        db=db,
        session_id=session_id,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=12.8,
        order_type="limit",
    )

    sent_order = service.engine.execute_order.await_args.args[0]
    assert sent_order["price"] == 12.8
    assert sent_order["order_type"] == "limit"
    assert result["success"] is False


@pytest.mark.asyncio
async def test_service_persists_filled_status_and_real_trade_id(monkeypatch):
    service = TradingService()
    session_id = uuid4()

    account = Account(
        account_id=uuid4(),
        user_id=7,
        available_cash=Decimal("100000"),
        total_assets=Decimal("100000"),
        market_value=Decimal("0"),
        total_profit_loss=Decimal("0"),
    )
    db = _FakeSession(account=account, position_results=[None, None], total_mv=Decimal("5000"))

    service.engine.execute_order = AsyncMock(return_value={
        "success": True,
        "message": "Order executed successfully",
        "trade_record": {
            "id": uuid4(),
            "price": 10.0,
            "shares": 1000,
            "turnover": 10000.0,
            "commission": 5.0,
            "stamp_duty": 0.0,
            "transfer_fee": 0.2,
            "total_fee": 5.2,
            "net_amount": 10005.2,
        },
        "updated_account": {
            "cash_balance": 89994.8,
            "total_assets": 99994.8,
            "market_value": 10000.0,
            "total_profit_loss": 0.0,
        },
        "updated_position": {
            "current_shares": 1000,
            "available_shares": 0,
            "frozen_shares": 1000,
            "avg_cost": 10.0052,
            "market_value": 10000.0,
            "unrealized_pnl": -5.2,
            "purchase_details": {"ledger": [{"time": datetime.now().isoformat(), "shares": 1000, "price": 10.0}]},
        },
        "executed_shares": 1000,
        "remaining_shares": 0,
        "realized_pnl": Decimal("0.00"),
        "order_status": "filled",
    })

    send_order_status = AsyncMock()
    send_position_update = AsyncMock()
    send_trade_executed = AsyncMock()
    monkeypatch.setattr("app.trading.service.ws_manager.send_order_status", send_order_status)
    monkeypatch.setattr("app.trading.service.ws_manager.send_position_update", send_position_update)
    monkeypatch.setattr("app.trading.service.ws_manager.send_trade_executed", send_trade_executed)

    result = await service.execute_order_and_update_db(
        db=db,
        session_id=session_id,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=1000,
        price=10.0,
        order_type="limit",
    )

    persisted_trade = next(obj for obj in db.added if isinstance(obj, TradeRecord))
    ws_payload = send_order_status.await_args.args[1]

    assert result["success"] is True
    assert result["order"].status == "filled"
    assert result["order"].filled_at is not None
    assert result["trade_result"]["trade_record"]["id"] == persisted_trade.trade_id
    assert ws_payload["status"] == "filled"


@pytest.mark.asyncio
async def test_service_sends_position_removed_event_when_sell_clears_position(monkeypatch):
    service = TradingService()

    account = Account(
        account_id=uuid4(),
        available_cash=Decimal("100000"),
        total_assets=Decimal("100000"),
        market_value=Decimal("10000"),
        total_profit_loss=Decimal("0"),
        total_trades=0,
        win_rate=Decimal("0"),
    )
    position = Position(
        position_id=uuid4(),
        account_id=account.account_id,
        stock_code="000001.SZ",
        total_shares=100,
        available_shares=100,
        frozen_shares=0,
        avg_cost=Decimal("10.0000"),
        current_price=Decimal("10.0000"),
        market_value=Decimal("1000.0000"),
        profit_loss=Decimal("0"),
        profit_loss_pct=Decimal("0"),
        purchase_details={"ledger": [{"time": datetime.now().isoformat(), "shares": 100, "price": 10.0}]},
        updated_at=datetime.now(),
    )
    db = _FakeSession(account=account, position_results=[position, None], total_mv=Decimal("0"))

    service.engine.execute_order = AsyncMock(return_value={
        "success": True,
        "message": "Order executed successfully",
        "trade_record": {
            "id": uuid4(),
            "price": 11.0,
            "shares": 100,
            "turnover": 1100.0,
            "commission": 5.0,
            "stamp_duty": 1.1,
            "transfer_fee": 0.02,
            "total_fee": 6.12,
            "net_amount": 1093.88,
        },
        "updated_account": {
            "cash_balance": 101093.88,
            "total_assets": 101093.88,
            "market_value": 0.0,
            "total_profit_loss": 93.88,
        },
        "updated_position": None,
        "executed_shares": 100,
        "remaining_shares": 0,
        "realized_pnl": Decimal("93.88"),
        "order_status": "filled",
    })

    monkeypatch.setattr("app.trading.service.ws_manager.send_order_status", AsyncMock())
    send_position_update = AsyncMock()
    monkeypatch.setattr("app.trading.service.ws_manager.send_position_update", send_position_update)
    monkeypatch.setattr("app.trading.service.ws_manager.send_trade_executed", AsyncMock())

    result = await service.execute_order_and_update_db(
        db=db,
        session_id=None,
        account=account,
        stock_code="000001.SZ",
        action="sell",
        shares=100,
        price=11.0,
        order_type="limit",
    )

    payload = send_position_update.await_args.args[1]
    assert result["success"] is True
    assert payload["removed"] is True
    assert payload["current_shares"] == 0
    assert payload["stock_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_service_updates_market_order_price_to_actual_fill(monkeypatch):
    service = TradingService()

    account = Account(
        account_id=uuid4(),
        available_cash=Decimal("100000"),
        total_assets=Decimal("100000"),
        market_value=Decimal("0"),
        total_profit_loss=Decimal("0"),
    )
    db = _FakeSession(account=account, position_results=[None, None], total_mv=Decimal("1023"))

    service.engine.execute_order = AsyncMock(return_value={
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
    })

    send_order_status = AsyncMock()
    send_trade_executed = AsyncMock()
    monkeypatch.setattr("app.trading.service.ws_manager.send_order_status", send_order_status)
    monkeypatch.setattr("app.trading.service.ws_manager.send_position_update", AsyncMock())
    monkeypatch.setattr("app.trading.service.ws_manager.send_trade_executed", send_trade_executed)

    result = await service.execute_order_and_update_db(
        db=db,
        session_id=None,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=0.0,
        order_type="market",
    )

    assert result["success"] is True
    assert float(result["order"].price) == 10.23
    assert float(result["order"].avg_fill_price) == 10.23
    assert result["order"].filled_at is not None
    assert send_order_status.await_args.args[1]["price"] == 10.23
    assert send_trade_executed.await_args.args[1]["price"] == 10.23


@pytest.mark.asyncio
async def test_service_persists_explicit_stop_loss_from_request(monkeypatch):
    service = TradingService()

    account = Account(
        account_id=uuid4(),
        available_cash=Decimal("100000"),
        total_assets=Decimal("100000"),
        market_value=Decimal("0"),
        total_profit_loss=Decimal("0"),
    )
    db = _FakeSession(account=account, position_results=[None, None], total_mv=Decimal("1023"))

    service.engine.execute_order = AsyncMock(return_value={
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
    })

    monkeypatch.setattr("app.trading.service.ws_manager.send_order_status", AsyncMock())
    monkeypatch.setattr("app.trading.service.ws_manager.send_position_update", AsyncMock())
    monkeypatch.setattr("app.trading.service.ws_manager.send_trade_executed", AsyncMock())

    result = await service.execute_order_and_update_db(
        db=db,
        session_id=None,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=0.0,
        order_type="market",
        stop_loss=9.41,
    )

    persisted_position = next(obj for obj in db.added if isinstance(obj, Position))

    assert result["success"] is True
    assert persisted_position.purchase_details["stop_loss"] == 9.41


@pytest.mark.asyncio
async def test_service_normalizes_decimal_models_for_engine_and_persists_float_results(monkeypatch):
    service = TradingService()

    account = Account(
        account_id=uuid4(),
        available_cash=Decimal("100000.00"),
        total_assets=Decimal("101000.00"),
        market_value=Decimal("1000.00"),
        total_profit_loss=Decimal("12.34"),
    )
    position = Position(
        position_id=uuid4(),
        account_id=account.account_id,
        stock_code="000001.SZ",
        total_shares=100,
        available_shares=100,
        frozen_shares=0,
        avg_cost=Decimal("10.0000"),
        current_price=Decimal("10.0000"),
        market_value=Decimal("1000.0000"),
        profit_loss=Decimal("0"),
        profit_loss_pct=Decimal("0"),
        purchase_details={
            "ledger": [
                {"time": datetime.now().isoformat(), "shares": 100, "price": 10.0, "cost_basis": 10.0}
            ]
        },
        updated_at=datetime.now(),
    )
    db = _FakeSession(account=account, position_results=[position, position], total_mv=Decimal("2200.50"))

    service.engine.execute_order = AsyncMock(return_value={
        "success": True,
        "message": "Order executed successfully",
        "trade_record": {
            "id": uuid4(),
            "price": 12.0,
            "shares": 100,
            "turnover": 1200.0,
            "commission": 5.0,
            "stamp_duty": 0.0,
            "transfer_fee": 0.02,
            "total_fee": 5.02,
            "net_amount": 1205.02,
        },
        "updated_account": {
            "cash_balance": 98794.98,
            "total_assets": 100995.48,
            "market_value": 2200.5,
            "total_profit_loss": 7.82,
        },
        "updated_position": {
            "current_shares": 200,
            "available_shares": 0,
            "frozen_shares": 200,
            "avg_cost": 11.0251,
            "market_value": 2200.5,
            "unrealized_pnl": -5.02,
            "purchase_details": {
                "ledger": [
                    {"time": datetime.now().isoformat(), "shares": 100, "price": 10.0, "cost_basis": 10.0},
                    {"time": datetime.now().isoformat(), "shares": 100, "price": 12.0, "cost_basis": 12.0502},
                ]
            },
        },
        "executed_shares": 100,
        "remaining_shares": 0,
        "realized_pnl": 0.0,
        "order_status": "filled",
    })

    monkeypatch.setattr("app.trading.service.ws_manager.send_order_status", AsyncMock())
    send_position_update = AsyncMock()
    monkeypatch.setattr("app.trading.service.ws_manager.send_position_update", send_position_update)
    monkeypatch.setattr("app.trading.service.ws_manager.send_trade_executed", AsyncMock())

    result = await service.execute_order_and_update_db(
        db=db,
        session_id=None,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=12.0,
        order_type="limit",
    )

    sent_order, sent_account, sent_position = service.engine.execute_order.await_args.args
    ws_payload = send_position_update.await_args.args[1]

    assert result["success"] is True
    assert sent_order["price"] == 12.0
    assert sent_account["cash_balance"] == 100000.0
    assert sent_account["total_assets"] == 101000.0
    assert sent_position["avg_cost"] == 10.0
    assert sent_position["market_value"] == 1000.0
    assert isinstance(position.avg_cost, Decimal)
    assert isinstance(position.current_price, Decimal)
    assert isinstance(position.market_value, Decimal)
    assert isinstance(position.profit_loss, Decimal)
    assert float(position.avg_cost) == pytest.approx(11.0251)
    assert float(position.current_price) == 12.0
    assert float(position.market_value) == 2200.5
    assert float(position.profit_loss) == -5.02
    assert float(account.available_cash) == pytest.approx(98794.98)
    assert float(account.total_assets) == pytest.approx(100995.48)
    assert ws_payload["avg_cost"] == pytest.approx(11.0251)
    assert ws_payload["market_value"] == pytest.approx(2200.5)
    assert ws_payload["unrealized_pnl"] == pytest.approx(-5.02)
