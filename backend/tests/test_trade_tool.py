from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.ai.agentic.tools import execute_trading_order, get_pm_order_type_guidance
import app.core.database as database_module
from app.models.account import Account
from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.models.order import Order
from app.models.position import Position
from app.models.session import Session as DebateSession
from app.models.user import User


def test_portfolio_manager_agent_exposes_order_type_guidance_tool():
    from app.ai.llm_engine.agents.governance import PortfolioManagerAgent

    agent = PortfolioManagerAgent(state={"session_id": str(uuid4())})
    tool_names = {tool.name for tool in agent.tools}

    assert "get_pm_order_type_guidance" in tool_names
    assert "calculate_executable_position_plan" in tool_names
    assert "execute_trading_order" in tool_names


def test_calculate_executable_position_plan_only_exposes_target_position():
    from app.ai.llm_engine.agents.governance import PortfolioManagerAgent

    agent = PortfolioManagerAgent(state={"session_id": str(uuid4())})
    position_tool = next(tool for tool in agent.tools if tool.name == "calculate_executable_position_plan")

    assert set(position_tool.args_schema.model_fields) == {"target_position"}


def test_portfolio_manager_prompt_requires_executable_position_plan_tool():
    from app.ai.llm_engine.prompts.templates import (
        SYSTEM_PROMPT_PORTFOLIO_MANAGER_CN,
        SYSTEM_PROMPT_PORTFOLIO_MANAGER_EN,
    )

    assert "calculate_executable_position_plan(target_position)" in SYSTEM_PROMPT_PORTFOLIO_MANAGER_CN
    assert "最小可执行试探仓" in SYSTEM_PROMPT_PORTFOLIO_MANAGER_CN
    assert "工具默认保留全部待成交订单" in SYSTEM_PROMPT_PORTFOLIO_MANAGER_CN
    assert "calculate_executable_position_plan(target_position)" in SYSTEM_PROMPT_PORTFOLIO_MANAGER_EN
    assert "minimum executable trial position" in SYSTEM_PROMPT_PORTFOLIO_MANAGER_EN
    assert "retains active pending" in SYSTEM_PROMPT_PORTFOLIO_MANAGER_EN


@pytest.mark.asyncio
async def test_get_pm_order_type_guidance_returns_market_during_trading_time(monkeypatch):
    monkeypatch.setattr("app.data.market_utils.is_trading_time", lambda: True)
    monkeypatch.setattr(
        "app.data.storage.data_storage_service.get_stock_realtime_market",
        AsyncMock(return_value={"latest_price": 10.25, "update_time": "2026-06-09 10:00:00"}),
    )

    result = await get_pm_order_type_guidance.ainvoke({"stock_code": "600519.SH"})

    assert result["success"] is True
    assert result["is_trading_time"] is True
    assert result["market_order_allowed"] is True
    assert result["recommended_order_type"] == "market"
    assert result["latest_price"] == 10.25
    assert result["limit_price"] is None


@pytest.mark.asyncio
async def test_get_pm_order_type_guidance_returns_limit_outside_trading_time(monkeypatch):
    monkeypatch.setattr("app.data.market_utils.is_trading_time", lambda: False)
    monkeypatch.setattr(
        "app.data.storage.data_storage_service.get_stock_realtime_market",
        AsyncMock(return_value={"latest_price": Decimal("10.25"), "update_time": "2026-06-08 15:00:00"}),
    )

    result = await get_pm_order_type_guidance.ainvoke({"stock_code": "600519.SH"})

    assert result["success"] is True
    assert result["is_trading_time"] is False
    assert result["market_order_allowed"] is False
    assert result["recommended_order_type"] == "limit"
    assert result["latest_price"] == 10.25
    assert result["limit_price"] == 10.25


