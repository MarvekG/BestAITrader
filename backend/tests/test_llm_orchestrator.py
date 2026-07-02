from datetime import datetime
from decimal import Decimal
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

import pytest
from app.ai.llm_engine.orchestrator import (
    fetch_context, sentiment_analysis, vertical_analysis,
    strategic_round_1, strategic_round_2_1,
    fact_arbitration, portfolio_management, persist_agent_report,
    create_analyst_workflow, _get_previous_pm_decision, _get_same_stock_history,
    _get_pending_orders_for_pm, _build_pm_review_focus, _build_portfolio_field_descriptions
)
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER, AGENT_ROLE_PORTFOLIO_MANAGER, AGENT_ROLE_RISK
from app.models.account import Account
from app.models.data_storage import KlineData, StockBasic, StockRealtimeMarket, StockValuationHistory
from app.models.debate_message import DebateMessage
from app.models.order import Order
from app.models.position import Position
from app.models.pm_decision import PMDecisionRecord
from app.models.session import Session as DebateSession
from app.models.trade_record import TradeRecord
from app.models.user import User

# Mock Data
MOCK_CONTEXT = {
    "metadata": {"stock_code": "000001.SZ", "stock_name": "Ping An"},
    "realtime": {
        "market": {"price": 100},
        "indicators": {"macd": 1.2},
        "money_flow": {"main_net_inflow": 1000},
        "index_reference": {"sh_index": 3200},
    },
    "snapshot": {
        "company": {"basic": {"industry": "Bank"}, "industry_rank": {"rank": 2}},
        "financial_statements": {
            "financial_indicator": {"items": [{"meta": {"report_date": "2025-12-31"}}]},
        },
        "valuation": {"pe_ttm": 6.1},
        "northbound": {"hold_ratio": 2.1},
        "ownership": {"top_holders": {"items": []}, "fund_holding": {"item_count": 3}},
        "flow": {"northbound": {"net_buy": 10}, "dragon_tiger": {"status": "available"}},
    },
    "history": {
        "kline": {"status": "available", "items": [{"close": 100}]},
        "money_flow_trend": {"items": [{"date": "2026-03-24", "net_inflow": 1000}]},
        "northbound_trend": {"trend": "up"},
        "financial_trend": {"items": [1, 2, 3]},
        "insider_activity": {"records": []},
        "seo_history": {"items": []},
    },
    "signals": {
        "hot_rank": {"rank": 8},
        "flow": {
            "dragon_tiger_effect": {"signal": "positive"},
            "margin": {"status": "available"},
            "block_trade": {"status": "available"},
            "sector_flow": {"status": "available"},
            "margin_analysis": {"signal": "neutral"},
        },
        "risk": {
            "pledge": {"ratio": 0},
            "insider": {"items": []},
            "shareholder": {"households": 100000},
            "shareholder_trend": {"trend": "stable"},
            "regulatory": {"items": []},
        },
    },
    "events": {
        "lockup_release": {"items": []},
        "regulatory": {"items": []},
    },
}

MOCK_REPORTS = {
    "fundamental": "Fundamental Analysis Report",
    "technical": "Technical Analysis Report",
    "bull": "Bull Strategy Report",
    "bear": "Bear Strategy Report"
}


def _expected_static_context(portfolio_info=None):
    static_context = {"data": MOCK_CONTEXT}
    static_context["portfolio_info"] = (
        portfolio_info if portfolio_info is not None else {
            "account": {},
            "position": {},
            "field_descriptions": _build_portfolio_field_descriptions(),
        }
    )
    return static_context


def _saved_pm_record():
    """构造 PM 工具保存后的最小记录替身。"""
    return SimpleNamespace(to_dict=lambda: {"confidence_score": 80, "target_position": 0.0})


@pytest.fixture
def initial_state():
    return {
        "stock_code": "000001.SZ",
        "trading_frequency": "swing",
        "trading_strategy": "momentum",
        "session_id": uuid4(),
        "static_context": {},
        "context": {},
        "sentiment_report": "",
        "news_report": "",
        "policy_report": "",
        "vertical_reports": {},
        "strategic_reports": {},
        "strategic_round_2_1_reports": {},
        "fact_arbitration_report": "",
        "pm_decision": {},
        "post_trade_reflection": {},
        "user_id": None,
        "errors": []
    }


@pytest.mark.asyncio
async def test_fetch_context_node(initial_state):
    initial_state["session_id"] = None
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService:
        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=MOCK_CONTEXT)

        result = await fetch_context(initial_state)

        assert "static_context" in result
        assert result["static_context"] == _expected_static_context()
        assert result["context"] == {}
        assert MockService.called


