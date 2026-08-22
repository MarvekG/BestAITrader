from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.ai.llm_engine.decision_snapshot import (
    DecisionSnapshot,
    DecisionSnapshotError,
    PendingOrderSnapshot,
    PositionSnapshot,
    RealtimeMarketSnapshot,
    build_decision_snapshot,
)
from app.models.account import Account
from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.models.order import Order
from app.models.position import Position
from app.models.session import Session as DebateSession
from app.models.user import User


@pytest.mark.asyncio
async def test_build_decision_snapshot_uses_one_dynamic_decision_input(async_db_session):
    user = User(
        username="memory_snapshot_user",
        email="memory_snapshot_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()

    account = Account(
        user_id=user.id,
        total_assets=Decimal("100000.00"),
        available_cash=Decimal("50000.00"),
        frozen_cash=Decimal("0.00"),
        market_value=Decimal("10000.00"),
        initial_capital=Decimal("100000.00"),
    )
    session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="active",
    )
    async_db_session.add_all(
        [
            account,
            session,
            StockBasic(stock_code="000001.SZ", name="Ping An Bank"),
        ]
    )
    await async_db_session.flush()
    async_db_session.add_all(
        [
            Position(
                account_id=account.account_id,
                stock_code="000001.SZ",
                total_shares=1000,
                available_shares=0,
                frozen_shares=1000,
                avg_cost=Decimal("10.00"),
                current_price=Decimal("10.00"),
                market_value=Decimal("10000.00"),
                profit_loss=Decimal("0.00"),
                profit_loss_pct=Decimal("0.00"),
                purchase_details={
                    "stop_loss": 9.5,
                    "ledger": [
                        {
                            "time": "2020-01-01T10:00:00",
                            "shares": 1000,
                            "price": 10.0,
                        }
                    ]
                },
            ),
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=Decimal("12.00"),
                timestamp=datetime(2026, 8, 22, 10, 0),
            ),
            Order(
                account_id=account.account_id,
                stock_code="000001.SZ",
                action="buy",
                order_type="limit",
                price=Decimal("11.50"),
                shares=500,
                filled_shares=100,
                status="pending",
            ),
        ]
    )
    await async_db_session.commit()

    snapshot = await build_decision_snapshot(session_id=session.session_id)

    assert snapshot.reference_price == Decimal("12.00")
    assert snapshot.account_total_assets == Decimal("62000.00")
    assert snapshot.available_cash == Decimal("50000.00")
    assert snapshot.portfolio_market_value == Decimal("12000.00")
    assert snapshot.position.total_shares == 1000
    assert snapshot.position.available_shares == 1000
    assert snapshot.position.frozen_shares == 0
    assert snapshot.position.market_value == Decimal("12000.00")
    assert snapshot.position.stop_loss == Decimal("9.5")
    assert snapshot.position.has_stop_loss is True
    assert snapshot.portfolio_positions == (snapshot.position,)
    assert snapshot.realtime_market is not None
    assert snapshot.realtime_market.price == Decimal("12.00")
    assert snapshot.pending_buy_shares == 400
    assert snapshot.pending_sell_shares == 0

    with pytest.raises(FrozenInstanceError):
        snapshot.reference_price = Decimal("13.00")


