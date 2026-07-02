import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.ai.llm_engine.agents.governance import PortfolioManagerAgent
from app.ai.llm_engine.orchestrator import (
    create_analyst_workflow,
    _build_portfolio_field_descriptions,
    persist_agent_report,
    portfolio_management,
)
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER, AGENT_ROLE_PORTFOLIO_MANAGER
from app.models.pm_decision import PMDecisionRecord
from app.models.session import Session as DebateSession
from app.models.user import User


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
        "financial_statements": {
            "financial_indicator": {"items": [{"meta": {"report_date": "2025-12-31"}}]},
        },
        "valuation": {"pe_ttm": 6.1},
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
    return SimpleNamespace(to_dict=lambda: {"confidence_score": 90, "target_position": 0.0})


@pytest.mark.asyncio
async def test_save_pm_decision_record_persists_minimal_fields(async_db_session):
    """PM 结构化决策保存到独立表，且同会话再次保存会更新原记录。"""
    from app.ai.llm_engine.pm_decision_service import save_pm_decision_record

    user = User(
        username="pm_decision_user",
        email="pm_decision_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
    session_obj = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="momentum",
    )
    async_db_session.add(session_obj)
    await async_db_session.commit()
    session_id = session_obj.session_id

    with patch("app.ai.llm_engine.pm_decision_service.sync_pm_discipline_to_position", new_callable=AsyncMock):
        first = await save_pm_decision_record(
            session_id=session_id,
            target_position=0.3,
            confidence_score=80,
            stop_loss=9.5,
            take_profit=12.0,
            holding_horizon_days=20,
        )
        second = await save_pm_decision_record(
            session_id=session_id,
            target_position=0.3,
            confidence_score=65,
        )

    rows = (
        await async_db_session.execute(select(PMDecisionRecord).where(PMDecisionRecord.session_id == session_id))
    ).scalars().all()
    assert len(rows) == 1
    assert first["decision_id"] == second["decision_id"]
    assert rows[0].confidence_score == 65


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
    fake_db = MagicMock()
    fake_db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist, \
            patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)), \
            patch(
                "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
                new_callable=AsyncMock,
                return_value=_saved_pm_record().to_dict(),
            ), \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, return_value=[]):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)

        def agent_result(static_context, context=None):
            assert static_context == _expected_static_context()
            context = context or {}
            if "previous_pm_decision" in context:
                return "# PM Decision"
            return "Mock agent report"

        mock_agent_run.side_effect = agent_result

        final_state = await create_analyst_workflow().ainvoke(initial_state)

    assert "pm_decision" in final_state
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

    fake_db = MagicMock()
    fake_db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
            patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)), \
            patch(
                "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
                new_callable=AsyncMock,
                return_value=_saved_pm_record().to_dict(),
            ), \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, return_value=[]):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)

        def agent_result(static_context, context=None):
            assert static_context["market_watch_trigger"] == trigger_context
            context = context or {}
            if "previous_pm_decision" in context:
                return "# PM Decision"
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
            return "# PM Decision"
        return "Mock agent report"

    fake_db = MagicMock()
    fake_db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
            patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)), \
            patch(
                "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
                new_callable=AsyncMock,
                return_value=_saved_pm_record().to_dict(),
            ), \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, return_value=[]):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)
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
            return "# PM Decision"
        return "Mock agent report"

    fake_db = MagicMock()
    fake_db.execute.return_value = SimpleNamespace(scalar_one_or_none=lambda: None)
    with patch("app.ai.llm_engine.orchestrator.AIContextService") as mock_context_service, \
            patch("app.ai.llm_engine.agents.base.BaseAgent.run", new_callable=AsyncMock) as mock_agent_run, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
            patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)), \
            patch(
                "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
                new_callable=AsyncMock,
                return_value=_saved_pm_record().to_dict(),
            ), \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, return_value=[]):
        mock_context_service.return_value.build = AsyncMock(return_value=MOCK_CONTEXT)
        mock_agent_run.side_effect = agent_result

        final_state = await create_analyst_workflow().ainvoke(initial_state)

    assert not final_state["errors"]
    assert max_active_calls == 1


@pytest.mark.asyncio
async def test_portfolio_management_returns_saved_pm_decision(initial_state):
    """
    PM 节点返回工具保存的结构化决策，而不是从最终 Markdown 解析字段。
    """
    initial_state["vertical_reports"] = {"fundamental": "fundamental report"}
    initial_state["strategic_reports"] = {"bull": "bull report", "bear": "bear report"}
    portfolio_info = {"account": {"total_assets": 100000}, "position": {}}
    initial_state["static_context"] = _expected_static_context(portfolio_info)

    saved_pm_decision = SimpleNamespace(
        to_dict=lambda: {"confidence_score": 90, "target_position": 0.0}
    )

    with patch("app.ai.llm_engine.orchestrator.PortfolioManagerAgent") as mock_pm_agent, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock) as mock_persist, \
            patch(
                "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
                new_callable=AsyncMock,
                return_value=saved_pm_decision.to_dict(),
            ), \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={"decision": "hold"}), \
            patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, return_value=[]):
        agent = mock_pm_agent.return_value
        agent.last_prompt = "pm prompt"
        agent.run = AsyncMock(return_value="# Sell")

        result = await portfolio_management(initial_state)

    assert result["pm_decision"]["confidence_score"] == 90
    pm_snapshot, pm_runtime_context = agent.run.await_args.args
    assert pm_snapshot == _expected_static_context(portfolio_info)
    assert pm_runtime_context["previous_pm_decision"]["decision"] == "hold"
    assert mock_persist.await_count == 1