@pytest.mark.asyncio
async def test_fetch_context_metadata_uses_share_fields_from_valuation(initial_state, test_db):
    async with test_db() as db:
        user = User(
            username="metadata_valuation_share_user",
            email="metadata_valuation_share_user@example.com",
            password_hash="hashed",
        )
        db.add(user)
        await db.flush()
        db.add(
            DebateSession(
                user_id=user.id,
                stock_code="000001.SZ",
                trading_frequency="swing",
                trading_strategy="momentum",
                status="active",
                session_id=initial_state["session_id"],
            )
        )
        db.add(StockBasic(stock_code="000001.SZ", name="平安银行", industry="银行"))
        db.add(
            StockValuationHistory(
                stock_code="000001.SZ",
                data_date=datetime(2026, 6, 10).date(),
                total_share=5_000_000_000,
                float_share=4_500_000_000,
            )
        )
        await db.commit()

    from app.ai.llm_engine.context.providers import MetadataProvider
    from app.ai.llm_engine.context.service import AIContextService

    with patch("app.ai.llm_engine.context.runtime.database_module.AsyncSessionLocal", test_db):
        context = await AIContextService(providers=[MetadataProvider()]).build("000001.SZ")

    assert context["metadata"]["company"]["total_share"] == 5_000_000_000
    assert context["metadata"]["company"]["float_share"] == 4_500_000_000
    assert context["metadata"]["company"]["share_unit"] == "shares"
    assert context["metadata"]["company"]["share_source"] == "stock_valuation_history"


@pytest.mark.asyncio
async def test_fetch_context_node_keeps_portfolio_risk_control_in_build_context(initial_state):
    initial_state["session_id"] = uuid4()
    ai_context = {
        **MOCK_CONTEXT,
        "portfolio": {
            "status": "available",
            "risk_control": {
                "summary": {
                    "enabled": True,
                    "max_single_position_pct": 0.2,
                    "max_industry_position_pct": 0.35,
                    "min_cash_pct": 0.1,
                    "require_stop_loss": True,
                    "stop_loss_warning_pct": 0.1,
                },
                "text": (
                    "Portfolio risk control: enabled; max single-stock weight 20.00%; "
                    "max industry weight 35.00%; minimum cash ratio 10.00%; "
                    "buy orders require stop loss; stop-loss warning threshold 10.00%."
                ),
            },
        },
    }
    fake_db = MagicMock()
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService, \
         patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)):
        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=ai_context)
        mock_session = MagicMock()
        mock_account = MagicMock()
        mock_account.total_assets = 1000000
        mock_account.available_cash = 200000
        mock_account.market_value = 800000
        mock_position = MagicMock()
        mock_position.stock_code = "000001.SZ"
        mock_position.total_shares = 1000
        mock_position.available_shares = 900
        mock_position.avg_cost = 10.0
        mock_position.current_price = 11.0
        mock_position.profit_loss = 1000.0
        mock_position.profit_loss_pct = 0.1

        fake_db.execute.side_effect = [
            SimpleNamespace(scalar_one_or_none=lambda: mock_session),
            SimpleNamespace(scalar_one_or_none=lambda: mock_account),
            SimpleNamespace(scalar_one_or_none=lambda: mock_position),
        ]

        result = await fetch_context(initial_state)

        assert "portfolio_risk_control" not in result["static_context"]
        assert result["static_context"]["data"]["portfolio"]["risk_control"] == ai_context["portfolio"]["risk_control"]
        assert result["static_context"]["portfolio_info"]["account"] == {
            "total_assets": "1000000元",
            "available_cash": "200000元",
            "market_value": "800000元",
        }
        assert result["static_context"]["portfolio_info"]["field_descriptions"] == _build_portfolio_field_descriptions()


@pytest.mark.asyncio
async def test_fetch_context_uses_portfolio_overview_position_as_pm_valuation_source(initial_state, async_db_session):
    user = User(
        username="pm_revalue_user",
        email="pm_revalue_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    account = Account(
        user_id=user.id,
        total_assets=Decimal("100000.00"),
        available_cash=Decimal("50000.00"),
        frozen_cash=Decimal("0.00"),
        market_value=Decimal("50000.00"),
        initial_capital=Decimal("100000.00"),
        total_profit_loss=Decimal("0.00"),
    )
    async_db_session.add(account)
    await async_db_session.flush()
    async_db_session.add(StockBasic(stock_code="000001.SZ", name="平安银行", industry="银行"))
    await async_db_session.flush()
    session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="active",
    )
    async_db_session.add_all(
        [
            session,
            Position(
                account_id=account.account_id,
                stock_code="000001.SZ",
                total_shares=1000,
                available_shares=900,
                avg_cost=Decimal("10.0000"),
                current_price=Decimal("11.0000"),
                market_value=Decimal("11000.0000"),
                profit_loss=Decimal("1000.0000"),
                profit_loss_pct=Decimal("0.1000"),
            ),
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=Decimal("8.5000"),
                timestamp=datetime(2026, 6, 5, 14, 58),
            ),
        ]
    )
    await async_db_session.commit()
    initial_state["session_id"] = session.session_id
    context_with_portfolio = {
        **MOCK_CONTEXT,
        "portfolio": {
            "overview": {
                "summary": {
                    "total_assets": 58500.0,
                    "available_cash": 50000.0,
                    "market_value": 8500.0,
                },
                "positions": [
                    {
                        "stock_code": "000001.SZ",
                        "current_price": 8.5,
                        "weight": 8500 / 58500,
                        "unrealized_pnl": -1500.0,
                        "unrealized_pnl_pct": -0.15,
                    }
                ],
            }
        },
    }

    with patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService, \
         patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=context_with_portfolio)

        result = await fetch_context(initial_state)

    position = result["static_context"]["portfolio_info"]["position"]
    assert position["total_shares"] == "1000股"
    assert position["available_shares"] == "900股"
    assert position["avg_cost"] == "10元"
    assert position["current_price"] == "8.5元"
    assert position["weight"] == 8500 / 58500
    assert position["unrealized_pnl"] == -1500.0
    assert position["unrealized_pnl_pct"] == -0.15
    assert result["static_context"]["portfolio_info"]["account"] == {
        "total_assets": "58500元",
        "available_cash": "50000元",
        "market_value": "8500元",
    }


