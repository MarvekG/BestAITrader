import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch, MagicMock
from uuid import uuid4

import pytest
from app.ai.llm_engine.orchestrator import (
    AnalystState, fetch_context, sentiment_analysis, vertical_analysis,
    strategic_round_1, strategic_round_2_1, strategic_round_2_rebuttal,
    portfolio_management, persist_agent_report,
    create_analyst_workflow
)
from app.ai.llm_engine.models import PMDecision
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER, AGENT_ROLE_PORTFOLIO_MANAGER

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
        "financial_statements": {"financial_indicator_latest": {"report_date": "2025-12-31"}},
        "valuation": {"pe_ttm": 6.1},
        "forecast": {"eps": 1.2},
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
        "interactive_qa": {"items": []},
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
            "financial_warning": {"level": "low"},
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
        portfolio_info if portfolio_info is not None else {"account": {}, "position": {}}
    )
    return static_context


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
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService, \
         patch("app.core.database.SessionLocal") as mock_session_local:
        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=ai_context)
        mock_db = MagicMock()
        mock_session_local.return_value.__enter__.return_value = mock_db
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

        mock_db.query.return_value.filter.return_value.first.side_effect = [mock_session, mock_account, mock_position]

        result = await fetch_context(initial_state)

        assert "portfolio_risk_control" not in result["static_context"]
        assert result["static_context"]["data"]["portfolio"]["risk_control"] == ai_context["portfolio"]["risk_control"]
        assert result["static_context"]["portfolio_info"]["account"] == {
            "total_assets": 1000000.0,
            "available_cash": 200000.0,
            "market_value": 800000.0,
        }

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

@pytest.mark.asyncio
async def test_full_workflow_integration(initial_state):
    """Integration test for the full graph flow using mocks for all external calls"""
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as MockService, \
         patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
         patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist, \
         patch(
             "app.trading.service.trading_service.execute_order_and_update_db",
             new_callable=AsyncMock,
         ) as mock_trade, \
         patch("app.core.database.SessionLocal") as mock_session_local, \
         patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", return_value={}):

        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=MOCK_CONTEXT)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session_local.return_value.__enter__.return_value = mock_db

        # Return different data based on the agent's expected output model
        def mock_run_side_effect(static_context, context=None):
            assert static_context == _expected_static_context()
            context = context or {}
            # If it's the PM, return PMDecision fields
            if "previous_pm_decision" in context:
                return {
                    "decision": "hold",
                    "confidence_score": 90.0,
                    "target_position": 0.0,
                    "verdict_summary": "Test verdict",
                    "investment_plan": "Test plan",
                    "price_range": "100-110",
                    "stop_loss": 95.0,
                    "risk_assessment": 0.1,
                    "execution_details": "Test details",
                    "report_markdown": "Test report"
                }
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
        assert "pm_decision" in final_state
        assert "trader_execution" not in final_state
        assert not final_state["errors"]
        
        # Total persists: 1 (news) + 1 (policy) + 1 (sentiment) + 4 (vertical) + 2 (round 1)
        # + 3 (round 2.1) + 1 (PM) = 13
        assert mock_persist.call_count == 13


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
        return _PersistQuery(self.session_obj)

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


class _SessionLocalContext:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        return False


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
    pm_report = PMDecision(
        decision="buy",
        confidence_score=88,
        target_position=0.4,
        verdict_summary="Bull case stronger",
        investment_plan="Build position in tranches",
        price_range="10-11",
        stop_loss=9.5,
        risk_assessment=0.2,
        execution_details="Start with half size",
        report_markdown="# PM report",
    )

    with patch("app.core.database.SessionLocal", return_value=_SessionLocalContext(fake_db)), \
         patch("app.api.endpoints.debate_ws.send_debate_message", new_callable=AsyncMock):
        await persist_agent_report(
            session_id=session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name=AGENT_NAME_PORTFOLIO_MANAGER,
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            report_content=pm_report,
            prompt_input="pm prompt",
        )

    assert len(fake_db.added) == 1
    assert fake_db.added[0].agent_role == AGENT_ROLE_PORTFOLIO_MANAGER
    assert fake_db.added[0].reasoning == "# PM report"
    assert fake_db.added[0].analysis["decision"] == "buy"
    assert fake_db.added[0].prompt_input == "pm prompt"


@pytest.mark.asyncio
async def test_portfolio_management_passes_expected_top_level_keys(initial_state):
    initial_state["vertical_reports"] = {"fundamental": "F"}
    initial_state["strategic_reports"] = {"bull": "B"}
    portfolio_info = {"account": {"total_assets": 1000000}, "position": {}}
    initial_state["static_context"] = _expected_static_context(portfolio_info)

    with patch("app.ai.llm_engine.orchestrator.PortfolioManagerAgent") as MockPM, \
         patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
         patch(
             "app.ai.llm_engine.orchestrator._get_previous_pm_decision",
             return_value={"decision": "buy", "target_position": 0.3},
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
            "previous_pm_decision",
            "vertical_views",
            "strategic_debate",
        }
        assert pm_runtime_context["previous_pm_decision"]["decision"] == "buy"