@pytest.mark.asyncio
async def test_fetch_context_reuses_snapshot_for_sensitive_portfolio_fields():
    from app.ai.llm_engine.orchestrator import fetch_context

    session_id = uuid4()
    target_position = PositionSnapshot(
        total_shares=1000,
        available_shares=800,
        frozen_shares=200,
        avg_cost=Decimal("10.00"),
        market_value=Decimal("12000.00"),
        unrealized_pnl=Decimal("2000.00"),
        unrealized_pnl_pct=Decimal("0.20"),
        weight=Decimal("12000") / Decimal("72000"),
        stock_code="000001.SZ",
        stock_name="Ping An Bank",
        industry="Bank",
        current_price=Decimal("12.00"),
        stop_loss=Decimal("9.50"),
        has_stop_loss=True,
    )
    other_position = PositionSnapshot(
        total_shares=500,
        available_shares=500,
        frozen_shares=0,
        avg_cost=Decimal("18.00"),
        market_value=Decimal("10000.00"),
        unrealized_pnl=Decimal("1000.00"),
        unrealized_pnl_pct=Decimal("1") / Decimal("9"),
        weight=Decimal("10000") / Decimal("72000"),
        stock_code="600519.SH",
        stock_name="Kweichow Moutai",
        industry="Liquor",
        current_price=Decimal("20.00"),
    )
    snapshot = DecisionSnapshot(
        snapshot_id=uuid4(),
        session_id=session_id,
        user_id=7,
        stock_code="000001.SZ",
        generated_at=datetime(2026, 8, 22, 10, 0),
        reference_price=Decimal("12.00"),
        price_source="realtime",
        price_as_of=datetime(2026, 8, 22, 9, 59),
        account_total_assets=Decimal("72000.00"),
        available_cash=Decimal("50000.00"),
        portfolio_market_value=Decimal("22000.00"),
        position=target_position,
        pending_buy_shares=400,
        pending_sell_shares=0,
        frozen_cash=Decimal("0.00"),
        portfolio_positions=(target_position, other_position),
        realtime_market=RealtimeMarketSnapshot(
            price=Decimal("12.00"),
            timestamp=datetime(2026, 8, 22, 9, 59),
            pct_chg=Decimal("1.25"),
            turnover_rate=None,
            volume_ratio=None,
            amplitude=None,
            pb=None,
            pe=None,
            amount=None,
            volume=None,
            turnover=None,
            total_market_cap=None,
            circulating_market_cap=None,
        ),
    )
    state = {
        "stock_code": "000001.SZ",
        "session_id": session_id,
        "decision_snapshot": snapshot,
        "static_context": {},
    }
    context = {
        "portfolio": {
            "status": "missing",
            "reason": "current_user_unavailable",
            "overview": {
                "summary": {
                    "total_assets": 100000,
                    "available_cash": 90000,
                    "market_value": 10000,
                },
                "positions": [
                    {
                        "stock_code": "000001.SZ",
                        "current_price": "13元",
                        "stop_loss": "9.5元",
                        "has_stop_loss": True,
                    },
                    {
                        "stock_code": "600519.SH",
                        "current_price": "25元",
                    },
                ],
                "top_weights": [{"stock_code": "600519.SH"}],
                "industry_allocations": [{"industry": "Old"}],
                "risk_metrics": {"top_single_position_stock_code": "600519.SH"},
            },
            "performance": {"available_cash": "90000元"},
        },
        "realtime": {
            "market": {"data_status": "available", "price": "13元", "timestamp": "2026-08-22T10:01:00"},
            "price_position_summary": {"status": "available", "price_vs_ma5_pct": "10%"},
            "technical_signal_summary": {"status": "available", "ma_alignment": "bullish"},
            "intraday_shape_summary": {"status": "available", "current_price": "13元"},
        },
    }

    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_service:
        mock_service.return_value.build = AsyncMock(return_value=context)
        result = await fetch_context(state)

    assert result["decision_snapshot"] is snapshot
    assert "decision_snapshot" not in result["static_context"]
    overview = result["static_context"]["data"]["portfolio"]["overview"]
    assert result["static_context"]["data"]["portfolio"]["status"] == "available"
    assert "reason" not in result["static_context"]["data"]["portfolio"]
    assert result["static_context"]["data"]["portfolio"]["performance"] == {
        "status": "stale",
        "reason": "decision_snapshot_portfolio_state",
    }
    assert overview["summary"]["total_assets"] == "72000元"
    assert overview["summary"]["cash_ratio"] == "69.44%"
    assert overview["summary"]["position_ratio"] == "30.56%"
    assert overview["positions"][0]["stock_code"] == "000001.SZ"
    assert overview["positions"][0]["current_price"] == "12元"
    assert overview["positions"][0]["stop_loss"] == "9.5元"
    assert overview["positions"][0]["has_stop_loss"] is True
    assert overview["top_weights"][0]["stock_code"] == "000001.SZ"
    assert overview["industry_allocations"][0]["industry"] == "Bank"
    assert overview["risk_metrics"]["top_single_position_stock_code"] == "000001.SZ"
    assert result["static_context"]["portfolio_info"]["account"]["total_assets"] == "72000元"
    assert result["static_context"]["portfolio_info"]["position"]["current_price"] == "12元"
    assert result["static_context"]["portfolio_info"]["position"]["stop_loss"] == "9.5元"
    realtime = result["static_context"]["data"]["realtime"]
    assert realtime["market"]["price"] == "12元"
    assert realtime["market"]["timestamp"] == "2026-08-22T09:59:00"
    assert realtime["market"]["data_status"] == "available"
    assert realtime["price_position_summary"]["status"] == "stale"
    assert realtime["technical_signal_summary"]["status"] == "stale"
    assert realtime["intraday_shape_summary"]["status"] == "stale"