@pytest.mark.asyncio
async def test_fetch_context_keeps_portfolio_info_consistent_with_overview(initial_state, async_db_session):
    user = User(
        username="pm_revalue_stale_realtime_user",
        email="pm_revalue_stale_realtime_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    account = Account(
        user_id=user.id,
        total_assets=Decimal("100000.00"),
        available_cash=Decimal("50000.00"),
        frozen_cash=Decimal("0.00"),
        market_value=Decimal("50000.00"),
        initial_capital=Decimal("100000.00"),
        total_profit_loss=Decimal("0.00"),
    )
    async_db_session.add(account)
    await async_db_session.flush()
    async_db_session.add(StockBasic(stock_code="000001.SZ", name="平安银行", industry="银行"))
    await async_db_session.flush()
    session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="active",
    )
    async_db_session.add_all(
        [
            session,
            Position(
                account_id=account.account_id,
                stock_code="000001.SZ",
                total_shares=1000,
                available_shares=900,
                avg_cost=Decimal("10.0000"),
                current_price=Decimal("11.0000"),
                market_value=Decimal("11000.0000"),
                profit_loss=Decimal("1000.0000"),
                profit_loss_pct=Decimal("0.1000"),
            ),
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=Decimal("8.5000"),
                timestamp=datetime(2026, 6, 5, 14, 58),
            ),
            KlineData(
                stock_code="000001.SZ",
                date=datetime(2026, 6, 6).date(),
                close=9.2,
                freq="D",
            ),
        ]
    )
    await async_db_session.commit()
    initial_state["session_id"] = session.session_id
    context_with_portfolio = {
        **MOCK_CONTEXT,
        "portfolio": {
            "overview": {
                "summary": {
                    "total_assets": 59200.0,
                    "available_cash": 50000.0,
                    "market_value": 9200.0,
                },
                "positions": [
                    {
                        "stock_code": "000001.SZ",
                        "current_price": 9.2,
                        "weight": 9200 / 59200,
                        "unrealized_pnl": -800.0,
                        "unrealized_pnl_pct": -0.08,
                    }
                ],
            }
        },
    }

    with patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService, \
         patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=context_with_portfolio)

        result = await fetch_context(initial_state)

    position = result["static_context"]["portfolio_info"]["position"]
    assert position["current_price"] == "9.2元"
    assert position["weight"] == 9200 / 59200
    assert position["unrealized_pnl"] == -800.0
    assert position["unrealized_pnl_pct"] == -0.08


@pytest.mark.asyncio
async def test_sentiment_analysis_node(initial_state):
    initial_state["static_context"] = _expected_static_context()

    with patch("app.ai.llm_engine.orchestrator.SentimentAgent") as MockS, \
         patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist:

        MockS.return_value.run = AsyncMock(return_value="S_Report")

        result = await sentiment_analysis(initial_state)

        assert result["sentiment_report"] == "S_Report"
        sentiment_snapshot, runtime_context = MockS.return_value.run.await_args.args
        assert sentiment_snapshot == _expected_static_context()
        assert runtime_context == {}
        assert mock_persist.call_count == 1


@pytest.mark.asyncio
async def test_vertical_analysis_node(initial_state):
    initial_state["static_context"] = _expected_static_context()

    # Mock specific agents
    with patch("app.ai.llm_engine.orchestrator.FundamentalAgent") as MockF, \
         patch("app.ai.llm_engine.orchestrator.TechnicalAgent") as MockT, \
         patch("app.ai.llm_engine.orchestrator.CapitalFlowAgent") as MockC, \
         patch("app.ai.llm_engine.orchestrator.RiskAgent") as MockR, \
         patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist:

        MockF.return_value.run = AsyncMock(return_value="F_Report")
        MockT.return_value.run = AsyncMock(return_value="T_Report")
        MockC.return_value.run = AsyncMock(return_value="C_Report")
        MockR.return_value.run = AsyncMock(return_value="R_Report")

        result = await vertical_analysis(initial_state)

        assert len(result["vertical_reports"]) == 4
        assert result["vertical_reports"]["fundamental"] == "F_Report"
        fundamental_snapshot, runtime_context = MockF.return_value.run.await_args.args
        assert fundamental_snapshot == _expected_static_context()
        assert runtime_context == {}
        assert mock_persist.call_count == 4


