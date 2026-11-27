from datetime import date, datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, select, update

from app.ai.experience import service as experience_service_module
from app.ai.experience.service import ExperienceReviewConflict, experience_service
from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER
from app.core.i18n import i18n_service
from app.models.data_storage import IndexDaily, KlineData, StockBasic
from app.models.debate_message import DebateMessage
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.experience_review_run import ExperienceReviewRun
from app.models.pm_decision import PMDecisionRecord
from app.models.session import Session as DebateSession
from app.models.trade_record import TradeRecord
from app.models.user import User


async def _create_user_and_session(async_db_session):
    user = User(
        username=f"experience_{uuid.uuid4().hex[:8]}",
        email=f"experience_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.commit()
    await async_db_session.refresh(user)

    session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend",
        status="completed",
    )
    async_db_session.add(session)
    await async_db_session.commit()
    await async_db_session.refresh(session)
    return user, session


async def _create_pm_record(async_db_session, user, session, *, created_at=datetime(2026, 1, 1, 15, 0)):
    record = PMDecisionRecord(
        session_id=session.session_id,
        user_id=user.id,
        stock_code=session.stock_code,
        target_position=0.5,
        confidence_score=82,
        stop_loss=9.5,
        take_profit=12.0,
        holding_horizon_days=20,
        created_at=created_at,
    )
    async_db_session.add(record)
    await async_db_session.commit()
    return record