@pytest.mark.asyncio
async def test_get_pm_order_type_guidance_requires_latest_price_outside_trading_time(monkeypatch):
    monkeypatch.setattr("app.data.market_utils.is_trading_time", lambda: False)
    monkeypatch.setattr("app.data.storage.data_storage_service.get_stock_realtime_market", AsyncMock(return_value=None))

    result = await get_pm_order_type_guidance.ainvoke({"stock_code": "600519.SH"})

    assert result["success"] is False
    assert result["recommended_order_type"] == "limit"
    assert result["limit_price"] is None
    assert result["reason"] == "latest_price_unavailable"


async def _seed_trade_context(
    session_factory,
    *,
    stock_code: str = "600519.SH",
    total_assets: Decimal = Decimal("1000000.00"),
    available_cash: Decimal | None = None,
    latest_price: Decimal = Decimal("100.00"),
    position_shares: int = 0,
    available_shares: int | None = None,
    order: Order | None = None,
):
    user_id = int(str(uuid4().int)[:8])
    session_id = uuid4()
    account_id = uuid4()
    async with session_factory() as db:
        db.add(
            User(
                id=user_id,
                username=f"trade_tool_{user_id}",
                email=f"trade_tool_{user_id}@example.com",
                password_hash="test",
                is_active=True,
            )
        )
        db.add(
            Account(
                account_id=account_id,
                user_id=user_id,
                total_assets=total_assets,
                available_cash=available_cash if available_cash is not None else total_assets,
                frozen_cash=Decimal("0"),
                market_value=Decimal("0"),
                initial_capital=total_assets,
                total_profit_loss=Decimal("0"),
                profit_loss_pct=Decimal("0"),
                total_trades=0,
                win_rate=Decimal("0"),
            )
        )
        db.add(
            DebateSession(
                session_id=session_id,
                user_id=user_id,
                stock_code=stock_code,
                trading_frequency="daily",
                trading_strategy="value",
            )
        )
        db.add(
            StockBasic(
                stock_code=stock_code,
                name=stock_code,
                market=stock_code.split(".")[-1] if "." in stock_code else None,
            )
        )
        db.add(
            StockRealtimeMarket(
                stock_code=stock_code,
                current_price=float(latest_price),
                timestamp=datetime.now(),
            )
        )
        if position_shares:
            db.add(
                Position(
                    account_id=account_id,
                    stock_code=stock_code,
                    total_shares=position_shares,
                    available_shares=available_shares if available_shares is not None else position_shares,
                    frozen_shares=0,
                    avg_cost=latest_price,
                    current_price=latest_price,
                    market_value=latest_price * position_shares,
                    profit_loss=Decimal("0"),
                    profit_loss_pct=Decimal("0"),
                    purchase_details={},
                )
            )
        if order is not None:
            order.account_id = account_id
            db.add(order)
        await db.commit()
    return session_id, account_id, user_id


async def _wrapped_pm_tool(session_id):
    from app.ai.llm_engine.agents.governance import PortfolioManagerAgent

    agent = PortfolioManagerAgent(state={"session_id": str(session_id)})
    return next(t for t in agent.tools if t.name == "execute_trading_order")


async def _wrapped_position_plan_tool(session_id):
    from app.ai.llm_engine.agents.governance import PortfolioManagerAgent

    agent = PortfolioManagerAgent(state={"session_id": str(session_id)})
    return next(t for t in agent.tools if t.name == "calculate_executable_position_plan")


@pytest.mark.asyncio
async def test_calculate_executable_position_plan_rejects_nominal_position_below_one_lot(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        latest_price=Decimal("1113.00"),
    )
    position_tool = await _wrapped_position_plan_tool(session_id)

    result = await position_tool.ainvoke({"target_position": 0.02})

    assert result["success"] is True
    assert result["executable"] is False
    assert result["reason"] == "below_minimum_buy_lot"
    assert result["action"] == "buy"
    assert result["order_shares"] == 0
    assert result["actual_target_position"] == 0
    assert result["minimum_lot_position"] == pytest.approx(0.1113)


