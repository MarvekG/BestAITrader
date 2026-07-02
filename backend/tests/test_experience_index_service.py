from datetime import datetime
import uuid

import pytest
from sqlalchemy import select

from app.ai.experience import service as experience_service_module
from app.ai.experience.index_service import experience_index_service
from app.ai.experience.service import experience_service
from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER
from app.models.data_storage import KlineData, StockBasic
from app.models.debate_message import DebateMessage
from app.models.experience_index import ExperienceIndex
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.session import Session as DebateSession
from app.models.user import User


async def _create_user_and_session(db):
    user = User(
        username=f"experience_index_{uuid.uuid4().hex[:8]}",
        email=f"experience_index_{uuid.uuid4().hex[:8]}@example.com",
        password_hash="hashed",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    session = DebateSession(
        user_id=user.id,
        stock_code="000001.SZ",
        trading_frequency="swing",
        trading_strategy="trend",
        status="completed",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return user, session


def _review_result(session, *, review_run_id="review-1"):
    return {
        "review_run_id": review_run_id,
        "review_horizon": "20d",
        "market_day_count": 21,
        "session_id": session.session_id,
        "stock_code": session.stock_code,
        "stock_name": "平安银行",
        "industry": "银行",
        "trading_strategy": session.trading_strategy,
        "analysis_payload": {
            "debate_correctness": "partially_correct",
            "confidence_score": 80,
            "experience_tags": {
                "stock_tags": ["000001.SZ"],
                "industry_tags": ["银行"],
                "strategy_tags": ["trend"],
                "failure_lesson_tags": ["追高"],
                "position_discipline_tags": ["降低仓位"],
                "signal_tags": ["行业相对强势"],
                "market_regime_tags": ["震荡市"],
            },
            "review_triads": {
                "original_judgment": {
                    "verdict": "partially_correct",
                    "score": 70,
                    "pm_decision": "buy",
                    "outcome_basis": "20D 收益为正但回撤较大。",
                    "reasoning": "方向部分正确。",
                },
                "signal_validation": {
                    "validated_signals": [],
                    "invalidated_signals": [],
                    "noise_signals": [],
                },
                "decision_process_improvement": {
                    "debate_changes": ["补充行业比较。"],
                    "pm_changes": ["降低仓位。"],
                    "risk_control_changes": ["跌破均线重新辩论。"],
                },
            },
            "written_memories": [
                {
                    "content": "行业强势但回撤扩大会降低加仓胜率，应等待成交确认后再提高仓位。",
                    "importance": "high",
                    "memo_session": "stock",
                    "stock_code": session.stock_code,
                    "status": "success",
                    "observation_id": "obs-memory-1",
                    "source_id": "memory-source-1",
                    "evidence_chain": {
                        "market_outcome_summary": {
                            "selected_horizon_outcome": {"absolute_return": 0.05},
                        }
                    },
                },
                {
                    "content": "这条写入失败，不应建立索引。",
                    "importance": "medium",
                    "status": "failed",
                    "error": "memory unavailable",
                    "observation_id": "obs-memory-failed",
                },
            ],
        },
    }


def _serializable_review_result(result):
    payload = dict(result)
    payload["session_id"] = str(payload["session_id"])
    return payload


async def _create_stock_pm_and_klines(db, session):
    stock = StockBasic(
        stock_code=session.stock_code,
        name="平安银行",
        industry="银行",
        market="SZSE",
    )
    db.add(stock)
    await db.commit()
    db.add(
        DebateMessage(
            session_id=session.session_id,
            stage="portfolio_manager",
            round_number=1,
            agent_name="PM",
            agent_role=AGENT_ROLE_PORTFOLIO_MANAGER,
            decision="buy",
            reasoning="pm reasoning",
            created_at=datetime(2026, 1, 1, 15, 0),
        )
    )
    db.add_all(
        [
            KlineData(
                stock_code=session.stock_code,
                date=datetime(2026, 1, 1 + index).date(),
                freq="D",
                open=10 + index,
                close=10.5 + index,
                high=11 + index,
                low=9.5 + index,
            )
            for index in range(21)
        ]
    )
    await db.commit()


@pytest.mark.asyncio
async def test_sync_from_review_result_indexes_successful_memory_writes(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    result = _review_result(session)

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        stats = await experience_index_service.sync_from_review_result(db, user_id=user.id, result=result)

    rows = (await async_db_session.execute(select(ExperienceIndex))).scalars().all()
    assert stats == {"created": 1, "updated": 0, "skipped": 1}
    assert len(rows) == 1
    assert rows[0].memory_observation_id == "obs-memory-1"
    assert rows[0].memory_source_id == "memory-source-1"
    assert rows[0].review_run_id == "review-1"
    assert rows[0].session_id == session.session_id
    assert rows[0].stock_code == "000001.SZ"
    assert rows[0].stock_name == "平安银行"
    assert rows[0].industry == "银行"
    assert rows[0].strategy == "trend"
    assert rows[0].review_horizon == "20d"
    assert rows[0].correctness == "partially_correct"
    assert rows[0].importance == "high"
    assert rows[0].outcome_label == "profit"
    assert "行业强势" in rows[0].summary
    assert rows[0].tags["failure_lesson_tags"] == ["追高"]


@pytest.mark.asyncio
async def test_sync_from_review_result_indexes_accepted_memory_writes(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    result = _review_result(session)
    result["analysis_payload"]["written_memories"][0]["status"] = "accepted"

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        stats = await experience_index_service.sync_from_review_result(db, user_id=user.id, result=result)

    rows = (await async_db_session.execute(select(ExperienceIndex))).scalars().all()
    assert stats == {"created": 1, "updated": 0, "skipped": 1}
    assert len(rows) == 1
    assert rows[0].memory_observation_id == "obs-memory-1"


@pytest.mark.asyncio
async def test_sync_from_review_result_is_idempotent_by_memory_observation_id(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    result = _review_result(session)

    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        first = await experience_index_service.sync_from_review_result(db, user_id=user.id, result=result)
        second = await experience_index_service.sync_from_review_result(db, user_id=user.id, result=result)

    assert first == {"created": 1, "updated": 0, "skipped": 1}
    assert second == {"created": 0, "updated": 1, "skipped": 1}
    rows = (await async_db_session.execute(select(ExperienceIndex))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_list_items_filters_by_horizon_tag_keyword_and_stock(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        await experience_index_service.sync_from_review_result(db, user_id=user.id, result=_review_result(session))

    matched = await experience_index_service.list_items(
        user_id=user.id,
        review_horizon="20d",
        tag="追高",
        keyword="成交确认",
        stock_code="000001.SZ",
    )
    missed = await experience_index_service.list_items(
        user_id=user.id,
        review_horizon="60d",
    )

    assert matched["total"] == 1
    assert matched["items"][0]["memory_observation_id"] == "obs-memory-1"
    assert missed["total"] == 0


@pytest.mark.asyncio
async def test_list_items_uses_fuzzy_matching_for_each_filter_field(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        await experience_index_service.sync_from_review_result(db, user_id=user.id, result=_review_result(session))

    filter_cases = [
        {"stock_code": "000001"},
        {"industry": "银"},
        {"strategy": "tre"},
        {"review_horizon": "20"},
        {"correctness": "partial"},
        {"importance": "hi"},
        {"tag": "追"},
    ]

    for filters in filter_cases:
        result = await experience_index_service.list_items(user_id=user.id, **filters)
        assert result["total"] == 1, filters
        assert result["items"][0]["memory_observation_id"] == "obs-memory-1"


@pytest.mark.asyncio
async def test_list_items_keyword_searches_multiple_index_fields_and_tags(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        await experience_index_service.sync_from_review_result(db, user_id=user.id, result=_review_result(session))

    keyword_cases = ["平安", "银行", "trend", "20d", "profit", "partial", "high", "追"]

    for keyword in keyword_cases:
        result = await experience_index_service.list_items(user_id=user.id, keyword=keyword)
        assert result["total"] == 1, keyword
        assert result["items"][0]["memory_observation_id"] == "obs-memory-1"


@pytest.mark.asyncio
async def test_list_items_fuzzy_search_returns_empty_when_no_field_matches(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        await experience_index_service.sync_from_review_result(db, user_id=user.id, result=_review_result(session))

    result = await experience_index_service.list_items(
        user_id=user.id,
        stock_code="999999",
        keyword="不存在的经验",
    )

    assert result["total"] == 0
    assert result["items"] == []


@pytest.mark.asyncio
async def test_get_detail_returns_review_payload_context(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    result = _review_result(session)
    completed = ExperienceReviewEvent(
        review_run_id="review-1",
        session_id=session.session_id,
        user_id=user.id,
        stage="experience_review",
        status="completed",
        message_key="experience.live_messages.completed",
        payload={"result": _serializable_review_result(result)},
        created_at=datetime(2026, 1, 2, 15, 0),
    )
    async_db_session.add(completed)
    await async_db_session.commit()
    async with experience_service_module.database_module.AsyncSessionLocal() as db:
        await experience_index_service.sync_from_review_result(db, user_id=user.id, result=result)
    row = (await async_db_session.execute(select(ExperienceIndex))).scalar_one()

    detail = await experience_index_service.get_detail(user_id=user.id, index_id=row.id)

    assert detail is not None
    assert detail["id"] == str(row.id)
    assert detail["review_triads"]["original_judgment"]["verdict"] == "partially_correct"
    assert detail["market_outcome_summary"] == {
        "selected_horizon_outcome": {"absolute_return": 0.05},
    }


@pytest.mark.asyncio
async def test_rebuild_for_user_indexes_completed_review_events(async_db_session):
    user, session = await _create_user_and_session(async_db_session)
    result = _review_result(session, review_run_id="review-rebuild")
    async_db_session.add(
        ExperienceReviewEvent(
            review_run_id="review-rebuild",
            session_id=session.session_id,
            user_id=user.id,
            stage="experience_review",
            status="completed",
            message_key="experience.live_messages.completed",
            payload={"result": _serializable_review_result(result)},
        )
    )
    await async_db_session.commit()

    stats = await experience_index_service.rebuild_for_user(user_id=user.id)

    assert stats["created"] == 1
    assert stats["updated"] == 0
    assert stats["skipped"] == 1
    assert stats["failed"] == 0
    rows = (await async_db_session.execute(select(ExperienceIndex))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_analyze_syncs_successful_memory_writes_to_experience_index(async_db_session, monkeypatch):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_pm_and_klines(async_db_session, session)

    class FakeWorkflow:
        async def ainvoke(self, state):
            result = _review_result(session, review_run_id=state["review_run_id"])
            return {
                "errors": [],
                "full_context": {},
                "analysis_payload": result["analysis_payload"],
                "tool_trace": [],
            }

    monkeypatch.setattr(experience_service_module, "create_experience_workflow", lambda: FakeWorkflow())

    result = await experience_service.analyze(
        user_id=user.id,
        session_id=session.session_id,
        review_horizon="20d",
    )

    rows = (await async_db_session.execute(select(ExperienceIndex))).scalars().all()
    assert result["review_horizon"] == "20d"
    assert len(rows) == 1
    assert rows[0].review_run_id == result["review_run_id"]
    assert rows[0].memory_observation_id == "obs-memory-1"


@pytest.mark.asyncio
async def test_analyze_keeps_completed_result_when_experience_index_sync_fails(async_db_session, monkeypatch, caplog):
    user, session = await _create_user_and_session(async_db_session)
    await _create_stock_pm_and_klines(async_db_session, session)

    class FakeWorkflow:
        async def ainvoke(self, state):
            result = _review_result(session, review_run_id=state["review_run_id"])
            return {
                "errors": [],
                "full_context": {},
                "analysis_payload": result["analysis_payload"],
                "tool_trace": [],
            }

    async def fail_sync(*args, **kwargs):
        raise RuntimeError("index offline")

    monkeypatch.setattr(experience_service_module, "create_experience_workflow", lambda: FakeWorkflow())
    monkeypatch.setattr(experience_service_module.experience_index_service, "sync_from_review_result", fail_sync)

    result = await experience_service.analyze(
        user_id=user.id,
        session_id=session.session_id,
        review_horizon="20d",
    )

    completed_events = (await async_db_session.execute(
        select(ExperienceReviewEvent).where(
            ExperienceReviewEvent.review_run_id == result["review_run_id"],
            ExperienceReviewEvent.status == "completed",
        )
    )).scalars().all()
    failed_events = (await async_db_session.execute(
        select(ExperienceReviewEvent).where(
            ExperienceReviewEvent.review_run_id == result["review_run_id"],
            ExperienceReviewEvent.status == "failed",
        )
    )).scalars().all()
    assert result["review_horizon"] == "20d"
    assert len(completed_events) == 1
    assert failed_events == []
    assert "experience index sync failed" in caplog.text


def test_experience_library_api_lists_details_and_rebuilds(client, auth_headers, test_db, run_async):
    async def _seed_review_event():
        async with test_db() as db:
            user = (await db.execute(select(User))).scalar_one()
            session = DebateSession(
                user_id=user.id,
                stock_code="000001.SZ",
                trading_frequency="swing",
                trading_strategy="trend",
                status="completed",
            )
            db.add(session)
            await db.commit()
            await db.refresh(session)
            result = _review_result(session, review_run_id="api-review")
            db.add(
                ExperienceReviewEvent(
                    review_run_id="api-review",
                    session_id=session.session_id,
                    user_id=user.id,
                    stage="experience_review",
                    status="completed",
                    message_key="experience.live_messages.completed",
                    payload={"result": _serializable_review_result(result)},
                )
            )
            await db.commit()

    run_async(_seed_review_event())

    rebuild_response = client.post("/api/v1/experience/library/rebuild", headers=auth_headers)
    list_response = client.get(
        "/api/v1/experience/library?review_horizon=20d&tag=追高&keyword=成交确认",
        headers=auth_headers,
    )
    item = list_response.json()["items"][0]
    detail_response = client.get(f"/api/v1/experience/library/{item['id']}", headers=auth_headers)

    assert rebuild_response.status_code == 200
    assert rebuild_response.json()["created"] == 1
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 1
    assert detail_response.status_code == 200
    assert detail_response.json()["review_triads"]["original_judgment"]["verdict"] == "partially_correct"
