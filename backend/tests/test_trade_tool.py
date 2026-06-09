import sys
import os
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from uuid import uuid4

# Set up path to import from backend
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.ai.agentic.tools import execute_trading_order

@pytest.mark.asyncio
async def test_execute_trading_order_buy_logic():
    # Mock dependencies
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        
        session_id = str(uuid4())
        stock_code = "600519.SH"
        
        # Instantiate PM Agent with state
        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        
        # Get the dynamically wrapped tool
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")
        
        # Mock Session, User, Account, Market
        mock_session_model = MagicMock()
        mock_user = MagicMock()
        mock_market = MagicMock()
        
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value
        
        mock_filter.first.side_effect = [mock_session_model, mock_user, None]
        mock_order_by.first.side_effect = [mock_market]
        
        # Setup mocks
        mock_user.account.total_assets = 1000000
        mock_market.current_price = 100.0
        
        # Invoke wrapped tool (without session_id)
        res = await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "buy",
            "target_position": 0.5,
            "stop_loss": 95.0,
            "take_profit": 120.0,
        })
        
        # Verify call to core tool happened with injected session_id
        mock_execute.assert_called_once()
        args = mock_execute.call_args[1]
        assert str(args["session_id"]) == session_id
        assert args["shares"] == 5000
        assert args["stop_loss"] == 95.0
        
        # Verify rounding to 100 and shares calculation
        mock_execute.assert_called_once()
        args = mock_execute.call_args[1]
        assert args["shares"] == 5000
        assert args["action"] == "buy"
        assert args["stop_loss"] == 95.0

@pytest.mark.asyncio
async def test_execute_trading_order_sell_liquidation():
    # Mock dependencies
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        
        session_id = str(uuid4())
        stock_code = "600519.SH"
        
        # Instantiate PM Agent with state
        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        
        # Get the dynamically wrapped tool
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")
        
        # Mock Session, User, Market, Position
        mock_user = MagicMock()
        mock_market = MagicMock()
        mock_pos = MagicMock()
        mock_pos.total_shares = 1234
        mock_pos.available_shares = 1200
        
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value
        
        mock_filter.first.side_effect = [MagicMock(), mock_user, mock_pos]
        mock_order_by.first.side_effect = [mock_market]
        
        # Setup prices
        mock_market.current_price = 100.0
        
        # Test Sell: Target position 0 (liquidation)
        res = await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "sell",
            "target_position": 0.0,
            "stop_loss": 88.0,
            "take_profit": 120.0,
        })
        
        # Verify sell all available
        mock_execute.assert_called_once()
        args = mock_execute.call_args[1]
        assert str(args["session_id"]) == session_id
        assert args["shares"] == 1200
        assert args["action"] == "sell"
        assert args["stop_loss"] == 88.0

@pytest.mark.asyncio
async def test_execute_trading_order_sell_liquidation_rounds_down_to_lot_size():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db

        session_id = str(uuid4())
        stock_code = "600519.SH"

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_user = MagicMock()
        mock_market = MagicMock()
        mock_pos = MagicMock()
        mock_pos.total_shares = 1234
        mock_pos.available_shares = 1234
        mock_pos.frozen_shares = 0
        mock_pos.purchase_details = {}

        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value

        mock_filter.first.side_effect = [MagicMock(), mock_user, mock_pos]
        mock_order_by.first.side_effect = [mock_market]
        mock_market.current_price = 100.0

        await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "sell",
            "target_position": 0.0,
            "stop_loss": 88.0,
            "take_profit": 120.0,
        })

        args = mock_execute.call_args[1]
        assert args["shares"] == 1200


@pytest.mark.asyncio
async def test_execute_trading_order_buy_logic_accepts_decimal_account_assets():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db

        session_id = str(uuid4())
        stock_code = "600795.SH"

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_session_model = MagicMock()
        mock_user = MagicMock()
        mock_market = MagicMock()

        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value

        mock_filter.first.side_effect = [mock_session_model, mock_user, None]
        mock_order_by.first.side_effect = [mock_market]

        mock_user.account.total_assets = Decimal("100000.00")
        mock_user.account.available_cash = Decimal("100000.00")
        mock_market.current_price = Decimal("10.00")

        await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 4.7,
            "take_profit": 12.0,
        })

        args = mock_execute.call_args[1]
        assert str(args["session_id"]) == session_id
        assert args["stock_code"] == stock_code
        assert args["action"] == "buy"
        assert args["price"] == 10.0
        assert args["shares"] == 500
        assert args["stop_loss"] == 4.7


@pytest.mark.asyncio
async def test_execute_trading_order_places_limit_order_with_limit_price():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_execute.return_value = {"success": True, "status": "pending", "message": "pending"}
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db

        session_id = str(uuid4())
        stock_code = "600795.SH"

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_user = MagicMock()
        mock_market = MagicMock()
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value
        mock_filter.first.side_effect = [MagicMock(), mock_user, None]
        mock_order_by.first.side_effect = [mock_market]
        mock_user.account.total_assets = Decimal("100000.00")
        mock_market.current_price = Decimal("10.00")

        result = await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 4.7,
            "take_profit": 12.0,
            "order_type": "limit",
            "limit_price": 9.8,
        })

        args = mock_execute.call_args[1]
        assert args["order_type"] == "limit"
        assert args["price"] == 9.8
        assert result["execution_status"] == "pending"