@pytest.mark.asyncio
async def test_calculate_executable_position_plan_returns_rounded_actual_position(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        latest_price=Decimal("123.00"),
    )
    position_tool = await _wrapped_position_plan_tool(session_id)

    result = await position_tool.ainvoke({"target_position": 0.05})

    assert result["success"] is True
    assert result["executable"] is True
    assert result["reason"] is None
    assert result["action"] == "buy"
    assert result["raw_target_shares"] == pytest.approx(406.504065)
    assert result["order_shares"] == 400
    assert result["actual_target_shares"] == 400
    assert result["actual_target_position"] == pytest.approx(0.0492)
    assert result["estimated_fee"] == pytest.approx(10.82)


@pytest.mark.asyncio
async def test_calculate_executable_position_plan_applies_available_shares_to_sell(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        latest_price=Decimal("100.00"),
        position_shares=500,
        available_shares=100,
    )
    position_tool = await _wrapped_position_plan_tool(session_id)

    result = await position_tool.ainvoke({"target_position": 0.02})

    assert result["success"] is True
    assert result["executable"] is True
    assert result["reason"] == "partially_executable_available_shares"
    assert result["action"] == "sell"
    assert result["order_shares"] == 100
    assert result["actual_target_shares"] == 400
    assert result["actual_target_position"] == pytest.approx(0.04)
    assert result["target_fully_reachable"] is False


@pytest.mark.asyncio
async def test_calculate_executable_position_plan_includes_pending_buy_remaining_shares(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        latest_price=Decimal("100.00"),
        available_cash=Decimal("40000.00"),
        position_shares=100,
        available_shares=100,
        order=Order(
            stock_code="600519.SH",
            action="buy",
            order_type="limit",
            price=Decimal("100.00"),
            shares=500,
            filled_shares=100,
            status="pending",
        ),
    )
    position_tool = await _wrapped_position_plan_tool(session_id)

    result = await position_tool.ainvoke({"target_position": 0.05})

    assert result["success"] is True
    assert result["executable"] is False
    assert result["reason"] == "target_covered_by_pending_orders"
    assert result["action"] == "hold"
    assert result["pending_order_policy"] == "retain"
    assert result["pending_buy_shares"] == 400
    assert result["effective_total_shares"] == 500
    assert result["order_shares"] == 0
    assert result["actual_target_shares"] == 500


@pytest.mark.asyncio
async def test_calculate_executable_position_plan_subtracts_pending_sell_reservation(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        latest_price=Decimal("100.00"),
        position_shares=500,
        available_shares=500,
        order=Order(
            stock_code="600519.SH",
            action="sell",
            order_type="limit",
            price=Decimal("100.00"),
            shares=100,
            filled_shares=0,
            status="pending",
        ),
    )
    position_tool = await _wrapped_position_plan_tool(session_id)

    result = await position_tool.ainvoke({"target_position": 0})

    assert result["success"] is True
    assert result["executable"] is True
    assert result["action"] == "sell"
    assert result["pending_order_policy"] == "retain"
    assert result["pending_sell_shares"] == 100
    assert result["current_available_shares"] == 500
    assert result["available_shares_after_pending_orders"] == 400
    assert result["order_shares"] == 400
    assert result["actual_target_shares"] == 0
    assert result["target_fully_reachable"] is True


def _track_tool_db_sessions(monkeypatch, session_factory):
    state = {"active": 0, "exited": 0}

    class TrackingSessionContext:
        def __init__(self):
            self._session_context = session_factory()

        async def __aenter__(self):
            state["active"] += 1
            return await self._session_context.__aenter__()

        async def __aexit__(self, exc_type, exc, tb):
            try:
                return await self._session_context.__aexit__(exc_type, exc, tb)
            finally:
                state["active"] -= 1
                state["exited"] += 1

    monkeypatch.setattr(database_module, "AsyncSessionLocal", TrackingSessionContext)
    return state