@pytest.mark.asyncio
async def test_cleanup_interrupted_review_runs_marks_active_runs_failed(async_db_session):
    user, active_session = await _create_user_and_session(async_db_session)

    completed_session = DebateSession(
        user_id=user.id,
        stock_code="000002.SZ",
        trading_frequency="position",
        trading_strategy="value",
        status="completed",
    )
    async_db_session.add(completed_session)
    await async_db_session.commit()

    started_run_id = str(uuid.uuid4())
    running_run_id = str(uuid.uuid4())
    completed_run_id = str(uuid.uuid4())

    async_db_session.add_all(
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
    await async_db_session.commit()

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        cleaned = await experience_service._cleanup_interrupted_review_runs_in_db(db)

    assert cleaned == 2

    started_events = list(
        (
            await async_db_session.execute(
                select(ExperienceReviewEvent)
                .where(ExperienceReviewEvent.review_run_id == started_run_id)
                .order_by(ExperienceReviewEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    running_events = list(
        (
            await async_db_session.execute(
                select(ExperienceReviewEvent)
                .where(ExperienceReviewEvent.review_run_id == running_run_id)
                .order_by(ExperienceReviewEvent.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    completed_events = list(
        (
            await async_db_session.execute(
                select(ExperienceReviewEvent)
                .where(ExperienceReviewEvent.review_run_id == completed_run_id)
                .order_by(ExperienceReviewEvent.created_at.asc())
            )
        )
        .scalars()
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
async def test_analyze_allows_existing_completed_review_to_run_again(async_db_session, monkeypatch):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=21)
    existing_review_run_id = str(uuid.uuid4())
    await _create_pm_record(async_db_session, user, session)
    async_db_session.add_all(
        [
            DebateMessage(
                session_id=session.session_id,
                stage="portfolio_manager",
                round_number=1,
                agent_name="PM",
                agent_role="portfolio_manager",
                decision="buy",
                reasoning="pm reasoning",
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
    await async_db_session.commit()

    async def fake_build_debate_review_context(*args, **kwargs):
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
        user_id=user.id,
        session_id=session.session_id,
    )

    rows = list(
        (
            await async_db_session.execute(
                select(ExperienceReviewEvent).where(ExperienceReviewEvent.session_id == session.session_id)
            )
        )
        .scalars()
        .all()
    )
    review_run_ids = {row.review_run_id for row in rows}
    assert existing_review_run_id in review_run_ids
    assert result["review_run_id"] in review_run_ids
    assert result["review_run_id"] != existing_review_run_id


@pytest.mark.asyncio
async def test_list_debate_sessions_marks_existing_reviews(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_pm_record(async_db_session, user, session)
    async_db_session.add(
        DebateMessage(
            session_id=session.session_id,
            stage="portfolio_manager",
            round_number=1,
            agent_name="PM",
            agent_role="portfolio_manager",
            decision="buy",
            reasoning="pm reasoning",
        )
    )
    async_db_session.add(
        ExperienceReviewEvent(
            review_run_id=str(uuid.uuid4()),
            session_id=session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
        )
    )
    await async_db_session.commit()

    sessions = await experience_service.list_debate_sessions(user_id=user.id)

    assert len(sessions) == 1
    assert sessions[0]["has_experience_review"] is True


@pytest.mark.asyncio
async def test_get_review_run_result_falls_back_from_completed_event_payload(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_pm_record(async_db_session, user, session)
    pm_message = DebateMessage(
        session_id=session.session_id,
        stage="portfolio_manager",
        round_number=1,
        agent_name="PM",
        agent_role="portfolio_manager",
        decision="buy",
        reasoning="pm reasoning",
    )
    completed_run_id = str(uuid.uuid4())
    async_db_session.add(pm_message)
    async_db_session.add_all(
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
    await async_db_session.commit()

    result = await experience_service.get_review_run_result(
        user_id=user.id,
        review_run_id=completed_run_id,
    )

    assert result is not None
    assert result["review_run_id"] == completed_run_id
    assert result["session_id"] == str(session.session_id)
    assert result["stock_code"] == session.stock_code
    assert result["style_bucket"] == "swing"
    assert result["analysis_payload"]["original_pm_decision"] == "buy"
    assert result["analysis_payload"]["debate_correctness"] == "correct"
    assert result["analysis_payload"]["written_memories"] == [
        {
            "status": "unknown",
            "stock_code": "000001.SZ",
            "max_chars": experience_service_module.settings.MEMORY_DOC_MAX_CHARS,
            "size_chars": len("平安银行复盘经验：上涨主因是估值修复和风险预期改善，追高前必须先确认基本面改善是否同步验证。"),
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


@pytest.mark.asyncio
async def test_delete_review_run_and_clear_review_runs(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    deletable_run_id = str(uuid.uuid4())
    remaining_run_id = str(uuid.uuid4())

    async_db_session.add_all(
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
    await async_db_session.commit()

    deleted = await experience_service.delete_review_run(
        user_id=user.id,
        review_run_id=deletable_run_id,
    )
    cleared = await experience_service.delete_all_review_runs(
        user_id=user.id,
    )

    assert deleted is True
    assert cleared == 1
    count = await async_db_session.scalar(select(func.count()).select_from(ExperienceReviewEvent))
    assert count == 0


async def _create_stock_and_klines(async_db_session, *, stock_code="000001.SZ", industry="Bank", count=21):
    stock = StockBasic(
        stock_code=stock_code,
        name="Ping An Bank",
        industry=industry,
        market="SZSE",
    )
    async_db_session.add(stock)
    await async_db_session.commit()
    async_db_session.add_all(
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
    await async_db_session.commit()


async def _create_pm_decision(async_db_session, user, session, *, created_at=datetime(2026, 1, 1, 15, 0)):
    await _create_pm_record(async_db_session, user, session, created_at=created_at)
    message = DebateMessage(
        session_id=session.session_id,
        stage="portfolio_manager",
        round_number=1,
        agent_name="PM",
        agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
        decision="buy",
        reasoning="pm reasoning",
        created_at=created_at,
    )
    async_db_session.add(message)
    await async_db_session.commit()
    return message


@pytest.mark.asyncio
async def test_build_debate_review_context_returns_raw_data_only(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=21)
    async_db_session.add_all(
        [
            IndexDaily(
                index_code="000300.SH",
                trade_date=date(2026, 1, 1) + timedelta(days=index),
                open=1000 + index * 10,
                close=1000 + index * 10,
            )
            for index in range(21)
        ]
    )
    await async_db_session.commit()
    pm_message = await _create_pm_decision(async_db_session, user, session)
    async_db_session.add_all(
        [
            TradeRecord(
                session_id=session.session_id,
                stock_code=session.stock_code,
                action="buy",
                quantity=100,
                fill_price=8.0,
                trade_time=datetime(2026, 1, 1, 15, 5),
            ),
            TradeRecord(
                session_id=session.session_id,
                stock_code=session.stock_code,
                action="sell",
                quantity=100,
                fill_price=99.0,
                trade_time=datetime(2026, 1, 2, 10, 0),
            ),
        ]
    )
    await async_db_session.commit()

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        context = await experience_service._build_debate_review_context(
            db,
            session_obj=session,
            stock_name="Ping An Bank",
            industry="Bank",
            debate_messages=[pm_message],
            pm_message=pm_message,
            review_horizon="20d",
            market_day_count=21,
        )

    assert "execution_summary" not in context
    assert context["orders"] == []
    assert context["trades"][0]["action"] == "buy"
    assert context["trades"][0]["quantity"] == 100
    assert context["trades"][0]["fill_price"] == 8.0

    market_outcome = context["market_outcome_summary"]
    assert set(market_outcome) == {
        "stock_code",
        "decision_date",
        "selected_horizon",
        "data_range",
        "time_range",
        "review_window",
        "latest_window",
        "holding_window",
        "benchmark_window",
    }
    assert "entry_price" not in market_outcome
    assert "selected_horizon_outcome" not in market_outcome
    assert "horizon_returns" not in market_outcome

    review_window = market_outcome["review_window"]
    assert review_window["trading_days"] == 21
    assert review_window["rows"][0] == {
        "date": "2026-01-01",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": None,
        "turnover": None,
    }
    assert review_window["rows"][-1]["close"] == 30.5

    holding_window = market_outcome["holding_window"]
    assert holding_window["deadline_date"] == "2026-01-21"
    assert holding_window["reached"] is True
    assert holding_window["trading_days"] == 21

    assert market_outcome["benchmark_window"]["trading_days"] == 21
    assert market_outcome["data_range"]["review"]["trading_days"] == 21
    assert market_outcome["data_range"]["holding"]["trading_days"] == 21
    assert market_outcome["time_range"] == {
        "start_date": "2026-01-01",
        "end_date": "2026-01-21",
    }


@pytest.mark.asyncio
async def test_build_market_outcome_summary_returns_raw_review_and_latest_windows(async_db_session):
    stock_code = "000001.SZ"
    await _create_stock_and_klines(async_db_session, stock_code=stock_code, count=200)

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        market_outcome = await experience_service._build_market_outcome_summary(
            db,
            stock_code=stock_code,
            decision_time=datetime(2026, 1, 1, 15, 0),
            review_horizon="60d",
        )

    review_window = market_outcome["review_window"]
    assert review_window["horizon"] == "60d"
    assert review_window["start_date"] == "2026-01-01"
    assert review_window["end_date"] == "2026-03-02"
    assert review_window["trading_days"] == 61
    assert len(review_window["rows"]) == 61

    latest_window = market_outcome["latest_window"]
    assert latest_window["trading_days"] == 30
    assert latest_window["start_date"] == "2026-06-20"
    assert latest_window["end_date"] == "2026-07-19"
    assert latest_window["rows"][-1]["close"] == 209.5

    assert market_outcome["data_range"]["latest"]["trading_days"] == 30
    assert market_outcome["time_range"] == {
        "start_date": "2026-01-01",
        "end_date": "2026-07-19",
    }
    assert "close_60d_return" not in market_outcome
    assert "max_drawdown_60d" not in market_outcome


@pytest.mark.asyncio
async def test_build_market_outcome_summary_marks_unreached_holding_window(async_db_session):
    stock_code = "000001.SZ"
    await _create_stock_and_klines(async_db_session, stock_code=stock_code, count=21)

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        market_outcome = await experience_service._build_market_outcome_summary(
            db,
            stock_code=stock_code,
            decision_time=datetime(2026, 1, 1, 15, 0),
            review_horizon="20d",
            holding_horizon_days=60,
        )

    holding_window = market_outcome["holding_window"]
    assert holding_window["deadline_date"] == "2026-03-02"
    assert holding_window["reached"] is False
    assert holding_window["end_date"] == "2026-01-21"
    assert holding_window["trading_days"] == 21


@pytest.mark.asyncio
async def test_build_market_outcome_summary_returns_raw_benchmark_rows(async_db_session):
    stock_code = "000001.SZ"
    await _create_stock_and_klines(async_db_session, stock_code=stock_code, count=21)
    async_db_session.add_all(
        [
            IndexDaily(
                index_code="000300.SH",
                trade_date=date(2026, 1, 1) + timedelta(days=index),
                open=1000 + index * 10,
                close=1000 + index * 10,
            )
            for index in range(21)
        ]
    )
    await async_db_session.commit()

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        market_outcome = await experience_service._build_market_outcome_summary(
            db,
            stock_code=stock_code,
            decision_time=datetime(2026, 1, 1, 15, 0),
            review_horizon="20d",
        )

    benchmark_window = market_outcome["benchmark_window"]
    assert benchmark_window["index_code"] == "000300.SH"
    assert benchmark_window["trading_days"] == 21
    assert benchmark_window["rows"][0] == {
        "date": "2026-01-01",
        "open": 1000.0,
        "high": None,
        "low": None,
        "close": 1000.0,
        "volume": None,
    }
    assert benchmark_window["rows"][-1]["close"] == 1200.0
    assert market_outcome["data_range"]["benchmark"]["index_code"] == "000300.SH"
    assert "relative_return_vs_index" not in market_outcome


@pytest.mark.asyncio
async def test_build_market_outcome_summary_returns_empty_when_rows_are_insufficient(async_db_session):
    stock_code = "000003.SZ"
    async_db_session.add(
        StockBasic(
            stock_code=stock_code,
            name="Missing Close Stock",
            industry="Bank",
            market="SZSE",
        )
    )
    await async_db_session.flush()
    async_db_session.add_all(
        [
            KlineData(
                stock_code=stock_code,
                date=date(2026, 1, 1) + timedelta(days=index),
                freq="D",
                open=10 + index,
                close=None,
                high=11 + index,
                low=9 + index,
            )
            for index in range(3)
        ]
    )
    await async_db_session.commit()

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        market_outcome = await experience_service._build_market_outcome_summary(
            db,
            stock_code=stock_code,
            decision_time=datetime(2026, 1, 1, 15, 0),
            review_horizon="5d",
        )

    assert market_outcome == {}


@pytest.mark.asyncio
async def test_list_review_candidates_returns_ready_horizons(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=21)
    await _create_pm_decision(async_db_session, user, session)

    result = await experience_service.list_review_candidates(user_id=user.id)

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


@pytest.mark.asyncio
async def test_list_review_candidates_hides_completed_horizon_but_keeps_later_ready_horizon(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=61)
    await _create_pm_decision(async_db_session, user, session)
    async_db_session.add(
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
    await async_db_session.commit()

    result = await experience_service.list_review_candidates(user_id=user.id)

    item = result["items"][0]
    assert item["eligible_horizons"] == ["5d", "20d", "60d"]
    assert item["latest_completed_horizons"] == ["20d"]
    assert item["review_status"] == "ready_60d"


@pytest.mark.asyncio
async def test_list_review_candidates_marks_not_ready_when_market_data_is_short(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=5)
    await _create_pm_decision(async_db_session, user, session)

    result = await experience_service.list_review_candidates(user_id=user.id)

    item = result["items"][0]
    assert item["eligible_horizons"] == []
    assert item["review_status"] == "not_ready"
    assert item["next_horizon"] == "5d"
    assert item["days_until_next_horizon"] == 1


@pytest.mark.asyncio
async def test_enqueue_review_creates_queued_run(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=6)
    await _create_pm_decision(async_db_session, user, session)

    result = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=session.session_id,
        review_horizon="5d",
    )

    assert result["status"] == "queued"
    assert result["queue_position"] == 1
    run = (
        await async_db_session.execute(
            select(ExperienceReviewRun).where(
                ExperienceReviewRun.review_run_id == result["review_run_id"]
            )
        )
    ).scalar_one()
    assert run.status == "queued"
    assert run.review_horizon == "5d"
    assert run.stock_code == session.stock_code
    queued_event = (
        await async_db_session.execute(
            select(ExperienceReviewEvent).where(
                ExperienceReviewEvent.review_run_id == result["review_run_id"],
                ExperienceReviewEvent.status == "queued",
            )
        )
    ).scalar_one()
    assert queued_event.message_key == "experience.live_messages.queued"


@pytest.mark.asyncio
async def test_delete_review_run_rejects_queued_run(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=6)
    await _create_pm_decision(async_db_session, user, session)
    queued = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=session.session_id,
        review_horizon="5d",
    )

    with pytest.raises(ValueError):
        await experience_service.delete_review_run(
            user_id=user.id,
            review_run_id=queued["review_run_id"],
        )


@pytest.mark.asyncio
async def test_enqueue_review_rejects_active_session(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=6)
    await _create_pm_decision(async_db_session, user, session)
    first = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=session.session_id,
        review_horizon="5d",
    )

    with pytest.raises(ExperienceReviewConflict) as exc_info:
        await experience_service.enqueue_review(
            user_id=user.id,
            session_id=session.session_id,
            review_horizon="5d",
        )

    assert exc_info.value.review_run_id == first["review_run_id"]


@pytest.mark.asyncio
async def test_enqueue_review_queues_same_stock_in_another_session(async_db_session):
    user, first_session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=6)
    await _create_pm_decision(async_db_session, user, first_session)

    second_session = DebateSession(
        user_id=user.id,
        stock_code=first_session.stock_code,
        trading_frequency="swing",
        trading_strategy="trend",
        status="completed",
    )
    async_db_session.add(second_session)
    await async_db_session.commit()
    await async_db_session.refresh(second_session)
    await _create_pm_decision(async_db_session, user, second_session)

    first = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=first_session.session_id,
        review_horizon="5d",
    )
    second = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=second_session.session_id,
        review_horizon="5d",
    )

    assert first["status"] == "queued"
    assert second["status"] == "queued"
    assert first["review_run_id"] != second["review_run_id"]
    run_count = await async_db_session.scalar(
        select(func.count()).select_from(ExperienceReviewRun)
    )
    assert run_count == 2


@pytest.mark.asyncio
async def test_claim_and_execute_queued_review(async_db_session, monkeypatch):
    from app.ai.experience.queue import claim_next_queued_review_run

    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=6)
    await _create_pm_decision(async_db_session, user, session)
    queued = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=session.session_id,
        review_horizon="5d",
    )

    class FakeWorkflow:
        async def ainvoke(self, state):
            return {
                "errors": [],
                "full_context": {},
                "analysis_payload": {
                    "original_pm_decision": "buy",
                    "debate_correctness": "correct",
                    "correctness_reasoning": "ok",
                    "written_memories": [],
                },
                "tool_trace": [],
            }

    monkeypatch.setattr(
        experience_service_module,
        "create_experience_workflow",
        lambda: FakeWorkflow(),
    )

    claimed_run_id = await claim_next_queued_review_run()
    assert claimed_run_id == queued["review_run_id"]
    assert await claim_next_queued_review_run() is None

    result = await experience_service.execute_queued_review(claimed_run_id)

    assert result["review_run_id"] == queued["review_run_id"]
    run = (
        await async_db_session.execute(
            select(ExperienceReviewRun).where(
                ExperienceReviewRun.review_run_id == queued["review_run_id"]
            )
        )
    ).scalar_one()
    assert run.status == "completed"
    completed_event = (
        await async_db_session.execute(
            select(ExperienceReviewEvent).where(
                ExperienceReviewEvent.review_run_id == queued["review_run_id"],
                ExperienceReviewEvent.status == "completed",
            )
        )
    ).scalar_one()
    assert completed_event.payload["review_horizon"] == "5d"


@pytest.mark.asyncio
async def test_claim_next_skips_queued_same_stock_while_running(async_db_session):
    from app.ai.experience.queue import claim_next_queued_review_run

    user, first_session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=6)
    await _create_pm_decision(async_db_session, user, first_session)

    second_session = DebateSession(
        user_id=user.id,
        stock_code=first_session.stock_code,
        trading_frequency="swing",
        trading_strategy="trend",
        status="completed",
    )
    async_db_session.add(second_session)
    await async_db_session.commit()
    await async_db_session.refresh(second_session)
    await _create_pm_decision(async_db_session, user, second_session)

    first = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=first_session.session_id,
        review_horizon="5d",
    )
    second = await experience_service.enqueue_review(
        user_id=user.id,
        session_id=second_session.session_id,
        review_horizon="5d",
    )

    claimed = await claim_next_queued_review_run()
    assert claimed in {first["review_run_id"], second["review_run_id"]}
    assert await claim_next_queued_review_run() is None

    await async_db_session.execute(
        update(ExperienceReviewRun)
        .where(ExperienceReviewRun.review_run_id == claimed)
        .values(status="completed")
    )
    await async_db_session.commit()

    next_claimed = await claim_next_queued_review_run()
    assert next_claimed == (
        second["review_run_id"]
        if claimed == first["review_run_id"]
        else first["review_run_id"]
    )


@pytest.mark.asyncio
async def test_analyze_rejects_unavailable_review_horizon(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=6)
    await _create_pm_decision(async_db_session, user, session)

    with pytest.raises(ValueError, match="requires 21 market days"):
        await experience_service.analyze(
            user_id=user.id,
            session_id=session.session_id,
            review_horizon="20d",
        )


@pytest.mark.asyncio
async def test_analyze_defaults_to_highest_available_review_horizon(async_db_session, monkeypatch):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=21)
    await _create_pm_decision(async_db_session, user, session)

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
        user_id=user.id,
        session_id=session.session_id,
    )

    assert result["review_horizon"] == "20d"
    assert result["market_day_count"] == 21
    completed = (
        await async_db_session.execute(
            select(ExperienceReviewEvent).where(
                ExperienceReviewEvent.review_run_id == result["review_run_id"],
                ExperienceReviewEvent.status == "completed",
            )
        )
    ).scalar_one()
    assert completed.payload["review_horizon"] == "20d"
    assert completed.payload["market_day_count"] == 21


@pytest.mark.asyncio
async def test_analyze_persists_completed_event_before_push(async_db_session, monkeypatch):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=21)
    await _create_pm_decision(async_db_session, user, session)
    calls = []
    original_persist = experience_service._persist_review_event

    class FakeWorkflow:
        async def ainvoke(self, state):
            return {
                "errors": [],
                "full_context": {},
                "analysis_payload": {
                    "recommended_action": "hold",
                    "confidence_score": 80,
                    "debate_correctness": "correct",
                },
                "tool_trace": [],
            }

    async def track_persist(db, **kwargs):
        await original_persist(db, **kwargs)
        calls.append(("persist", kwargs["status"]))

    async def track_push(**kwargs):
        calls.append(("push", kwargs["status"]))

    monkeypatch.setattr(experience_service_module, "create_experience_workflow", lambda: FakeWorkflow())
    monkeypatch.setattr(experience_service, "_persist_review_event", track_persist)
    monkeypatch.setattr(experience_service, "_push_review_update", track_push)

    await experience_service.analyze(user_id=user.id, session_id=session.session_id)

    assert calls.index(("persist", "completed")) < calls.index(("push", "completed"))


@pytest.mark.asyncio
async def test_analyze_defaults_to_20d_before_60d_when_both_are_available(async_db_session, monkeypatch):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_and_klines(async_db_session, count=61)
    await _create_pm_decision(async_db_session, user, session)

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
        user_id=user.id,
        session_id=session.session_id,
    )

    assert result["review_horizon"] == "20d"