@pytest.mark.asyncio
async def test_vertical_analysis_collects_agent_failures(initial_state):
    initial_state["static_context"] = _expected_static_context()

    with patch("app.ai.llm_engine.orchestrator.FundamentalAgent") as MockF, \
         patch("app.ai.llm_engine.orchestrator.TechnicalAgent") as MockT, \
         patch("app.ai.llm_engine.orchestrator.CapitalFlowAgent") as MockC, \
         patch("app.ai.llm_engine.orchestrator.RiskAgent") as MockR, \
         patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock):
        MockF.return_value.run = AsyncMock(side_effect=RuntimeError("fundamental feed timeout"))
        MockT.return_value.run = AsyncMock(return_value="T_Report")
        MockC.return_value.run = AsyncMock(return_value="C_Report")
        MockR.return_value.run = AsyncMock(return_value="R_Report")

        result = await vertical_analysis(initial_state)

    assert "fundamental" not in result["vertical_reports"]
    assert result["vertical_reports"]["technical"] == "T_Report"
    assert any("fundamental feed timeout" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_strategic_round_nodes(initial_state):
    initial_state["static_context"] = _expected_static_context()
    initial_state["vertical_reports"] = {"fundamental": "F", "technical": "T"}

    with patch("app.ai.llm_engine.orchestrator.BullAgent") as MockBull, \
         patch("app.ai.llm_engine.orchestrator.BearAgent") as MockBear, \
         patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist_1:

        MockBull.return_value.run = AsyncMock(return_value="Bull_Report")
        MockBear.return_value.run = AsyncMock(return_value="Bear_Report")

        res1 = await strategic_round_1(initial_state)
        assert len(res1["strategic_reports"]) == 2
        bull_snapshot, bull_runtime_context = MockBull.return_value.run.await_args.args
        assert bull_snapshot == _expected_static_context()
        assert bull_runtime_context["layer1_analysis"] == {"fundamental": "F", "technical": "T"}
        assert mock_persist_1.call_count == 2

        # Round 2
        initial_state["strategic_reports"] = res1["strategic_reports"]
        with patch("app.ai.llm_engine.orchestrator.AggressiveAgent") as MockA, \
             patch("app.ai.llm_engine.orchestrator.ConservativeAgent") as MockC, \
             patch("app.ai.llm_engine.orchestrator.NeutralAgent") as MockN, \
             patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist_2:

            MockA.return_value.run = AsyncMock(return_value="A_Report")
            MockC.return_value.run = AsyncMock(return_value="C_Report")
            MockN.return_value.run = AsyncMock(return_value="N_Report")

            res2 = await strategic_round_2_1(initial_state)
            assert "strategic_round_2_1_reports" in res2
            aggressive_snapshot, aggressive_runtime_context = MockA.return_value.run.await_args.args
            assert aggressive_snapshot == _expected_static_context()
            assert aggressive_runtime_context["debate_round_1"] == res1["strategic_reports"]
            assert mock_persist_2.call_count == 3

        initial_state["strategic_reports"] = res2["strategic_reports"]
        initial_state["strategic_round_2_1_reports"] = res2["strategic_round_2_1_reports"]
        with patch("app.ai.llm_engine.orchestrator.FactArbitrationAgent") as MockFact, \
             patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist_fact:

            MockFact.return_value.run = AsyncMock(return_value="# 事实冲突仲裁摘要")

            res3 = await fact_arbitration(initial_state)
            assert res3["fact_arbitration_report"] == "# 事实冲突仲裁摘要"
            fact_snapshot, fact_runtime_context = MockFact.return_value.run.await_args.args
            assert fact_snapshot == _expected_static_context()
            assert fact_runtime_context["strategic_debate"] == res2["strategic_reports"]
            assert fact_runtime_context["strategic_round_2_1"] == res2["strategic_round_2_1_reports"]
            assert mock_persist_fact.call_count == 1


@pytest.mark.asyncio
async def test_full_workflow_integration(initial_state):
    """Integration test for the full graph flow using mocks for all external calls"""
    fake_db = MagicMock()
    fake_db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    with (
        patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService,
        patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run,
        patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist,
        patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)),
        patch(
            "app.trading.service.trading_service.execute_order_and_update_db",
            new_callable=AsyncMock,
        ) as mock_trade,
        patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}),
        patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, return_value=[]),
        patch(
            "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
            new_callable=AsyncMock,
            return_value=_saved_pm_record().to_dict(),
        ),
        patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={}),
    ):

        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=MOCK_CONTEXT)
        # Return different data based on the agent's expected output model
        def mock_run_side_effect(static_context, context=None):
            assert static_context == _expected_static_context()
            context = context or {}
            # If it's the PM, return final Markdown report.
            if "previous_pm_decision" in context:
                return "# PM report"
            # Otherwise return vertical/strategic reports
            return "Test Report"

        mock_agent_run.side_effect = mock_run_side_effect
        mock_trade.return_value = {"success": True, "message": "Success"}

        workflow = create_analyst_workflow()
        final_state = await workflow.ainvoke(initial_state)

        assert "context" in final_state
        assert "sentiment_report" in final_state
        assert "vertical_reports" in final_state
        assert len(final_state["vertical_reports"]) == 4
        assert len(final_state["strategic_reports"]) == 5
        assert final_state["fact_arbitration_report"] == "Test Report"
        assert "pm_decision" in final_state
        assert "trader_execution" not in final_state
        assert not final_state["errors"]

        # Total persists: 1 (news) + 1 (policy) + 1 (sentiment) + 4 (vertical) + 2 (round 1)
        # + 3 (round 2.1) + 1 (fact arbitration) + 1 (PM) = 14
        assert mock_persist.call_count == 14