@pytest.mark.asyncio
async def test_execute_trading_order_rejects_limit_buy_stop_loss_above_limit_price():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db

        session_id = str(uuid4())
        stock_code = "600795.SH"

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_user = MagicMock()
        mock_market = MagicMock()
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value
        mock_filter.first.side_effect = [MagicMock(), mock_user, None]
        mock_order_by.first.side_effect = [mock_market]
        mock_user.account.total_assets = Decimal("100000.00")
        mock_market.current_price = Decimal("10.00")

        result = await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 8.1,
            "take_profit": 9.0,
            "order_type": "limit",
            "limit_price": 8.0,
        })

        mock_execute.assert_not_called()
        assert result["success"] is False
        assert result["reason"] == "invalid_buy_stop_loss"


@pytest.mark.asyncio
async def test_execute_trading_order_cancels_pending_order():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.cancel_order", new_callable=AsyncMock) as mock_cancel:
        mock_cancel.return_value = {"success": True, "message": "cancelled", "order": MagicMock(status="cancelled")}
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db

        session_id = str(uuid4())
        order_uuid = uuid4()
        order_id = str(order_uuid).replace("-", "")[:8]

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_user = MagicMock()
        mock_order = MagicMock()
        mock_order.order_id = order_uuid
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_filter.first.side_effect = [MagicMock(), mock_user]
        mock_filter.all.return_value = [mock_order]

        result = await wrapped_tool.ainvoke({
            "operation": "cancel",
            "order_id": order_id,
        })

        mock_cancel.assert_awaited_once()
        assert result["success"] is True
        assert result["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_execute_trading_order_skips_when_risk_control_blocks():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        risk_result = {
            "enabled": True,
            "passed": False,
            "severity": "block",
            "accepted": [],
            "blocks": [{"rule": "require_stop_loss", "message": "blocked"}],
            "metrics": {},
        }
        mock_execute.return_value = {
            "success": False,
            "message": "Order blocked by portfolio risk control",
            "reason": "risk_control_blocked",
            "risk_control": risk_result,
        }

        session_id = str(uuid4())
        stock_code = "600519.SH"

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_session_model = MagicMock()
        mock_user = MagicMock()
        mock_market = MagicMock()
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value

        mock_filter.first.side_effect = [mock_session_model, mock_user, None]
        mock_order_by.first.side_effect = [mock_market]
        mock_user.account.total_assets = Decimal("100000.00")
        mock_user.account.available_cash = Decimal("100000.00")
        mock_market.current_price = Decimal("10.00")

        result = await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 9.0,
            "take_profit": 12.0,
        })

        mock_execute.assert_called_once()
        assert result["success"] is False
        assert result["reason"] == "risk_control_blocked"
        assert result["risk_control"]["blocks"][0]["rule"] == "require_stop_loss"


@pytest.mark.asyncio
async def test_execute_trading_order_rejects_buy_when_target_not_above_current_position():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        session_id = str(uuid4())
        stock_code = "600519.SH"

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_user = MagicMock()
        mock_market = MagicMock()
        mock_pos = MagicMock()
        mock_pos.total_shares = 1000
        mock_pos.available_shares = 1000
        mock_pos.frozen_shares = 0
        mock_pos.purchase_details = {}

        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value
        mock_filter.first.side_effect = [MagicMock(), mock_user, mock_pos]
        mock_order_by.first.side_effect = [mock_market]
        mock_user.account.total_assets = Decimal("100000.00")
        mock_market.current_price = Decimal("10.00")

        result = await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "buy",
            "target_position": 0.10,
            "stop_loss": 9.0,
            "take_profit": 12.0,
        })

        mock_execute.assert_not_called()
        assert result["success"] is False
        assert result["reason"] == "decision_target_mismatch"


@pytest.mark.asyncio
async def test_execute_trading_order_rejects_buy_when_take_profit_not_above_price():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
        session_id = str(uuid4())
        stock_code = "600519.SH"

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        mock_user = MagicMock()
        mock_market = MagicMock()
        mock_query = mock_db.query.return_value
        mock_filter = mock_query.filter.return_value
        mock_order_by = mock_filter.order_by.return_value
        mock_filter.first.side_effect = [MagicMock(), mock_user, None]
        mock_order_by.first.side_effect = [mock_market]
        mock_user.account.total_assets = Decimal("100000.00")
        mock_market.current_price = Decimal("10.00")

        result = await wrapped_tool.ainvoke({
            "stock_code": stock_code,
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 9.0,
            "take_profit": 10.0,
        })

        mock_execute.assert_not_called()
        assert result["success"] is False
        assert result["reason"] == "invalid_buy_take_profit"


@pytest.mark.asyncio
async def test_execute_trading_order_rejects_invalid_take_profit_before_db_lookup():
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock) as mock_execute:
        session_id = str(uuid4())

        from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
        agent = PortfolioManagerAgent(state={"session_id": session_id})
        wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")

        result = await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 9.0,
            "take_profit": 0,
        })

        mock_session_local.assert_not_called()
        mock_execute.assert_not_called()
        assert result["success"] is False
        assert result["reason"] == "Invalid take_profit: 0.0. take_profit must be greater than 0."

if __name__ == "__main__":
    asyncio.run(test_execute_trading_order_buy_logic())
    asyncio.run(test_execute_trading_order_sell_liquidation())
    print("Tests passed!")
