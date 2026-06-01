from datetime import date, datetime, timedelta
import uuid

import pytest

from app.ai.experience import service as experience_service_module
from app.ai.experience.service import experience_service
from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER
from app.core.i18n import i18n_service
from app.models.data_storage import KlineData, StockBasic
from app.models.debate_message import DebateMessage
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.session import Session as DebateSession
from app.models.user import User


def _create_user_and_session(db_session):
    user = User(
        username=f"experience_{uuid.uuid4().hex[:8]}",
        email=f"experience_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend",
        status="completed",
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return user, session


def test_cleanup_interrupted_review_runs_marks_active_runs_failed(db_session):
    user, active_session = _create_user_and_session(db_session)

    completed_session = DebateSession(
        user_id=user.id,
        stock_code="000002.SZ",
        trading_frequency="position",
        trading_strategy="value",
        status="completed",
    )
    db_session.add_all([active_session, completed_session])
    db_session.commit()

    started_run_id = str(uuid.uuid4())
    running_run_id = str(uuid.uuid4())
    completed_run_id = str(uuid.uuid4())

    db_session.add_all(
        [
            ExperienceReviewEvent(
                review_run_id=started_run_id,
                session_id=active_session.session_id,
                user_id=user.id,
                stage="experience_review",
                status="started",
                message_key="experience.live_messages.started",
            ),
            ExperienceReviewEvent(
                review_run_id=running_run_id,
                session_id=active_session.session_id,
                user_id=user.id,
                stage="tool_call",
                status="running",
                message_key="experience.live_messages.tool_call",
            ),
            ExperienceReviewEvent(
                review_run_id=completed_run_id,
                session_id=completed_session.session_id,
                user_id=user.id,
                stage="experience_review",
                status="completed",
                message_key="experience.live_messages.completed",
            ),
        ]
    )
    db_session.commit()

    cleaned = experience_service._cleanup_interrupted_review_runs_in_db(db_session)

    assert cleaned == 2

    started_events = (
        db_session.query(ExperienceReviewEvent)
        .filter(ExperienceReviewEvent.review_run_id == started_run_id)
        .order_by(ExperienceReviewEvent.created_at.asc())
        .all()
    )
    running_events = (
        db_session.query(ExperienceReviewEvent)
        .filter(ExperienceReviewEvent.review_run_id == running_run_id)
        .order_by(ExperienceReviewEvent.created_at.asc())
        .all()
    )
    completed_events = (
        db_session.query(ExperienceReviewEvent)
        .filter(ExperienceReviewEvent.review_run_id == completed_run_id)
        .order_by(ExperienceReviewEvent.created_at.asc())
        .all()
    )

    failure_message = i18n_service.t("experience.review_interrupted_by_restart")

    assert started_events[-1].stage == "experience_review"
    assert started_events[-1].status == "failed"
    assert started_events[-1].message_key == "experience.live_messages.failed"
    assert started_events[-1].message_params == {"error": failure_message}
    assert started_events[-1].payload["reason"] == "restart_recovery"

    assert running_events[-1].stage == "tool_call"
    assert running_events[-1].status == "failed"
    assert running_events[-1].message_key == "experience.live_messages.failed"
    assert running_events[-1].message_params == {"error": failure_message}
    assert running_events[-1].payload["reason"] == "restart_recovery"

    assert len(completed_events) == 1
    assert completed_events[-1].status == "completed"


@pytest.mark.asyncio
async def test_analyze_allows_existing_completed_review_to_run_again(db_session, monkeypatch):
    user, session = _create_user_and_session(db_session)
    _create_stock_and_klines(db_session, count=21)
    existing_review_run_id = str(uuid.uuid4())
    db_session.add_all(
        [
            DebateMessage(
                session_id=session.session_id,
                stage="portfolio_manager",
                round_number=1,
                agent_name="PM",
                agent_role="portfolio_manager",
                decision="buy",
                confidence=0.82,
                reasoning="pm reasoning",
                analysis={"decision": "buy"},
                created_at=datetime(2026, 1, 1, 15, 0),
            ),
            ExperienceReviewEvent(
                review_run_id=existing_review_run_id,
                session_id=session.session_id,
                user_id=user.id,
                stage="experience_review",
                status="completed",
                message_key="experience.live_messages.completed",
            ),
        ]
    )
    db_session.commit()

    def fake_build_debate_review_context(*args, **kwargs):
        return {
            "pm_decision": {"decision": "buy", "target_position": 0.5},
            "market_outcome_summary": {"return_pct": 3.2},
        }

    class FakeWorkflow:
        async def ainvoke(self, state):
            return {
                "errors": [],
                "full_context": {},
                "analysis_payload": {
                    "recommended_action": "hold",
                    "debate_correctness": "correct",
                    "confidence_score": 80,
                },
                "tool_trace": [],
            }

    monkeypatch.setattr(type(experience_service), "_build_debate_review_context", fake_build_debate_review_context)
    monkeypatch.setattr(experience_service_module, "create_experience_workflow", lambda: FakeWorkflow())

    result = await experience_service.analyze(
        db_session,
        user_id=user.id,
        session_id=session.session_id,
    )

    rows = (
        db_session.query(ExperienceReviewEvent)
        .filter(ExperienceReviewEvent.session_id == session.session_id)
        .all()
    )
    review_run_ids = {row.review_run_id for row in rows}
    assert existing_review_run_id in review_run_ids
    assert result["review_run_id"] in review_run_ids
    assert result["review_run_id"] != existing_review_run_id


def test_list_debate_sessions_marks_existing_reviews(db_session):
    user, session = _create_user_and_session(db_session)
    db_session.add(
        DebateMessage(
            session_id=session.session_id,
            stage="portfolio_manager",
            round_number=1,
            agent_name="PM",
            agent_role="portfolio_manager",
            decision="buy",
            confidence=0.82,
            reasoning="pm reasoning",
            analysis={"decision": "buy"},
        )
    )
    db_session.add(
        ExperienceReviewEvent(
            review_run_id=str(uuid.uuid4()),
            session_id=session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
        )
    )
    db_session.commit()

    sessions = experience_service.list_debate_sessions(db_session, user_id=user.id)

    assert len(sessions) == 1
    assert sessions[0]["has_experience_review"] is True


def test_get_review_run_result_falls_back_from_completed_event_payload(db_session):
    user, session = _create_user_and_session(db_session)
    pm_message = DebateMessage(
        session_id=session.session_id,
        stage="portfolio_manager",
        round_number=1,
        agent_name="PM",
        agent_role="portfolio_manager",
        decision="buy",
        confidence=0.82,
        reasoning="pm reasoning",
        analysis={"decision": "buy"},
    )
    completed_run_id = str(uuid.uuid4())
    db_session.add(pm_message)
    db_session.add_all(
        [
            ExperienceReviewEvent(
                review_run_id=completed_run_id,
                session_id=session.session_id,
                user_id=user.id,
                stage="tool_call",
                status="completed",
                message_key="experience.live_messages.tool_call",
                payload={
                    "tool_name": "write_memory",
                    "args": {
                        "content": "平安银行复盘经验：上涨主因是估值修复和风险预期改善，追高前必须先确认基本面改善是否同步验证。",
                        "importance": "high",
                        "stock_code": "000001.SZ",
                    },
                },
            ),
            ExperienceReviewEvent(
                review_run_id=completed_run_id,
                session_id=session.session_id,
                user_id=user.id,
                stage="experience_review",
                status="completed",
                message_key="experience.live_messages.completed",
                payload={
                    "recommended_action": "buy",
                    "debate_correctness": "correct",
                    "tool_trace": [
                        {
                            "name": "write_memory",
                            "args": {
                                "content": "平安银行复盘经验：上涨主因是估值修复和风险预期改善，追高前必须先确认基本面改善是否同步验证。",
                                "importance": "high",
                                "stock_code": "000001.SZ",
                            },
                        }
                    ],
                },
            ),
        ]
    )
    db_session.commit()

    result = experience_service.get_review_run_result(
        db_session,
        user_id=user.id,
        review_run_id=completed_run_id,
    )

    assert result is not None
    assert result["review_run_id"] == completed_run_id
    assert result["session_id"] == str(session.session_id)
    assert result["stock_code"] == session.stock_code
    assert result["style_bucket"] == "swing"
    assert result["analysis_payload"]["recommended_action"] == "buy"
    assert result["analysis_payload"]["debate_correctness"] == "correct"
    assert result["analysis_payload"]["written_memories"] == [
        {
            "content": "平安银行复盘经验：上涨主因是估值修复和风险预期改善，追高前必须先确认基本面改善是否同步验证。",
            "importance": "high",
            "memory_scope": "stock",
            "stock_code": "000001.SZ",
        }
    ]
    assert result["tool_trace"] == [
        {
            "name": "write_memory",
            "args": {
                "content": "平安银行复盘经验：上涨主因是估值修复和风险预期改善，追高前必须先确认基本面改善是否同步验证。",
                "importance": "high",
                "stock_code": "000001.SZ",
            },
        }
    ]


def test_delete_review_run_and_clear_review_runs(db_session):
    user, session = _create_user_and_session(db_session)
    deletable_run_id = str(uuid.uuid4())
    remaining_run_id = str(uuid.uuid4())

    db_session.add_all(
        [
            ExperienceReviewEvent(
                review_run_id=deletable_run_id,
                session_id=session.session_id,
                user_id=user.id,
                stage="experience_review",
                status="completed",
                message_key="experience.live_messages.completed",
            ),
            ExperienceReviewEvent(
                review_run_id=remaining_run_id,
                session_id=session.session_id,
                user_id=user.id,
                stage="experience_review",
                status="failed",
                message_key="experience.live_messages.failed",
            ),
        ]
    )
    db_session.commit()

    deleted = experience_service.delete_review_run(
        db_session,
        user_id=user.id,
        review_run_id=deletable_run_id,
    )
    cleared = experience_service.delete_all_review_runs(
        db_session,
        user_id=user.id,
    )

    assert deleted is True
    assert cleared == 1
    assert db_session.query(ExperienceReviewEvent).count() == 0


def _create_stock_and_klines(db_session, *, stock_code="000001.SZ", industry="Bank", count=21):
    stock = StockBasic(
        stock_code=stock_code,
        name="Ping An Bank",
        industry=industry,
        market="SZSE",
    )
    db_session.add(stock)
    db_session.commit()
    db_session.add_all(
        [
            KlineData(
                stock_code=stock_code,
                date=date(2026, 1, 1) + timedelta(days=index),
                freq="D",
                open=10 + index,
                close=10.5 + index,
                high=11 + index,
                low=9.5 + index,
            )
            for index in range(count)
        ]
    )
    db_session.commit()

def _create_pm_decision(db_session, session, *, created_at=datetime(2026, 1, 1, 15, 0)):
    message = DebateMessage(
        session_id=session.session_id,
        stage="portfolio_manager",
        round_number=1,
        agent_name="PM",
        agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
        decision="buy",
        confidence=0.82,
        reasoning="pm reasoning",
        analysis={"decision": "buy"},
        created_at=created_at,
    )
    db_session.add(message)
    db_session.commit()
    return message


def test_list_review_candidates_returns_ready_horizons(db_session):
    user, session = _create_user_and_session(db_session)
    _create_stock_and_klines(db_session, count=21)
    _create_pm_decision(db_session, session)

    result = experience_service.list_review_candidates(db_session, user_id=user.id)

    assert result["summary"]["ready_20d"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["session_id"] == session.session_id
    assert item["stock_code"] == "000001.SZ"
    assert item["stock_name"] == "Ping An Bank"
    assert item["industry"] == "Bank"
    assert item["market_day_count"] == 21
    assert item["eligible_horizons"] == ["5d", "20d"]
    assert item["review_status"] == "ready_20d"
    assert item["next_horizon"] == "60d"
    assert item["days_until_next_horizon"] == 40


def test_list_review_candidates_hides_completed_horizon_but_keeps_later_ready_horizon(db_session):
    user, session = _create_user_and_session(db_session)
    _create_stock_and_klines(db_session, count=61)
    _create_pm_decision(db_session, session)
    db_session.add(
        ExperienceReviewEvent(
            review_run_id=str(uuid.uuid4()),
            session_id=session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
            payload={"review_horizon": "20d"},
        )
    )
    db_session.commit()

    result = experience_service.list_review_candidates(db_session, user_id=user.id)

    item = result["items"][0]
    assert item["eligible_horizons"] == ["5d", "20d", "60d"]
    assert item["latest_completed_horizons"] == ["20d"]
    assert item["review_status"] == "ready_60d"


def test_list_review_candidates_marks_not_ready_when_market_data_is_short(db_session):
    user, session = _create_user_and_session(db_session)
    _create_stock_and_klines(db_session, count=5)
    _create_pm_decision(db_session, session)

    result = experience_service.list_review_candidates(db_session, user_id=user.id)

    item = result["items"][0]
    assert item["eligible_horizons"] == []
    assert item["review_status"] == "not_ready"
    assert item["next_horizon"] == "5d"
    assert item["days_until_next_horizon"] == 1


@pytest.mark.asyncio
async def test_analyze_rejects_unavailable_review_horizon(db_session):
    user, session = _create_user_and_session(db_session)
    _create_stock_and_klines(db_session, count=6)
    _create_pm_decision(db_session, session)

    with pytest.raises(ValueError, match="requires 21 market days"):
        await experience_service.analyze(
            db_session,
            user_id=user.id,
            session_id=session.session_id,
            review_horizon="20d",
        )


@pytest.mark.asyncio
async def test_analyze_defaults_to_highest_available_review_horizon(db_session, monkeypatch):
    user, session = _create_user_and_session(db_session)
    _create_stock_and_klines(db_session, count=21)
    _create_pm_decision(db_session, session)

    class FakeWorkflow:
        async def ainvoke(self, state):
            assert state["review_horizon"] == "20d"
            assert state["market_day_count"] == 21
            return {
                "errors": [],
                "full_context": {},
                "analysis_payload": {
                    "thesis_summary": "PM 判断部分正确。",
                    "recommended_action": "hold",
                    "confidence_score": 80,
                    "debate_correctness": "partially_correct",
                    "correctness_score": 70,
                    "review_triads": {
                        "original_judgment": {
                            "verdict": "partially_correct",
                            "score": 70,
                            "pm_decision": "buy",
                            "outcome_basis": "20D outcome",
                            "reasoning": "上涨但回撤较大。",
                        },
                        "signal_validation": {
                            "validated_signals": [],
                            "invalidated_signals": [],
                            "noise_signals": [],
                        },
                        "decision_process_improvement": {
                            "debate_changes": ["补充行业相对收益。"],
                            "pm_changes": ["降低仓位。"],
                            "risk_control_changes": ["跌破关键位重新辩论。"],
                        },
                    },
                    "experience_tags": {
                        "stock_tags": ["000001.SZ"],
                        "industry_tags": ["Bank"],
                        "strategy_tags": ["trend"],
                        "failure_lesson_tags": [],
                        "position_discipline_tags": [],
                        "signal_tags": [],
                        "market_regime_tags": [],
                    },
                    "written_memories": [],
                },
                "tool_trace": [],
            }

    monkeypatch.setattr(experience_service_module, "create_experience_workflow", lambda: FakeWorkflow())

    result = await experience_service.analyze(
        db_session,
        user_id=user.id,
        session_id=session.session_id,
    )

    assert result["review_horizon"] == "20d"
    assert result["market_day_count"] == 21
    completed = (
        db_session.query(ExperienceReviewEvent)
        .filter(ExperienceReviewEvent.review_run_id == result["review_run_id"], ExperienceReviewEvent.status == "completed")
        .one()
    )
    assert completed.payload["review_horizon"] == "20d"
    assert completed.payload["market_day_count"] == 21


@pytest.mark.asyncio
async def test_analyze_defaults_to_20d_before_60d_when_both_are_available(db_session, monkeypatch):
    user, session = _create_user_and_session(db_session)
    _create_stock_and_klines(db_session, count=61)
    _create_pm_decision(db_session, session)

    class FakeWorkflow:
        async def ainvoke(self, state):
            assert state["review_horizon"] == "20d"
            assert state["market_day_count"] == 61
            return {
                "errors": [],
                "full_context": {},
                "analysis_payload": {
                    "thesis_summary": "PM 判断部分正确。",
                    "recommended_action": "hold",
                    "confidence_score": 80,
                    "debate_correctness": "partially_correct",
                    "correctness_score": 70,
                    "review_triads": {
                        "original_judgment": {
                            "verdict": "partially_correct",
                            "score": 70,
                            "pm_decision": "buy",
                            "outcome_basis": "20D outcome",
                            "reasoning": "上涨但回撤较大。",
                        },
                        "signal_validation": {
                            "validated_signals": [],
                            "invalidated_signals": [],
                            "noise_signals": [],
                        },
                        "decision_process_improvement": {
                            "debate_changes": ["补充行业相对收益。"],
                            "pm_changes": ["降低仓位。"],
                            "risk_control_changes": ["跌破关键位重新辩论。"],
                        },
                    },
                    "experience_tags": {},
                    "written_memories": [],
                },
                "tool_trace": [],
            }

    monkeypatch.setattr(experience_service_module, "create_experience_workflow", lambda: FakeWorkflow())

    result = await experience_service.analyze(
        db_session,
        user_id=user.id,
        session_id=session.session_id,
    )

    assert result["review_horizon"] == "20d"
