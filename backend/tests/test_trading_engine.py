import pytest
import asyncio
from decimal import Decimal
from uuid import UUID
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from app.trading.trading_engine import TradingEngine

# Mock data
MOCK_ACCOUNT = {
    "cash_balance": 100000.0,
    "total_assets": 100000.0,
    "market_value": 0.0
}

MOCK_POSITION = {
    "id": UUID(int=1),
    "stock_code": "000001.SZ",
    "stock_name": "Ping An Bank",
    "current_shares": 1000,
    "available_shares": 1000,
    "frozen_shares": 0,
    "avg_cost": 10.0,
    "market_value": 10000.0,
    "unrealized_pnl": 0.0,
    "created_at": datetime.now().isoformat(),
    "updated_at": datetime.now().isoformat()
}

@pytest.fixture
def trading_engine():
    return TradingEngine()

class TestTradingEngine:
    def test_build_position_snapshot_derives_share_fields_from_ledger(self, trading_engine):
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        snapshot = trading_engine.build_position_snapshot({
            "stock_code": "000001.SZ",
            "current_shares": 1000,
            "available_shares": 0,
            "frozen_shares": 1000,
            "purchase_details": {
                "ledger": [
                    {"time": yesterday, "shares": 1000, "price": 10.0}
                ],
                "stop_loss": 9.5,
            },
        })

        assert snapshot["available_shares"] == 1000
        assert snapshot["frozen_shares"] == 0
        assert snapshot["purchase_details"]["stop_loss"] == 9.5
        assert snapshot["purchase_details"]["ledger"][0]["shares"] == 1000

    def test_build_position_snapshot_accepts_object_input(self, trading_engine):
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        position = SimpleNamespace(
            total_shares=1000,
            available_shares=0,
            frozen_shares=1000,
            avg_cost=Decimal("10.1200"),
            current_price=Decimal("10.5000"),
            market_value=Decimal("10500.00"),
            purchase_details={
                "ledger": [
                    {"time": yesterday, "shares": 1000, "price": 10.0}
                ]
            },
            profit_loss=Decimal("12.34"),
        )

        snapshot = trading_engine.build_position_snapshot(position)

        assert snapshot["current_shares"] == 1000
        assert snapshot["available_shares"] == 1000
        assert snapshot["frozen_shares"] == 0
        assert snapshot["avg_cost"] == 10.12
        assert snapshot["current_price"] == 10.5
        assert snapshot["market_value"] == 10500.0
        assert snapshot["unrealized_pnl"] == 12.34

    
    # 1. Test Fee Calculation
    def test_calculate_fee_buy(self, trading_engine):
        # Buying 100 shares at 10.0
        # Turnover = 1000
        # Commission = 1000 * 0.0002 = 0.2 < 5 => 5
        # Transfer Fee = 1000 * 0.00002 = 0.02 > 0.01 => 0.02
        # Total = 5 + 0.02 = 5.02
        fees = trading_engine.calculate_fee(10.0, 100, is_buy=True)
        assert fees["commission"] == 5.0
        assert fees["stamp_duty"] == 0.0
        assert fees["transfer_fee"] == 0.02
        assert fees["total_fee"] == 5.02

    def test_calculate_fee_sell(self, trading_engine):
        # Selling 10000 shares at 10.0
        # Turnover = 100000
        # Commission = 100000 * 0.0002 = 20
        # Stamp Duty = 100000 * 0.001 = 100
        # Transfer Fee = 100000 * 0.00002 = 2
        # Total = 20 + 100 + 2 = 122
        fees = trading_engine.calculate_fee(10.0, 10000, is_buy=False)
        assert fees["commission"] == 20.0
        assert fees["stamp_duty"] == 100.0
        assert fees["transfer_fee"] == 2.0
        assert fees["total_fee"] == 122.0

    # 2. Test Order Validity
    def test_check_order_validity_buy_success(self, trading_engine):
        order = {"action": "buy", "shares": 100, "price": 10.0, "order_type": "limit"}
        account = MOCK_ACCOUNT.copy()
        result = trading_engine.check_order_validity(order, account)
        assert result["is_valid"] is True

    def test_check_order_validity_insufficient_funds(self, trading_engine):
        order = {"action": "buy", "shares": 100000, "price": 10.0, "order_type": "limit"} # Need ~1M
        account = MOCK_ACCOUNT.copy() # Has 100k
        result = trading_engine.check_order_validity(order, account)
        assert result["is_valid"] is False
        assert "Insufficient funds" in result["message"]

    def test_check_order_validity_sell_success(self, trading_engine):
        order = {"action": "sell", "shares": 500, "price": 10.0, "order_type": "limit"}
        account = MOCK_ACCOUNT.copy()
        position = MOCK_POSITION.copy()
        result = trading_engine.check_order_validity(order, account, position)
        assert result["is_valid"] is True

    def test_check_order_validity_insufficient_shares(self, trading_engine):
        order = {"action": "sell", "shares": 2000, "price": 10.0, "order_type": "limit"}
        account = MOCK_ACCOUNT.copy()
        position = MOCK_POSITION.copy()
        result = trading_engine.check_order_validity(order, account, position)
        assert result["is_valid"] is False
        assert "Insufficient shares" in result["message"]

    def test_check_order_validity_sell_uses_real_executable_shares(self, trading_engine):
        order = {"action": "sell", "shares": 4500, "price": 10.0, "order_type": "limit"}
        account = MOCK_ACCOUNT.copy()
        position = {
            **MOCK_POSITION.copy(),
            "current_shares": 4400,
            "available_shares": 4500,
            "frozen_shares": -100,
        }
        result = trading_engine.check_order_validity(order, account, position)
        assert result["is_valid"] is False
        assert "Insufficient shares" in result["message"]

    def test_get_sellable_shares_uses_shanghai_trading_date_for_t1(self, trading_engine, monkeypatch):
        shanghai_tz = trading_engine.get_market_timezone()
        monkeypatch.setattr(
            trading_engine,
            "get_market_now",
            lambda: shanghai_tz.localize(datetime(2026, 3, 24, 0, 30, 0)),
        )

        purchase_details = {
            "ledger": [
                {"time": "2026-03-23T17:00:00+00:00", "shares": 100, "price": 10.0},
                {"time": "2026-03-23T15:00:00+00:00", "shares": 200, "price": 10.0},
            ]
        }

        sellable = trading_engine.get_sellable_shares(purchase_details)

        assert sellable == 200

    def test_should_auto_sell_uses_explicit_stop_loss_price(self, trading_engine):
        position = {
            **MOCK_POSITION.copy(),
            "purchase_details": {
                "ledger": [],
                "stop_loss": 9.8,
            },
        }

        result = trading_engine.should_auto_sell(9.7, position)

        assert result["should_sell"] is True
        assert result["reason"] == "stop_loss"
        assert "9.8" in result["message"]

    def test_should_auto_sell_falls_back_to_percentage_when_stop_loss_missing(self, trading_engine):
        position = {
            **MOCK_POSITION.copy(),
            "purchase_details": {
                "ledger": [],
            },
        }

        result = trading_engine.should_auto_sell(9.4, position)

        assert result["should_sell"] is True
        assert result["reason"] == "stop_loss"
        assert "5.0% loss" in result["message"]

    # 3. Test Execution (Async)
    @pytest.mark.asyncio
    async def test_execute_buy_order(self, trading_engine):
        import random
        random.seed(0)
        order = {
            "id": UUID(int=999),
            "stock_code": "000001.SZ", 
            "stock_name": "Ping An", 
            "action": "buy", 
            "shares": 1000, 
            "price": 10.0, 
            "order_type": "limit"
        }
        account = MOCK_ACCOUNT.copy()
        
        # We need to mock the random fill simulation inside execute_order to ensure full fill for testing math
        # Or we verify the logic based on 'executed_shares' returned
        
        result = await trading_engine.execute_order(order, account, None)
        
        assert result["success"] is True
        executed_shares = result["executed_shares"]
        
        # Verify Account Update
        # Fee for 1000 * 10 = 10000 turnover
        # Comm=5, Trans=0.2 => 5.2
        # Cost = 10000 + 5.2 = 10005.2 (if full fill)
        
        if executed_shares == 1000:
            expected_fee = 5.2
            expected_cost = 10000 + expected_fee
            assert result["updated_account"]["cash_balance"] == 100000.0 - expected_cost
            
            # Verify Position Creation
            pos = result["updated_position"]
            assert pos["current_shares"] == 1000
            assert pos["frozen_shares"] == 1000 # T+1: bought shares are frozen
            assert pos["available_shares"] == 0
            assert pos["avg_cost"] == 10.0052 # Cost per share included fees roughly 10.01
            assert pos["purchase_details"]["ledger"][0]["cost_basis"] == 10.0052

    @pytest.mark.asyncio
    async def test_execute_sell_order_t_plus_1(self, trading_engine):
        # Scenario: Position has 1000 shares, but all are frozen (bought today)
        position = MOCK_POSITION.copy()
        position["available_shares"] = 0
        position["frozen_shares"] = 1000
        
        order = {
            "id": UUID(int=998),
            "stock_code": "000001.SZ", 
            "action": "sell", 
            "shares": 500, 
            "price": 11.0, 
            "order_type": "limit"
        }
        account = MOCK_ACCOUNT.copy()
        
        # Should execute but 0 shares filled because of availability check inside execute_order
        result = await trading_engine.execute_order(order, account, position)
        
        # Based on current logic, it might return success=False or partial fill 0
        # Let's check the code path in trading_engine.py...
        # It checks available_shares limit. If executed_shares > available_shares(=0) => executed_shares = 0
        # If executed_shares < MIN_TRANSACTION(100) => returns error "Insufficient available shares (T+1 rule)"
        
        assert result["success"] is False
        assert "Insufficient available shares" in result["message"]

    @pytest.mark.asyncio
    async def test_execute_sell_order_syncs_position_share_fields(self, trading_engine, monkeypatch):
        monkeypatch.setattr("random.uniform", lambda _a, _b: 1.0)

        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        position = {
            **MOCK_POSITION.copy(),
            "current_shares": 4400,
            "available_shares": 4500,
            "frozen_shares": -100,
            "purchase_details": {
                "ledger": [
                    {"time": yesterday, "shares": 4500, "price": 10.0}
                ]
            },
        }
        order = {
            "id": UUID(int=997),
            "stock_code": "000001.SZ",
            "action": "sell",
            "shares": 100,
            "price": 11.0,
            "order_type": "limit"
        }
        account = MOCK_ACCOUNT.copy()

        result = await trading_engine.execute_order(order, account, position)

        assert result["success"] is True
        assert result["executed_shares"] == 100
        assert result["updated_position"]["current_shares"] == 4300
        assert result["updated_position"]["available_shares"] == 4300
        assert result["updated_position"]["frozen_shares"] == 0
        assert result["updated_position"]["purchase_details"]["ledger"][0]["shares"] == 4400

    @pytest.mark.asyncio
    async def test_execute_sell_order_uses_ledger_when_available_shares_is_stale(self, trading_engine, monkeypatch):
        monkeypatch.setattr("random.uniform", lambda _a, _b: 1.0)

        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        position = {
            **MOCK_POSITION.copy(),
            "current_shares": 1000,
            "available_shares": 0,
            "frozen_shares": 1000,
            "purchase_details": {
                "ledger": [
                    {"time": yesterday, "shares": 1000, "price": 10.0}
                ]
            },
        }
        order = {
            "id": UUID(int=996),
            "stock_code": "000001.SZ",
            "action": "sell",
            "shares": 1000,
            "price": 11.0,
            "order_type": "limit"
        }
        account = MOCK_ACCOUNT.copy()

        result = await trading_engine.execute_order(order, account, position)

        assert result["success"] is True
        assert result["executed_shares"] == 1000
        assert result["updated_position"] is None

    @pytest.mark.asyncio
    async def test_market_order_fails_when_market_price_unavailable(self, trading_engine, monkeypatch):
        monkeypatch.setattr("app.data.storage.data_storage_service.get_stock_realtime_market", lambda _code: None)

        order = {
            "stock_code": "000001.SZ",
            "action": "buy",
            "shares": 100,
            "price": 0,
            "order_type": "market"
        }

        result = await trading_engine.execute_order(order, MOCK_ACCOUNT.copy(), None)

        assert result["success"] is False
        assert "Market price unavailable" in result["message"]

    @pytest.mark.asyncio
    async def test_market_order_ignores_client_price_when_market_price_unavailable(self, trading_engine, monkeypatch):
        monkeypatch.setattr("app.data.storage.data_storage_service.get_stock_realtime_market", lambda _code: None)

        order = {
            "stock_code": "000001.SZ",
            "action": "buy",
            "shares": 100,
            "price": 12.34,
            "order_type": "market"
        }

        result = await trading_engine.execute_order(order, MOCK_ACCOUNT.copy(), None)

        assert result["success"] is False
        assert "Market price unavailable" in result["message"]

    @pytest.mark.asyncio
    async def test_execute_sell_order_realized_pnl_includes_sell_fees(self, trading_engine, monkeypatch):
        monkeypatch.setattr("random.uniform", lambda _a, _b: 1.0)

        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        position = {
            **MOCK_POSITION.copy(),
            "current_shares": 100,
            "available_shares": 100,
            "frozen_shares": 0,
            "avg_cost": 10.0,
            "purchase_details": {
                "ledger": [
                    {"time": yesterday, "shares": 100, "price": 10.0}
                ]
            },
        }
        order = {
            "stock_code": "000001.SZ",
            "action": "sell",
            "shares": 100,
            "price": 11.0,
            "order_type": "limit"
        }

        result = await trading_engine.execute_order(order, MOCK_ACCOUNT.copy(), position)

        assert result["success"] is True
        assert result["executed_shares"] == 100
        assert result["realized_pnl"] == pytest.approx(93.88)
        assert result["updated_account"]["total_profit_loss"] == pytest.approx(93.88)

    @pytest.mark.asyncio
    async def test_execute_sell_order_realized_pnl_uses_fifo_cost_basis(self, trading_engine, monkeypatch):
        monkeypatch.setattr("random.uniform", lambda _a, _b: 1.0)

        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        position = {
            **MOCK_POSITION.copy(),
            "current_shares": 200,
            "available_shares": 200,
            "frozen_shares": 0,
            "avg_cost": 11.5,
            "purchase_details": {
                "ledger": [
                    {"time": yesterday, "shares": 100, "price": 10.0, "cost_basis": 10.05},
                    {"time": yesterday, "shares": 100, "price": 12.0, "cost_basis": 12.03},
                ]
            },
        }
        order = {
            "stock_code": "000001.SZ",
            "action": "sell",
            "shares": 100,
            "price": 11.0,
            "order_type": "limit"
        }

        result = await trading_engine.execute_order(order, MOCK_ACCOUNT.copy(), position)

        assert result["success"] is True
        assert result["executed_shares"] == 100
        assert result["realized_pnl"] == pytest.approx(88.88)
        assert result["updated_position"]["purchase_details"]["ledger"] == [
            {"time": yesterday, "shares": 100, "price": 12.0, "cost_basis": 12.03}
        ]

    @pytest.mark.asyncio
    async def test_execute_buy_order_accepts_decimal_position_values(self, trading_engine):
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        position = {
            **MOCK_POSITION.copy(),
            "current_shares": 100,
            "available_shares": 100,
            "frozen_shares": 0,
            "avg_cost": Decimal("10.0000"),
            "current_price": Decimal("10.0000"),
            "market_value": Decimal("1000.0000"),
            "unrealized_pnl": Decimal("0"),
            "purchase_details": {
                "ledger": [
                    {"time": yesterday, "shares": 100, "price": 10.0, "cost_basis": 10.0}
                ]
            },
        }
        order = {
            "stock_code": "000001.SZ",
            "action": "buy",
            "shares": 100,
            "price": 12.0,
            "order_type": "limit"
        }

        result = await trading_engine.execute_order(order, MOCK_ACCOUNT.copy(), position)

        assert result["success"] is True
        assert result["updated_position"]["current_shares"] == 200
        assert result["updated_position"]["avg_cost"] == pytest.approx(11.0251)

    @pytest.mark.asyncio
    async def test_execute_buy_order_accepts_decimal_account_and_position_values(self, trading_engine):
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        account = {
            "cash_balance": Decimal("100000.00"),
            "total_assets": Decimal("100000.00"),
            "market_value": Decimal("0"),
            "total_profit_loss": Decimal("0"),
        }
        position = {
            **MOCK_POSITION.copy(),
            "current_shares": 100,
            "available_shares": 100,
            "frozen_shares": 0,
            "avg_cost": Decimal("10.0000"),
            "current_price": Decimal("10.0000"),
            "market_value": Decimal("1000.0000"),
            "unrealized_pnl": Decimal("0"),
            "purchase_details": {
                "ledger": [
                    {"time": yesterday, "shares": 100, "price": 10.0, "cost_basis": 10.0}
                ]
            },
        }
        order = {
            "stock_code": "000001.SZ",
            "action": "buy",
            "shares": 100,
            "price": Decimal("12.0"),
            "order_type": "limit"
        }

        result = await trading_engine.execute_order(order, account, position)

        assert result["success"] is True
        assert result["updated_account"]["cash_balance"] == pytest.approx(98794.98)
        assert result["updated_position"]["current_shares"] == 200
        assert result["updated_position"]["avg_cost"] == pytest.approx(11.0251)

    @pytest.mark.asyncio
    async def test_execute_limit_order_returns_filled_status(self, trading_engine):
        order = {
            "stock_code": "000001.SZ",
            "action": "buy",
            "shares": 1000,
            "price": 10.0,
            "order_type": "limit"
        }

        result = await trading_engine.execute_order(order, MOCK_ACCOUNT.copy(), None)

        assert result["success"] is True
        assert result["executed_shares"] == 1000
        assert result["remaining_shares"] == 0
        assert result["order_status"] == "filled"
        assert result["message"] == "Order executed successfully"