@pytest.mark.asyncio
async def test_fetch_context_builds_snapshot_once_when_state_has_empty_slot():
    from app.ai.llm_engine.orchestrator import fetch_context

    session_id = uuid4()
    snapshot = DecisionSnapshot(
        snapshot_id=uuid4(),
        session_id=session_id,
        user_id=7,
        stock_code="000001.SZ",
        generated_at=datetime(2026, 8, 22, 10, 0),
        reference_price=Decimal("12.00"),
        price_source="realtime",
        price_as_of=None,
        account_total_assets=Decimal("100000.00"),
        available_cash=Decimal("100000.00"),
        portfolio_market_value=Decimal("0.00"),
        position=PositionSnapshot(0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        pending_buy_shares=0,
        pending_sell_shares=0,
    )
    state = {
        "stock_code": "000001.SZ",
        "session_id": session_id,
        "decision_snapshot": None,
        "static_context": {},
    }

    with (
        patch("app.ai.llm_engine.orchestrator.build_decision_snapshot", new_callable=AsyncMock, return_value=snapshot) as build_mock,
        patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_service,
    ):
        mock_service.return_value.build = AsyncMock(return_value={})
        result = await fetch_context(state)

    build_mock.assert_awaited_once_with(session_id=session_id)
    assert result["decision_snapshot"] is snapshot


@pytest.mark.asyncio
async def test_fetch_context_builds_snapshot_when_internal_field_is_missing():
    from app.ai.llm_engine.orchestrator import fetch_context

    session_id = uuid4()
    snapshot = _empty_snapshot(session_id=session_id)
    state = {
        "stock_code": "000001.SZ",
        "session_id": session_id,
        "static_context": {},
    }

    with (
        patch("app.ai.llm_engine.orchestrator.build_decision_snapshot", new_callable=AsyncMock, return_value=snapshot) as build_mock,
        patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_service,
    ):
        mock_service.return_value.build = AsyncMock(return_value={})
        result = await fetch_context(state)

    build_mock.assert_awaited_once_with(session_id=session_id)
    assert result["decision_snapshot"] is snapshot


@pytest.mark.asyncio
async def test_fetch_context_rejects_session_stock_mismatch():
    from app.ai.llm_engine.orchestrator import fetch_context

    snapshot = _empty_snapshot(session_id=uuid4())
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_service:
        mock_service.return_value.build = AsyncMock()
        result = await fetch_context(
            {
                "stock_code": "600519.SH",
                "session_id": snapshot.session_id,
                "decision_snapshot": snapshot,
                "static_context": {},
            }
        )

    assert result == {"errors": ["snapshot_stock_code_mismatch"]}
    mock_service.return_value.build.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_context_normalizes_equivalent_session_stock_code():
    from app.ai.llm_engine.orchestrator import fetch_context

    snapshot = _empty_snapshot(session_id=uuid4())
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_service:
        mock_service.return_value.build = AsyncMock(return_value={})
        result = await fetch_context(
            {
                "stock_code": "000001",
                "session_id": snapshot.session_id,
                "decision_snapshot": snapshot,
                "static_context": {},
            }
        )

    mock_service.return_value.build.assert_awaited_once_with("000001.SZ")
    assert result["stock_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_fetch_context_rejects_snapshot_from_another_session():
    from app.ai.llm_engine.orchestrator import fetch_context

    snapshot = _empty_snapshot(session_id=uuid4())
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_service:
        mock_service.return_value.build = AsyncMock()
        result = await fetch_context(
            {
                "stock_code": "000001.SZ",
                "session_id": uuid4(),
                "decision_snapshot": snapshot,
                "static_context": {},
            }
        )

    assert result == {"errors": ["snapshot_session_mismatch"]}
    mock_service.return_value.build.assert_not_awaited()


@pytest.mark.asyncio
async def test_fetch_context_returns_snapshot_error_code():
    from app.ai.llm_engine.orchestrator import fetch_context

    with patch(
        "app.ai.llm_engine.orchestrator.build_decision_snapshot",
        new_callable=AsyncMock,
        side_effect=DecisionSnapshotError("snapshot_price_unavailable"),
    ):
        result = await fetch_context(
            {
                "stock_code": "000001.SZ",
                "session_id": uuid4(),
                "decision_snapshot": None,
                "static_context": {},
            }
        )

    assert result == {"errors": ["snapshot_price_unavailable"]}


@pytest.mark.asyncio
async def test_portfolio_management_uses_snapshot_pending_orders():
    from app.ai.llm_engine.orchestrator import portfolio_management

    session_id = uuid4()
    snapshot = _empty_snapshot(
        session_id=session_id,
        pending_orders=(
            PendingOrderSnapshot(
                order_id=uuid4(),
                session_id=session_id,
                stock_code="000001.SZ",
                action="buy",
                order_type="limit",
                status="pending",
                price=Decimal("10.00"),
                shares=200,
                filled_shares=0,
                created_at=datetime(2026, 8, 22, 10, 0),
                source="ai",
            ),
        ),
    )
    state = {
        "stock_code": "000001.SZ",
        "session_id": session_id,
        "decision_snapshot": snapshot,
        "static_context": {},
        "vertical_reports": {},
        "strategic_reports": {},
    }

    with (
        patch("app.ai.llm_engine.orchestrator.PortfolioManagerAgent") as mock_agent_class,
        patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock),
        patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={}),
        patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}),
        patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, side_effect=AssertionError("live pending orders used")),
        patch("app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session", new_callable=AsyncMock, return_value={}),
    ):
        agent = mock_agent_class.return_value
        agent.last_prompt = "pm prompt"
        agent.run = AsyncMock(return_value="# PM report")
        await portfolio_management(state)

    runtime_context = agent.run.await_args.args[1]
    assert runtime_context["pending_orders"] == [
        {
            "order_id": str(snapshot.pending_orders[0].order_id).replace("-", "")[:8],
            "session_id": str(session_id),
            "stock_code": "000001.SZ",
            "action": "buy",
            "order_type": "limit",
            "status": "pending",
            "price": 10.0,
            "shares": 200,
            "filled_shares": 0,
            "created_at": "2026-08-22T10:00:00",
            "source": "ai",
        }
    ]