@pytest.mark.asyncio
async def test_full_workflow_stops_before_strategy_when_layer1_fails(initial_state):
    initial_state["session_id"] = None

    with patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService, \
         patch("app.ai.llm_engine.orchestrator.NewsAgent") as MockNews, \
         patch("app.ai.llm_engine.orchestrator.PolicyAgent") as MockPolicy, \
         patch("app.ai.llm_engine.orchestrator.SentimentAgent") as MockSentiment, \
         patch("app.ai.llm_engine.orchestrator.FundamentalAgent") as MockF, \
         patch("app.ai.llm_engine.orchestrator.TechnicalAgent") as MockT, \
         patch("app.ai.llm_engine.orchestrator.CapitalFlowAgent") as MockC, \
         patch("app.ai.llm_engine.orchestrator.RiskAgent") as MockR, \
         patch("app.ai.llm_engine.orchestrator.BullAgent") as MockBull, \
         patch("app.ai.llm_engine.orchestrator.BearAgent") as MockBear, \
         patch("app.ai.llm_engine.orchestrator.PortfolioManagerAgent") as MockPM, \
         patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock):
        MockService.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)
        MockNews.return_value.run = AsyncMock(side_effect=RuntimeError("news service unavailable"))
        MockPolicy.return_value.run = AsyncMock(return_value="P_Report")
        MockSentiment.return_value.run = AsyncMock(return_value="S_Report")
        MockF.return_value.run = AsyncMock(return_value="F_Report")
        MockT.return_value.run = AsyncMock(return_value="T_Report")
        MockC.return_value.run = AsyncMock(return_value="C_Report")
        MockR.return_value.run = AsyncMock(return_value="R_Report")
        MockBull.return_value.run = AsyncMock(return_value="Bull_Report")
        MockBear.return_value.run = AsyncMock(return_value="Bear_Report")
        MockPM.return_value.run = AsyncMock(return_value={"decision": "hold"})

        final_state = await create_analyst_workflow().ainvoke(initial_state)

    assert any("news service unavailable" in error for error in final_state["errors"])
    assert final_state["strategic_reports"] == {}
    assert final_state["pm_decision"] == {}
    assert MockBull.return_value.run.await_count == 0
    assert MockBear.return_value.run.await_count == 0
    assert MockPM.return_value.run.await_count == 0


class _PersistQuery:
    def __init__(self, session_obj):
        self.session_obj = session_obj

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.session_obj


class _PersistDb:
    def __init__(self, session_obj):
        self.session_obj = session_obj
        self.added = []

    def query(self, *_args, **_kwargs):
        raise AssertionError("Legacy sync .query() must not be used by async persistence tests")

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        return None

    def refresh(self, obj):
        if not getattr(obj, "message_id", None):
            obj.message_id = uuid4()
        if not getattr(obj, "created_at", None):
            obj.created_at = datetime.now()

    def rollback(self):
        return None

    def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.session_obj.session_id)


class _SessionLocalContext:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        return False


class _AsyncSessionLocalContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, statement):
        result = self.db.execute(statement)
        if inspect.isawaitable(result):
            return await result
        return result

    def add(self, obj):
        self.db.add(obj)

    async def commit(self):
        result = self.db.commit()
        if inspect.isawaitable(result):
            await result

    async def refresh(self, obj):
        result = self.db.refresh(obj)
        if inspect.isawaitable(result):
            await result


@pytest.mark.asyncio
async def test_persist_agent_report_saves_pm_report():
    session_id = uuid4()
    fake_session = SimpleNamespace(
        session_id=session_id,
        user_id=7,
        stock_code="000001.SZ",
        trading_strategy="momentum",
        trading_frequency="swing",
    )
    fake_db = _PersistDb(fake_session)
    saved_pm_decision = SimpleNamespace(
        confidence_score=88,
        to_dict=lambda: {"confidence_score": 88, "target_position": 0.4},
    )

    with (
        patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)),
        patch(
            "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
            new_callable=AsyncMock,
            return_value=saved_pm_decision,
        ),
        patch("app.api.endpoints.debate_ws.send_debate_message", new_callable=AsyncMock),
    ):
        await persist_agent_report(
            session_id=session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name=AGENT_NAME_PORTFOLIO_MANAGER,
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            report_content="# PM report",
            prompt_input="pm prompt",
        )

    assert len(fake_db.added) == 1
    assert fake_db.added[0].agent_role == AGENT_ROLE_PORTFOLIO_MANAGER
    assert fake_db.added[0].reasoning == "# PM report"
    assert fake_db.added[0].prompt_input == "pm prompt"


