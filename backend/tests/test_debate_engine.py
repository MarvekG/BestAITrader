import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.ai.llm_engine.models import PMDecision
from app.ai.llm_engine.orchestrator import (
    create_analyst_workflow,
    _build_portfolio_field_descriptions,
    persist_agent_report,
    portfolio_management,
)
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER, AGENT_ROLE_PORTFOLIO_MANAGER


MOCK_CONTEXT = {
    "metadata": {"stock_code": "000001.SZ", "stock_name": "平安银行"},
    "realtime": {
        "market": {"price": 10.0},
        "indicators": {"rsi": 50},
        "money_flow": {"main_net_inflow": 1000},
        "index_reference": {"sh_index": 3200},
    },
    "snapshot": {
        "company": {"basic": {"industry": "银行"}},
        "financial_statements": {"financial_indicator_latest": {"report_date": "2025-12-31"}},
        "valuation": {"pe_ttm": 6.1},
        "forecast": {},
        "northbound": {},
        "ownership": {},
        "flow": {},
    },
    "history": {
        "kline": {"items": [{"close": 10.0}]},
        "money_flow_trend": {"items": []},
        "northbound_trend": {},
        "financial_trend": {},
        "insider_activity": {},
        "interactive_qa": {},
        "seo_history": {},
    },
    "signals": {
        "hot_rank": {"rank": 8},
        "flow": {},
        "risk": {},
    },
    "events": {
        "lockup_release": {},
        "regulatory": {},
    },
}


def test_pm_decision_requires_take_profit_and_holding_horizon_days():
    """PM 决策必须包含有效止盈目标和预期持有周期。"""
    with pytest.raises(ValueError):
        PMDecision(
            decision="buy",
            confidence_score=80,
            target_position=0.5,
            verdict_summary="Bull case is stronger",
            investment_plan="Build position gradually",
            price_range="9.8-10.2",
            stop_loss=9.5,
            take_profit=0,
            holding_horizon_days=20,
            risk_assessment=0.2,
            execution_details="Start with half target size",
            report_markdown="# PM Decision",
        )

    with pytest.raises(ValueError):
        PMDecision(
            decision="buy",
            confidence_score=80,
            target_position=0.5,
            verdict_summary="Bull case is stronger",
            investment_plan="Build position gradually",
            price_range="9.8-10.2",
            stop_loss=9.5,
            take_profit=11.5,
            holding_horizon_days=0,
            risk_assessment=0.2,
            execution_details="Start with half target size",
            report_markdown="# PM Decision",
        )


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


@pytest.fixture
def initial_state():
    return {
        "stock_code": "000001.SZ",
        "trading_frequency": "swing",
        "trading_strategy": "momentum",
        "session_id": uuid4(),
        "user_id": None,
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
        "errors": [],
    }


@pytest.mark.asyncio
async def test_current_workflow_runs_with_mocked_agents(initial_state):
    """
    当前辩论流程由 llm_engine.orchestrator 的 LangGraph workflow 驱动。
    """
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist, \
            patch("app.core.database.SessionLocal") as mock_session_local, \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", return_value={}):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session_local.return_value.__enter__.return_value = mock_db

        def agent_result(static_context, context=None):
            assert static_context == _expected_static_context()
            context = context or {}
            if "previous_pm_decision" in context:
                return PMDecision(
                    decision="buy",
                    confidence_score=80,
                    target_position=0.5,
                    verdict_summary="Bull case is stronger",
                    investment_plan="Build position gradually",
                    price_range="9.8-10.2",
                    stop_loss=9.5,
                    take_profit=11.5,
                    holding_horizon_days=20,
                    risk_assessment=0.2,
                    execution_details="Start with half target size",
                    report_markdown="# PM Decision",
                )
            return "Mock agent report"

        mock_agent_run.side_effect = agent_result

        final_state = await create_analyst_workflow().ainvoke(initial_state)

    assert final_state["pm_decision"]["decision"] == "buy"
    assert final_state["pm_decision"]["target_position"] == 0.5
    assert not final_state["errors"]
    assert mock_persist.call_count == 14


@pytest.mark.asyncio
async def test_analyst_workflow_preserves_market_watch_trigger_context(initial_state):
    """辩论工作流应保留启动方传入的盯盘触发原因。"""
    trigger_context = {
        "source": "market_watch",
        "trigger_reason": "Strong anomaly and news context",
        "evidence_summary": "Quote anomaly and news are aligned.",
    }
    initial_state["static_context"] = {"market_watch_trigger": trigger_context}

    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
            patch("app.core.database.SessionLocal") as mock_session_local, \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", return_value={}):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session_local.return_value.__enter__.return_value = mock_db

        def agent_result(static_context, context=None):
            assert static_context["market_watch_trigger"] == trigger_context
            context = context or {}
            if "previous_pm_decision" in context:
                return PMDecision(
                    decision="buy",
                    confidence_score=80,
                    target_position=0.5,
                    verdict_summary="Bull case is stronger",
                    investment_plan="Build position gradually",
                    price_range="9.8-10.2",
                    stop_loss=9.5,
                    take_profit=11.5,
                    holding_horizon_days=20,
                    risk_assessment=0.2,
                    execution_details="Start with half target size",
                    report_markdown="# PM Decision",
                )
            return "Mock agent report"

        mock_agent_run.side_effect = agent_result

        final_state = await create_analyst_workflow().ainvoke(initial_state)

    assert final_state["static_context"]["market_watch_trigger"] == trigger_context
    assert not final_state["errors"]