def test_pm_position_tool_uses_state_snapshot_without_changing_tool_schema():
    from app.ai.llm_engine.agents.governance import PortfolioManagerAgent

    snapshot = DecisionSnapshot(
        snapshot_id=uuid4(),
        session_id=uuid4(),
        user_id=7,
        stock_code="000001.SZ",
        generated_at=datetime(2026, 8, 22, 10, 0),
        reference_price=Decimal("100.00"),
        price_source="realtime",
        price_as_of=datetime(2026, 8, 22, 9, 59),
        account_total_assets=Decimal("100000.00"),
        available_cash=Decimal("100000.00"),
        portfolio_market_value=Decimal("0.00"),
        position=PositionSnapshot(0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")),
        pending_buy_shares=0,
        pending_sell_shares=0,
    )
    agent = PortfolioManagerAgent(state={"session_id": str(snapshot.session_id), "decision_snapshot": snapshot})
    position_tool = next(tool for tool in agent.tools if tool.name == "calculate_executable_position_plan")

    result = __import__("asyncio").run(position_tool.ainvoke({"target_position": 0.2}))

    assert set(position_tool.args_schema.model_fields) == {"target_position"}
    assert result["position_reference_price"] == 100.0
    assert result["minimum_lot_position"] == pytest.approx(0.1)
    assert result["stock_code"] == "000001.SZ"
    assert result["price"] == 100.0
    assert result["price_source"] == "realtime"
    assert result["price_as_of"] == "2026-08-22T09:59:00"
    assert result["total_assets"] == 100000.0
    assert result["available_cash"] == 100000.0


def test_snapshot_position_plan_keeps_daily_close_as_date():
    from app.ai.llm_engine.position_plan_service import build_executable_position_plan_from_snapshot

    snapshot = _empty_snapshot(price_as_of=date(2026, 8, 22), price_source="daily_close")

    result = build_executable_position_plan_from_snapshot(snapshot, 0.2)

    assert result["price_as_of"] == "2026-08-22"


def _empty_snapshot(
    *,
    session_id=None,
    pending_orders=(),
    price_as_of=datetime(2026, 8, 22, 9, 59),
    price_source="realtime",
) -> DecisionSnapshot:
    return DecisionSnapshot(
        snapshot_id=uuid4(),
        session_id=session_id or uuid4(),
        user_id=7,
        stock_code="000001.SZ",
        generated_at=datetime(2026, 8, 22, 10, 0),
        reference_price=Decimal("100.00"),
        price_source=price_source,
        price_as_of=price_as_of,
        account_total_assets=Decimal("100000.00"),
        available_cash=Decimal("100000.00"),
        portfolio_market_value=Decimal("0.00"),
        position=PositionSnapshot(0, 0, 0, Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), stock_code="000001.SZ"),
        pending_buy_shares=0,
        pending_sell_shares=0,
        pending_orders=pending_orders,
    )