@pytest.mark.asyncio
async def test_portfolio_management_passes_expected_top_level_keys(initial_state):
    initial_state["vertical_reports"] = {"fundamental": "F", AGENT_ROLE_RISK: "Risk report"}
    initial_state["strategic_reports"] = {"bull": "B"}
    initial_state["fact_arbitration_report"] = "Fact arbitration"
    portfolio_info = {"account": {"total_assets": 1000000}, "position": {}}
    initial_state["static_context"] = _expected_static_context(portfolio_info)

    with (
        patch("app.ai.llm_engine.orchestrator.PortfolioManagerAgent") as MockPM,
        patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock),
        patch(
            "app.ai.llm_engine.orchestrator._get_previous_pm_decision",
            new_callable=AsyncMock,
            return_value={"decision": "buy", "target_position": 0.3},
        ),
        patch(
            "app.ai.llm_engine.orchestrator._get_same_stock_history",
            new_callable=AsyncMock,
            return_value={"recent_execution_summary": {"recent_realized_pnl": -100.0}},
        ),
        patch(
            "app.ai.llm_engine.orchestrator._get_pending_orders_for_pm",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
            new_callable=AsyncMock,
            return_value=_saved_pm_record().to_dict(),
        ),
    ):
        mock_agent = MockPM.return_value
        mock_agent.last_prompt = "test prompt"
        mock_agent.run = AsyncMock(return_value={"decision": "hold", "confidence_score": 80})

        result = await portfolio_management(initial_state)

        assert "pm_decision" in result
        pm_snapshot, pm_runtime_context = mock_agent.run.await_args.args
        assert pm_snapshot == _expected_static_context(portfolio_info)
        assert set(pm_runtime_context.keys()) == {
            "sentiment_report",
            "news_report",
            "policy_report",
            "risk_report",
            "previous_pm_decision",
            "same_stock_history",
            "pending_orders",
            "vertical_views",
            "strategic_debate",
            "fact_arbitration_report",
        }
        assert pm_runtime_context["previous_pm_decision"]["decision"] == "buy"
        assert pm_runtime_context["risk_report"] == "Risk report"
        assert pm_runtime_context["same_stock_history"]["recent_execution_summary"]["recent_realized_pnl"] == -100.0
        assert pm_runtime_context["pending_orders"] == []
        assert pm_runtime_context["fact_arbitration_report"] == "Fact arbitration"


@pytest.mark.asyncio
async def test_get_same_stock_history_includes_trades_pnl_and_stop_loss(async_db_session):
    user = User(
        username="same_stock_history_user",
        email="same_stock_history_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    account = Account(
        user_id=user.id,
        total_assets=Decimal("1000000.00"),
        available_cash=Decimal("500000.00"),
        frozen_cash=Decimal("0.00"),
        market_value=Decimal("500000.00"),
        initial_capital=Decimal("1000000.00"),
        total_profit_loss=Decimal("-4916.02"),
        profit_loss_pct=Decimal("-0.49"),
        total_trades=2,
        win_rate=Decimal("0.00"),
    )
    async_db_session.add(account)
    await async_db_session.flush()
    previous_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend_following",
        status="completed",
    )
    current_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend_following",
        status="active",
    )
    async_db_session.add_all([previous_session, current_session])
    await async_db_session.flush()
    async_db_session.add(
        DebateMessage(
            session_id=previous_session.session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name="PM",
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            decision="sell",
            reasoning="# previous stop loss report",
            created_at=datetime(2026, 6, 4, 13, 37),
        )
    )
    async_db_session.add(
        PMDecisionRecord(
            session_id=previous_session.session_id,
            user_id=user.id,
            stock_code="000001.SZ",
            target_position=0.0,
            confidence_score=85,
            stop_loss=29.5,
            take_profit=38.7,
            holding_horizon_days=20,
        )
    )
    buy_order = Order(
        session_id=previous_session.session_id,
        account_id=account.account_id,
        stock_code="000001.SZ",
        action="buy",
        order_type="market",
        price=Decimal("30.10"),
        shares=900,
        status="filled",
        filled_shares=900,
        avg_fill_price=Decimal("30.10"),
        realized_pnl=Decimal("0.00"),
        created_at=datetime(2026, 5, 20, 14, 30),
        filled_at=datetime(2026, 5, 20, 14, 30),
        source=f"ai:{previous_session.session_id}",
    )
    sell_order = Order(
        session_id=previous_session.session_id,
        account_id=account.account_id,
        stock_code="000001.SZ",
        action="sell",
        order_type="market",
        price=Decimal("28.62"),
        shares=900,
        status="filled",
        filled_shares=900,
        avg_fill_price=Decimal("28.62"),
        realized_pnl=Decimal("-4916.02"),
        created_at=datetime(2026, 6, 4, 14, 48),
        filled_at=datetime(2026, 6, 4, 14, 48),
        source=f"ai:{previous_session.session_id}",
    )
    async_db_session.add_all([buy_order, sell_order])
    await async_db_session.flush()
    async_db_session.add_all(
        [
            TradeRecord(
                session_id=previous_session.session_id,
                account_id=account.account_id,
                order_id=buy_order.order_id,
                stock_code="000001.SZ",
                action="buy",
                quantity=900,
                fill_price=Decimal("30.10"),
                commission=Decimal("5.42"),
                stamp_duty=Decimal("0.00"),
                transfer_fee=Decimal("0.54"),
                total_fees=Decimal("5.96"),
                net_amount=Decimal("27095.96"),
                trade_time=datetime(2026, 5, 20, 14, 30),
            ),
            TradeRecord(
                session_id=previous_session.session_id,
                account_id=account.account_id,
                order_id=sell_order.order_id,
                stock_code="000001.SZ",
                action="sell",
                quantity=900,
                fill_price=Decimal("28.62"),
                commission=Decimal("12.02"),
                stamp_duty=Decimal("60.10"),
                transfer_fee=Decimal("1.20"),
                total_fees=Decimal("73.32"),
                net_amount=Decimal("25758.00"),
                trade_time=datetime(2026, 6, 4, 14, 48),
            ),
        ]
    )
    await async_db_session.commit()

    with patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        result = await _get_same_stock_history(current_session.session_id, "000001.SZ")

    summary = result["recent_execution_summary"]
    assert summary["has_orders"] is True
    assert summary["has_trades"] is True
    assert summary["recent_realized_pnl"] == -4916.02
    assert summary["has_recent_realized_loss"] is True
    assert summary["new_edge_review_required"] is True
    assert summary["latest_exit_order"]["action"] == "sell"
    assert summary["latest_exit_order"]["pm_stop_loss"] == 29.5
    assert result["recent_orders"][0]["order_id"] == str(sell_order.order_id).replace("-", "")[:8]
    assert result["recent_orders"][0]["realized_pnl"] == -4916.02
    assert result["recent_trades"][0]["order_id"] == str(sell_order.order_id).replace("-", "")[:8]
    assert result["recent_trades"][0]["order_realized_pnl"] == -4916.02
    assert result["recent_pm_decisions"][0]["stop_loss"] == 29.5
    assert result["recent_pm_decisions"][0]["execution_summary"]["realized_pnl"] == -4916.02