@pytest.mark.asyncio
async def test_analyst_workflow_allows_agent_calls_in_parallel_by_default(initial_state, monkeypatch):
    monkeypatch.setattr(
        "app.ai.llm_routing.settings.DEBATE_AGENT_PARALLEL_ENABLED",
        True,
    )
    active_calls = 0
    max_active_calls = 0

    async def agent_result(static_context, context=None):
        nonlocal active_calls, max_active_calls
        assert static_context == _expected_static_context()
        context = context or {}
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        if "previous_pm_decision" in context:
            return PMDecision(
                decision="buy",
                confidence_score=80,
                target_position=0.5,
                verdict_summary="Bull case is stronger",
                investment_plan="Build position gradually",
                price_range="9.8-10.2",
                stop_loss=9.5,
                take_profit=11.5,
                holding_horizon_days=20,
                risk_assessment=0.2,
                execution_details="Start with half target size",
                report_markdown="# PM Decision",
            )
        return "Mock agent report"

    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
            patch("app.core.database.SessionLocal") as mock_session_local, \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", return_value={}):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session_local.return_value.__enter__.return_value = mock_db
        mock_agent_run.side_effect = agent_result

        final_state = await create_analyst_workflow().ainvoke(initial_state)

    assert not final_state["errors"]
    assert max_active_calls > 1


@pytest.mark.asyncio
async def test_analyst_workflow_runs_agent_calls_serially_when_env_parallel_disabled(initial_state, monkeypatch):
    monkeypatch.setattr(
        "app.ai.llm_routing.settings.DEBATE_AGENT_PARALLEL_ENABLED",
        False,
    )
    active_calls = 0
    max_active_calls = 0

    async def agent_result(static_context, context=None):
        nonlocal active_calls, max_active_calls
        assert static_context == _expected_static_context()
        context = context or {}
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        if "previous_pm_decision" in context:
            return PMDecision(
                decision="buy",
                confidence_score=80,
                target_position=0.5,
                verdict_summary="Bull case is stronger",
                investment_plan="Build position gradually",
                price_range="9.8-10.2",
                stop_loss=9.5,
                take_profit=11.5,
                holding_horizon_days=20,
                risk_assessment=0.2,
                execution_details="Start with half target size",
                report_markdown="# PM Decision",
            )
        return "Mock agent report"

    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
            patch("app.core.database.SessionLocal") as mock_session_local, \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", return_value={}):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_session_local.return_value.__enter__.return_value = mock_db
        mock_agent_run.side_effect = agent_result

        final_state = await create_analyst_workflow().ainvoke(initial_state)

    assert not final_state["errors"]
    assert max_active_calls == 1


@pytest.mark.asyncio
async def test_portfolio_management_returns_current_pm_decision_schema(initial_state):
    """
    PM 节点返回当前 PMDecision schema，而不是旧版 final_decision 包装结构。
    """
    initial_state["vertical_reports"] = {"fundamental": "fundamental report"}
    initial_state["strategic_reports"] = {"bull": "bull report", "bear": "bear report"}
    portfolio_info = {"account": {"total_assets": 100000}, "position": {}}
    initial_state["static_context"] = _expected_static_context(portfolio_info)

    pm_decision = PMDecision(
        decision="sell",
        confidence_score=90,
        target_position=0.0,
        verdict_summary="Risk dominates",
        investment_plan="Exit position",
        price_range="market",
        stop_loss=9.0,
        take_profit=10.5,
        holding_horizon_days=10,
        risk_assessment=0.8,
        execution_details="Sell all available shares",
        report_markdown="# Sell",
    )

    with patch("app.ai.llm_engine.orchestrator.PortfolioManagerAgent") as mock_pm_agent, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist, \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", return_value={"decision": "hold"}), \
            patch("app.ai.llm_engine.orchestrator._get_same_stock_history", return_value={}):
        agent = mock_pm_agent.return_value
        agent.last_prompt = "pm prompt"
        agent.run = AsyncMock(return_value=pm_decision)

        result = await portfolio_management(initial_state)

    assert result["pm_decision"]["decision"] == "sell"
    assert result["pm_decision"]["confidence_score"] == 90
    pm_snapshot, pm_runtime_context = agent.run.await_args.args
    assert pm_snapshot == _expected_static_context(portfolio_info)
    assert pm_runtime_context["previous_pm_decision"]["decision"] == "hold"
    assert mock_persist.await_count == 1


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
        return None

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
async def test_persist_agent_report_saves_current_pm_decision():
    """
    持久化层保存当前 PMDecision 为 DebateMessage。
    """
    session_id = uuid4()
    fake_session = SimpleNamespace(session_id=session_id, user_id=1)
    fake_db = _PersistDb(fake_session)
    pm_decision = PMDecision(
        decision="hold",
        confidence_score=75,
        target_position=0.3,
        verdict_summary="Wait for confirmation",
        investment_plan="Hold current position",
        price_range="10-11",
        stop_loss=9.2,
        take_profit=12.0,
        holding_horizon_days=20,
        risk_assessment=0.3,
        execution_details="No immediate trade",
        report_markdown="# Hold",
    )

    with patch("app.core.database.SessionLocal", return_value=_SessionLocalContext(fake_db)), \
            patch("app.api.endpoints.debate_ws.send_debate_message", new_callable=AsyncMock):
        await persist_agent_report(
            session_id=session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name=AGENT_NAME_PORTFOLIO_MANAGER,
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            report_content=pm_decision,
            prompt_input="pm prompt",
        )

    assert len(fake_db.added) == 1
    saved_message = fake_db.added[0]
    assert saved_message.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER
    assert saved_message.decision == "hold"
    assert saved_message.confidence == 0.75
    assert saved_message.reasoning == "# Hold"
    assert saved_message.analysis["target_position"] == 0.3
    assert saved_message.analysis["take_profit"] == 12.0
    assert saved_message.analysis["holding_horizon_days"] == 20