@pytest.mark.asyncio
async def test_portfolio_management_reports_pm_agent_errors(initial_state):
    """PM Agent 抛出不可恢复错误时，工作流应失败而不是静默完成。"""
    initial_state["static_context"] = _expected_static_context()

    with patch("app.ai.llm_engine.orchestrator.PortfolioManagerAgent") as mock_pm_agent, \
            patch("app.ai.llm_engine.orchestrator.persist_agent_report", new_callable=AsyncMock), \
            patch("app.ai.llm_engine.orchestrator._get_previous_pm_decision", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_same_stock_history", new_callable=AsyncMock, return_value={}), \
            patch("app.ai.llm_engine.orchestrator._get_pending_orders_for_pm", new_callable=AsyncMock, return_value=[]):
        agent = mock_pm_agent.return_value
        agent.last_prompt = "pm prompt"
        agent.run = AsyncMock(side_effect=ValueError("PM structured decision requires session_id"))

        result = await portfolio_management(initial_state)

    assert result == {"errors": ["PM Error: PM structured decision requires session_id"]}


@pytest.mark.asyncio
async def test_pm_agent_requests_save_tool_before_accepting_final_output():
    """PM Agent 接受最终 Markdown 前会要求模型先调用 save_pm_decision。"""
    agent = PortfolioManagerAgent(state={"session_id": str(uuid4())})

    with patch("app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session", new_callable=AsyncMock, return_value=None):
        feedback = await agent.get_final_output_feedback("# PM Decision")

    assert feedback is not None
    assert "save_pm_decision" in feedback


@pytest.mark.asyncio
async def test_pm_agent_save_decision_tool_injects_session_id(monkeypatch):
    """PM 专属 save_pm_decision 工具应注入 session_id 并调用保存服务。"""
    session_id = str(uuid4())
    captured = {}

    async def fake_save_pm_decision_record(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        "app.ai.llm_engine.agents.governance.save_pm_decision_record",
        fake_save_pm_decision_record,
    )

    agent = PortfolioManagerAgent(state={"session_id": session_id})
    wrapped_tool = next(tool for tool in agent.tools if tool.name == "save_pm_decision")
    result = await wrapped_tool.ainvoke({
        "target_position": 0,
        "confidence_score": 70,
        "stop_loss": 0,
        "take_profit": 0,
        "holding_horizon_days": 0,
    })

    assert result["success"] is True
    assert captured == {
        "session_id": session_id,
        "target_position": 0,
        "confidence_score": 70,
        "stop_loss": 0,
        "take_profit": 0,
        "holding_horizon_days": 0,
    }


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
        return self.db.execute(statement)

    def add(self, obj):
        self.db.add(obj)

    async def commit(self):
        self.db.commit()

    async def refresh(self, obj):
        self.db.refresh(obj)


@pytest.mark.asyncio
async def test_persist_agent_report_saves_pm_markdown_and_saved_decision_snapshot():
    """
    持久化层保存 PM Markdown，并把已保存结构化决策快照写入 DebateMessage。
    """
    session_id = uuid4()
    fake_session = SimpleNamespace(session_id=session_id, user_id=1)
    fake_db = _PersistDb(fake_session)
    saved_pm_decision = SimpleNamespace(
        confidence_score=75,
        to_dict=lambda: {
            "confidence_score": 75,
            "target_position": 0.3,
            "take_profit": 12.0,
            "holding_horizon_days": 20,
        },
    )

    with patch("app.core.database.AsyncSessionLocal", return_value=_AsyncSessionLocalContext(fake_db)), \
            patch(
                "app.ai.llm_engine.pm_decision_service.get_pm_decision_for_session",
                new_callable=AsyncMock,
                return_value=saved_pm_decision,
            ), \
            patch("app.api.endpoints.debate_ws.send_debate_message", new_callable=AsyncMock):
        await persist_agent_report(
            session_id=session_id,
            stage="portfolio_management",
            round_number=0,
            agent_name=AGENT_NAME_PORTFOLIO_MANAGER,
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            report_content="# Hold",
            prompt_input="pm prompt",
        )

    assert len(fake_db.added) == 1
    saved_message = fake_db.added[0]
    assert saved_message.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER
    assert saved_message.reasoning == "# Hold"