@pytest.mark.asyncio
async def test_get_same_stock_history_does_not_treat_rejected_sell_as_exit(async_db_session):
    user = User(
        username="same_stock_rejected_sell_user",
        email="same_stock_rejected_sell_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    account = Account(
        user_id=user.id,
        total_assets=Decimal("1000000.00"),
        available_cash=Decimal("500000.00"),
        frozen_cash=Decimal("0.00"),
        market_value=Decimal("500000.00"),
        initial_capital=Decimal("1000000.00"),
    )
    async_db_session.add(account)
    await async_db_session.flush()
    previous_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend_following",
        status="completed",
    )
    current_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend_following",
        status="active",
    )
    async_db_session.add_all([previous_session, current_session])
    await async_db_session.flush()
    async_db_session.add(
        Order(
            session_id=previous_session.session_id,
            account_id=account.account_id,
            stock_code="000001.SZ",
            action="sell",
            order_type="market",
            price=Decimal("28.62"),
            shares=900,
            status="rejected",
            filled_shares=0,
            avg_fill_price=None,
            realized_pnl=None,
            created_at=datetime(2026, 6, 4, 14, 48),
            source=f"ai:{previous_session.session_id}",
        )
    )
    async_db_session.add(
        PMDecisionRecord(
            session_id=previous_session.session_id,
            user_id=user.id,
            stock_code="000001.SZ",
            target_position=0.0,
            confidence_score=70,
        )
    )
    await async_db_session.commit()

    with patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        result = await _get_same_stock_history(current_session.session_id, "000001.SZ")

    summary = result["recent_execution_summary"]
    assert summary["has_orders"] is True
    assert summary["has_trades"] is False
    assert summary["latest_exit_order"] is None
    assert summary["new_edge_review_required"] is False
    assert result["recent_orders"][0]["status"] == "rejected"


def test_pm_review_focus_uses_system_language(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "en")
    assert _build_pm_review_focus() == [
        "What was actually bought or sold historically",
        "How much those trades actually made or lost",
        "Where the latest stop-loss or liquidation reference was",
        "Whether this round has new verifiable edge versus the losing trade",
    ]

    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")
    assert _build_pm_review_focus() == [
        "历史上实际买卖了什么",
        "这些交易实际赚亏多少",
        "上一轮止损或清仓参考在哪里",
        "本轮相对亏损交易是否有新增可验证优势",
    ]


@pytest.mark.asyncio
async def test_get_previous_pm_decision_includes_execution_summary_with_dates(async_db_session):
    user = User(
        username="previous_pm_context_user",
        email="previous_pm_context_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    previous_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="completed",
    )
    current_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="active",
    )
    async_db_session.add_all([previous_session, current_session])
    await async_db_session.flush()
    pm_message = DebateMessage(
        session_id=previous_session.session_id,
        stage="portfolio_management",
        round_number=0,
        agent_name="PM",
        agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
        decision="buy",
        reasoning="# previous report",
        created_at=datetime(2026, 1, 1, 15, 0),
    )
    async_db_session.add(pm_message)
    async_db_session.add(
        PMDecisionRecord(
            session_id=previous_session.session_id,
            user_id=user.id,
            stock_code="000001.SZ",
            target_position=0.3,
            confidence_score=80,
            stop_loss=9.5,
            take_profit=12.0,
            holding_horizon_days=20,
        )
    )
    async_db_session.add_all(
        [
            Order(
                session_id=previous_session.session_id,
                stock_code="000001.SZ",
                action="buy",
                order_type="market",
                price=Decimal("10.0"),
                shares=100,
                status="filled",
                realized_pnl=Decimal("3.5"),
                created_at=datetime(2026, 1, 1, 15, 1),
            ),
            TradeRecord(
                session_id=previous_session.session_id,
                stock_code="000001.SZ",
                action="buy",
                quantity=100,
                fill_price=Decimal("10.0"),
                trade_time=datetime(2026, 1, 1, 15, 5),
            ),
            TradeRecord(
                session_id=previous_session.session_id,
                stock_code="000001.SZ",
                action="buy",
                quantity=300,
                fill_price=Decimal("12.0"),
                trade_time=datetime(2026, 1, 1, 15, 6),
            ),
        ]
    )
    await async_db_session.commit()

    with patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        result = await _get_previous_pm_decision(current_session.session_id, "000001.SZ")

    assert result["take_profit"] == 12.0
    assert result["holding_horizon_days"] == 20
    assert result["execution_summary"] == {
        "has_orders": True,
        "has_trades": True,
        "order_count": 1,
        "filled_order_count": 1,
        "avg_fill_price": 11.5,
        "total_quantity": 400,
        "realized_pnl": 3.5,
        "first_order_time": "2026-01-01T15:01:00",
        "latest_order_time": "2026-01-01T15:01:00",
        "first_trade_time": "2026-01-01T15:05:00",
        "latest_trade_time": "2026-01-01T15:06:00",
    }
    assert "experience_review_summary" not in result