@pytest.mark.asyncio
async def test_execute_trading_order_buy_logic(test_db, monkeypatch):
    session_id, _, _ = await _seed_trade_context(test_db)
    wrapped_tool = await _wrapped_pm_tool(session_id)
    session_state = _track_tool_db_sessions(monkeypatch, test_db)

    async def fake_execute_order_and_update_db(**_kwargs):
        assert session_state["active"] == 0
        assert session_state["exited"] == 1
        return {"success": True, "message": "ok"}

    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.side_effect = fake_execute_order_and_update_db

        await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "buy",
            "target_position": 0.5,
            "stop_loss": 95.0,
            "take_profit": 120.0,
        })

    args = mock_execute.call_args.kwargs
    assert str(args["session_id"]) == str(session_id)
    assert args["shares"] == 5000
    assert args["action"] == "buy"
    assert args["stop_loss"] == 95.0


@pytest.mark.asyncio
async def test_execute_trading_order_sell_liquidation(test_db):
    session_id, _, _ = await _seed_trade_context(test_db, position_shares=1234, available_shares=1200)
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}

        await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "sell",
            "target_position": 0.0,
            "stop_loss": 88.0,
            "take_profit": 120.0,
        })

    args = mock_execute.call_args.kwargs
    assert str(args["session_id"]) == str(session_id)
    assert args["shares"] == 1200
    assert args["action"] == "sell"
    assert args["stop_loss"] == 88.0


@pytest.mark.asyncio
async def test_execute_trading_order_sell_liquidation_rounds_down_to_lot_size(test_db):
    session_id, _, _ = await _seed_trade_context(test_db, position_shares=1234, available_shares=1234)
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}

        await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "sell",
            "target_position": 0.0,
            "stop_loss": 88.0,
            "take_profit": 120.0,
        })

    assert mock_execute.call_args.kwargs["shares"] == 1200


@pytest.mark.asyncio
async def test_execute_trading_order_subtracts_pending_sell_reservation(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        position_shares=500,
        available_shares=500,
        order=Order(
            stock_code="600519.SH",
            action="sell",
            order_type="limit",
            price=Decimal("100.00"),
            shares=100,
            filled_shares=0,
            status="pending",
        ),
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}

        result = await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "sell",
            "target_position": 0.0,
            "stop_loss": 88.0,
            "take_profit": 120.0,
        })

    assert result["success"] is True
    assert mock_execute.call_args.kwargs["shares"] == 400