@pytest.mark.asyncio
async def test_get_previous_pm_decision_orders_execution_summary_by_order_time(async_db_session):
    user = User(
        username="previous_pm_multi_order_user",
        email="previous_pm_multi_order_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    previous_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="completed",
    )
    current_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="active",
    )
    async_db_session.add_all([previous_session, current_session])
    await async_db_session.flush()
    async_db_session.add(
        DebateMessage(
            session_id=previous_session.session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name="PM",
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            decision="buy",
            reasoning="# previous report",
            created_at=datetime(2026, 1, 1, 15, 0),
        )
    )
    async_db_session.add(
        PMDecisionRecord(
            session_id=previous_session.session_id,
            user_id=user.id,
            stock_code="000001.SZ",
            target_position=0.3,
            confidence_score=80,
        )
    )
    later_order_id = uuid4()
    earlier_order_id = uuid4()
    async_db_session.add_all(
        [
            Order(
                order_id=later_order_id,
                session_id=previous_session.session_id,
                stock_code="000001.SZ",
                action="buy",
                order_type="market",
                price=Decimal("11.0"),
                shares=100,
                status="filled",
                created_at=datetime(2026, 1, 1, 15, 3),
            ),
            Order(
                order_id=earlier_order_id,
                session_id=previous_session.session_id,
                stock_code="000001.SZ",
                action="buy",
                order_type="market",
                price=Decimal("10.0"),
                shares=100,
                status="filled",
                created_at=datetime(2026, 1, 1, 15, 1),
            ),
        ]
    )
    await async_db_session.commit()

    with patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        result = await _get_previous_pm_decision(current_session.session_id, "000001.SZ")

    assert result["execution_summary"]["first_order_time"] == "2026-01-01T15:01:00"
    assert result["execution_summary"]["latest_order_time"] == "2026-01-01T15:03:00"


@pytest.mark.asyncio
async def test_get_pending_orders_for_pm_returns_llm_order_ids(async_db_session):
    user = User(
        username="pending_order_context_user",
        email="pending_order_context_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    account = Account(
        user_id=user.id,
        total_assets=Decimal("100000.0000"),
        available_cash=Decimal("90000.0000"),
        frozen_cash=Decimal("10000.0000"),
        market_value=Decimal("0.0000"),
        total_profit_loss=Decimal("0.0000"),
    )
    current_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="active",
    )
    async_db_session.add_all([account, current_session])
    await async_db_session.flush()
    order_id = uuid4()
    async_db_session.add(
        Order(
            order_id=order_id,
            session_id=current_session.session_id,
            account_id=account.account_id,
            stock_code="000001.SZ",
            action="buy",
            order_type="limit",
            price=Decimal("10.5000"),
            shares=100,
            filled_shares=0,
            status="pending",
            created_at=datetime(2026, 1, 1, 15, 1),
        )
    )
    await async_db_session.commit()

    with patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        result = await _get_pending_orders_for_pm(current_session.session_id)

    assert result == [
        {
            "order_id": str(order_id).replace("-", "")[:8],
            "session_id": str(current_session.session_id),
            "stock_code": "000001.SZ",
            "action": "buy",
            "order_type": "limit",
            "status": "pending",
            "price": 10.5,
            "shares": 100,
            "filled_shares": 0,
            "created_at": "2026-01-01T15:01:00",
            "source": None,
        }
    ]


@pytest.mark.asyncio
async def test_get_previous_pm_decision_marks_missing_execution(async_db_session):
    user = User(
        username="previous_pm_no_result_user",
        email="previous_pm_no_result_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    previous_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="completed",
    )
    current_session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
        status="active",
    )
    async_db_session.add_all([previous_session, current_session])
    await async_db_session.flush()
    async_db_session.add(
        DebateMessage(
            session_id=previous_session.session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name="PM",
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            decision="hold",
            reasoning="# previous hold report",
            created_at=datetime(2026, 1, 1, 15, 0),
        )
    )
    async_db_session.add(
        PMDecisionRecord(
            session_id=previous_session.session_id,
            user_id=user.id,
            stock_code="000001.SZ",
            target_position=0.0,
            confidence_score=70,
        )
    )
    await async_db_session.commit()

    with patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(async_db_session)):
        result = await _get_previous_pm_decision(current_session.session_id, "000001.SZ")

    assert result["execution_summary"] == {
        "has_orders": False,
        "has_trades": False,
        "order_count": 0,
        "filled_order_count": 0,
        "avg_fill_price": None,
        "total_quantity": 0,
        "realized_pnl": 0,
        "first_order_time": None,
        "latest_order_time": None,
        "first_trade_time": None,
        "latest_trade_time": None,
    }
    assert "experience_review_summary" not in result