@pytest.mark.asyncio
async def test_execute_trading_order_buy_logic_accepts_decimal_account_assets(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        stock_code="600795.SH",
        total_assets=Decimal("100000.00"),
        latest_price=Decimal("10.00"),
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = {"success": True, "message": "ok"}

        await wrapped_tool.ainvoke({
            "stock_code": "600795.SH",
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 4.7,
            "take_profit": 12.0,
        })

    args = mock_execute.call_args.kwargs
    assert str(args["session_id"]) == str(session_id)
    assert args["stock_code"] == "600795.SH"
    assert args["action"] == "buy"
    assert args["price"] == 10.0
    assert args["shares"] == 500
    assert args["stop_loss"] == 4.7


@pytest.mark.asyncio
async def test_execute_trading_order_places_limit_order_with_limit_price(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        stock_code="600795.SH",
        total_assets=Decimal("1000000.00"),
        latest_price=Decimal("100.00"),
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = {"success": True, "status": "pending", "message": "pending"}

        result = await wrapped_tool.ainvoke({
            "stock_code": "600795.SH",
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 70.0,
            "take_profit": 120.0,
            "order_type": "limit",
            "limit_price": 80.0,
        })

    args = mock_execute.call_args.kwargs
    assert args["order_type"] == "limit"
    assert args["price"] == 80.0
    assert args["shares"] == 500
    assert result["execution_status"] == "pending"


@pytest.mark.asyncio
async def test_execute_trading_order_rejects_limit_buy_stop_loss_above_limit_price(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        stock_code="600795.SH",
        total_assets=Decimal("100000.00"),
        latest_price=Decimal("10.00"),
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        result = await wrapped_tool.ainvoke({
            "stock_code": "600795.SH",
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
async def test_execute_trading_order_cancels_pending_order(test_db, monkeypatch):
    order_uuid = uuid4()
    session_id, _, _ = await _seed_trade_context(
        test_db,
        order=Order(
            order_id=order_uuid,
            stock_code="600519.SH",
            action="buy",
            order_type="limit",
            price=Decimal("10.00"),
            shares=100,
            filled_shares=0,
            status="pending",
        ),
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    session_state = _track_tool_db_sessions(monkeypatch, test_db)

    async def fake_cancel_order(*_args, **_kwargs):
        assert session_state["active"] == 0
        assert session_state["exited"] == 1
        return {"success": True, "message": "cancelled", "order": SimpleNamespace(status="cancelled")}

    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.cancel_order", new_callable=AsyncMock
    ) as mock_cancel:
        mock_cancel.side_effect = fake_cancel_order

        result = await wrapped_tool.ainvoke({
            "operation": "cancel",
            "order_id": str(order_uuid).replace("-", "")[:8],
        })

    mock_cancel.assert_awaited_once()
    assert result["success"] is True
    assert result["execution_status"] == "cancelled"


@pytest.mark.asyncio
async def test_execute_trading_order_skips_when_risk_control_blocks(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        total_assets=Decimal("100000.00"),
        latest_price=Decimal("10.00"),
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    risk_result = {
        "enabled": True,
        "passed": False,
        "severity": "block",
        "accepted": [],
        "blocks": [{"rule": "require_stop_loss", "message": "blocked"}],
        "metrics": {},
    }
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        mock_execute.return_value = {
            "success": False,
            "message": "Order blocked by portfolio risk control",
            "reason": "risk_control_blocked",
            "risk_control": risk_result,
        }

        result = await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
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
async def test_execute_trading_order_rejects_buy_when_target_not_above_current_position(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        total_assets=Decimal("100000.00"),
        latest_price=Decimal("10.00"),
        position_shares=1000,
        available_shares=1000,
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        result = await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "buy",
            "target_position": 0.10,
            "stop_loss": 9.0,
            "take_profit": 12.0,
        })

    mock_execute.assert_not_called()
    assert result["success"] is False
    assert result["reason"] == "decision_target_mismatch"


@pytest.mark.asyncio
async def test_execute_trading_order_rejects_buy_when_take_profit_not_above_price(test_db):
    session_id, _, _ = await _seed_trade_context(
        test_db,
        total_assets=Decimal("100000.00"),
        latest_price=Decimal("10.00"),
    )
    wrapped_tool = await _wrapped_pm_tool(session_id)
    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        result = await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 9.0,
            "take_profit": 10.0,
        })

    mock_execute.assert_not_called()
    assert result["success"] is False
    assert result["reason"] == "invalid_buy_take_profit"


@pytest.mark.asyncio
async def test_execute_trading_order_rejects_invalid_take_profit_before_db_lookup(monkeypatch):
    def _fail_session_local():
        raise AssertionError("invalid request should not open a database session")

    from app.ai.llm_engine.agents.governance import PortfolioManagerAgent

    agent = PortfolioManagerAgent(state={"session_id": str(uuid4())})
    wrapped_tool = next(t for t in agent.tools if t.name == "execute_trading_order")
    monkeypatch.setattr(database_module, "AsyncSessionLocal", _fail_session_local)

    with patch("app.core.config.settings.ENABLE_AUTO_TRADE", True), patch(
        "app.trading.service.trading_service.execute_order_and_update_db", new_callable=AsyncMock
    ) as mock_execute:
        result = await wrapped_tool.ainvoke({
            "stock_code": "600519.SH",
            "action": "buy",
            "target_position": 0.05,
            "stop_loss": 9.0,
            "take_profit": 0,
        })

    mock_execute.assert_not_called()
    assert result["success"] is False
    assert result["reason"] == "Invalid take_profit: 0.0. take_profit must be greater than 0."
