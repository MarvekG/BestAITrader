from importlib import reload
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.core.database as database_module
from app.api.endpoints import llm
import app.crud.llm_usage_log as usage_module
from app.crud.llm_usage_log import llm_usage_log
from app.models.llm_usage_log import LLMUsageLog


@pytest_asyncio.fixture
async def llm_usage_db():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(LLMUsageLog.__table__.create)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    original_async_session_local = database_module.AsyncSessionLocal
    database_module.AsyncSessionLocal = session_factory
    try:
        yield session_factory
    finally:
        database_module.AsyncSessionLocal = original_async_session_local
        await engine.dispose()


async def _single_llm_usage_row(session_factory):
    async with session_factory() as db:
        return (await db.execute(select(LLMUsageLog))).scalar_one()


@pytest.mark.asyncio
async def test_llm_usage_stats_include_cached_and_reasoning_tokens(llm_usage_db):
    await llm_usage_log.create(
        model="deepseek-test",
        role="generic",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cached_tokens=60,
        reasoning_tokens=5,
    )
    await llm_usage_log.create(
        model="deepseek-test",
        role="generic",
        input_tokens=50,
        output_tokens=10,
        total_tokens=60,
        cached_tokens=15,
        reasoning_tokens=3,
    )

    stats = await llm_usage_log.get_stats()

    assert stats["total_calls"] == 2
    assert stats["input_tokens"] == 150
    assert stats["output_tokens"] == 30
    assert stats["total_tokens"] == 180
    assert stats["cached_tokens"] == 75
    assert stats["reasoning_tokens"] == 8
    assert stats["cache_hit_rate"] == 0.5
    assert stats["by_role"] == {"generic": 2}


@pytest.mark.asyncio
async def test_llm_usage_stats_include_cache_miss_and_observability_breakdowns(llm_usage_db):
    await llm_usage_log.create(
        model="provider-test",
        role="fundamental",
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        cached_tokens=60,
        cache_miss_tokens=40,
        reasoning_tokens=5,
        workflow="debate_analysis",
        stage="layer1",
        call_kind="agent",
        iteration_index=1,
        cache_lane="research",
        api_key_alias="research_llm_api_key",
    )
    await llm_usage_log.create(
        model="provider-test",
        role="fundamental_tool_summary",
        input_tokens=50,
        output_tokens=10,
        total_tokens=60,
        cached_tokens=10,
        cache_miss_tokens=40,
        reasoning_tokens=0,
        workflow="debate_analysis",
        stage="tool_summary",
        call_kind="tool_summary",
        iteration_index=1,
        cache_lane="shared",
        api_key_alias="shared_llm_api_key",
    )

    stats = await llm_usage_log.get_stats()

    assert stats["cache_miss_tokens"] == 80
    assert stats["by_workflow"]["debate_analysis"] == {
        "calls": 2,
        "input_tokens": 150,
        "output_tokens": 30,
        "total_tokens": 180,
        "cached_tokens": 70,
        "cache_miss_tokens": 80,
        "reasoning_tokens": 5,
        "cache_hit_rate": 70 / 150,
    }
    assert stats["by_stage"]["layer1"]["calls"] == 1
    assert stats["by_stage"]["tool_summary"]["cache_miss_tokens"] == 40
    assert stats["by_workflow_stage"]["debate_analysis/layer1"]["calls"] == 1
    assert stats["by_workflow_stage"]["debate_analysis/tool_summary"]["cache_miss_tokens"] == 40
    assert stats["by_workflow_call_kind"]["debate_analysis/agent"]["cached_tokens"] == 60
    assert stats["by_workflow_call_kind"]["debate_analysis/tool_summary"]["cache_hit_rate"] == 0.2
    assert stats["by_call_kind"]["agent"]["cached_tokens"] == 60
    assert stats["by_cache_lane"]["research"]["cache_hit_rate"] == 0.6
    assert stats["by_api_key_alias"]["shared_llm_api_key"]["input_tokens"] == 50
    assert stats["by_role_detail"]["fundamental"]["iteration_indexes"] == [1]


@pytest.mark.asyncio
async def test_record_llm_usage_extracts_nested_usage_detail_tokens(llm_usage_db):
    session_factory = llm_usage_db
    real_usage_module = reload(usage_module)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=25,
            total_tokens=125,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
            completion_tokens_details={"reasoning_tokens": 7},
        )
    )

    await real_usage_module.record_llm_usage(response, "deepseek-test", "generic")

    row = await _single_llm_usage_row(session_factory)
    assert row.input_tokens == 100
    assert row.output_tokens == 25
    assert row.total_tokens == 125
    assert row.cached_tokens == 40
    assert row.cache_miss_tokens == 60
    assert row.reasoning_tokens == 7


@pytest.mark.asyncio
async def test_record_llm_usage_extracts_provider_cache_hit_and_miss_tokens(llm_usage_db):
    session_factory = llm_usage_db
    real_usage_module = reload(usage_module)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=25,
            total_tokens=125,
            prompt_cache_hit_tokens=55,
            prompt_cache_miss_tokens=45,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=9),
        )
    )

    await real_usage_module.record_llm_usage(response, "deepseek-test", "generic")

    row = await _single_llm_usage_row(session_factory)
    assert row.input_tokens == 100
    assert row.cached_tokens == 55
    assert row.cache_miss_tokens == 45
    assert row.reasoning_tokens == 9


@pytest.mark.asyncio
async def test_record_llm_usage_extracts_langchain_response_metadata_cache_hits(llm_usage_db):
    session_factory = llm_usage_db
    real_usage_module = reload(usage_module)
    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 25,
            "total_tokens": 125,
        },
        response_metadata={
            "token_usage": {
                "prompt_tokens": 100,
                "completion_tokens": 25,
                "total_tokens": 125,
                "prompt_cache_hit_tokens": 55,
                "prompt_cache_miss_tokens": 45,
                "completion_tokens_details": {"reasoning_tokens": 9},
            }
        },
    )

    await real_usage_module.record_llm_usage(response, "deepseek-test", "generic")

    row = await _single_llm_usage_row(session_factory)
    assert row.input_tokens == 100
    assert row.cached_tokens == 55
    assert row.cache_miss_tokens == 45
    assert row.reasoning_tokens == 9


@pytest.mark.asyncio
async def test_record_llm_usage_extracts_langchain_cache_read_tokens(llm_usage_db):
    session_factory = llm_usage_db
    real_usage_module = reload(usage_module)
    response = SimpleNamespace(
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 25,
            "total_tokens": 125,
            "input_token_details": {"cache_read": 35},
        }
    )

    await real_usage_module.record_llm_usage(response, "deepseek-test", "generic")

    row = await _single_llm_usage_row(session_factory)
    assert row.cached_tokens == 35
    assert row.cache_miss_tokens == 65


@pytest.mark.asyncio
async def test_record_llm_usage_persists_observability_metadata(llm_usage_db):
    session_factory = llm_usage_db
    real_usage_module = reload(usage_module)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=25,
            total_tokens=125,
            prompt_cache_hit_tokens=55,
            prompt_cache_miss_tokens=45,
        )
    )

    await real_usage_module.record_llm_usage(
        response,
        "provider-test",
        "fundamental",
        workflow="debate_analysis",
        stage="layer1",
        call_kind="agent",
        iteration_index=2,
        cache_lane="research",
        api_key_alias="research_llm_api_key",
    )

    row = await _single_llm_usage_row(session_factory)
    assert row.workflow == "debate_analysis"
    assert row.stage == "layer1"
    assert row.call_kind == "agent"
    assert row.iteration_index == 2
    assert row.cache_lane == "research"
    assert row.api_key_alias == "research_llm_api_key"


def test_request_llm_completion_records_market_watch_workflow():
    usage_metadata = llm._llm_usage_observability_for_role("market_watch")

    assert usage_metadata == {
        "workflow": "market_watch",
        "stage": "watch_ai_gate",
        "call_kind": "agent",
        "cache_lane": "market_watch",
        "api_key_alias": "market_watch_llm_api_key",
    }


@pytest.mark.asyncio
async def test_get_llm_usage_stats_merges_backend_and_memory_usage(monkeypatch):
    backend_stats = {
        "total_calls": 10,
        "input_tokens": 500,
        "output_tokens": 100,
        "total_tokens": 500,
        "cached_tokens": 150,
        "cache_miss_tokens": 350,
        "reasoning_tokens": 20,
        "cache_hit_rate": 0.3,
        "by_role": {
            "Agentic Decision": 6,
            "generic": 4,
        },
        "by_workflow_call_kind": {
            "debate_analysis/agent": {
                "calls": 6,
                "input_tokens": 400,
                "cached_tokens": 200,
                "cache_hit_rate": 0.5,
            },
        },
    }
    memory_stats = {
        "status": "ok",
        "total_calls": 3,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "cached_tokens": 30,
        "cache_miss_tokens": 70,
        "reasoning_tokens": 10,
        "cache_hit_rate": 0.3,
        "by_operation": {
            "memory_summary": {"calls": 2, "total_tokens": 80, "cached_tokens": 20},
            "memory_query_optimizer": {"calls": 1, "total_tokens": 40, "cached_tokens": 10},
        },
    }

    monkeypatch.setattr(llm, "llm_usage_log", MagicMock(get_stats=AsyncMock(return_value=backend_stats)))
    monkeypatch.setattr(llm.memory_client, "get_usage_stats", AsyncMock(return_value=memory_stats))
    monkeypatch.setattr(llm.memory_client, "get_last_error", MagicMock(return_value=None))

    result = await llm.get_llm_usage_stats()

    assert result["total_calls"] == 13
    assert result["input_tokens"] == 600
    assert result["output_tokens"] == 120
    assert result["total_tokens"] == 620
    assert result["cached_tokens"] == 180
    assert result["cache_miss_tokens"] == 420
    assert result["reasoning_tokens"] == 30
    assert result["cache_hit_rate"] == 0.3
    assert result["by_role"]["Agentic Decision"] == 6
    assert result["by_role"]["memory_summary"] == 2
    assert result["by_role"]["memory_query_optimizer"] == 1
    assert result["backend"] == backend_stats
    assert result["by_workflow_call_kind"] == backend_stats["by_workflow_call_kind"]
    assert result["memory"] == memory_stats
    assert result["combined"]["total_calls"] == 13
    assert result["combined"]["input_tokens"] == 600
    assert result["combined"]["output_tokens"] == 120
    assert result["combined"]["total_tokens"] == 620
    assert result["combined"]["cached_tokens"] == 180
    assert result["combined"]["cache_miss_tokens"] == 420
    assert result["combined"]["reasoning_tokens"] == 30
    assert result["combined"]["cache_hit_rate"] == 0.3
    assert result["combined"]["by_workflow_call_kind"] == backend_stats["by_workflow_call_kind"]


@pytest.mark.asyncio
async def test_get_llm_usage_stats_surfaces_memory_fetch_error_without_breaking(monkeypatch):
    backend_stats = {
        "total_calls": 4,
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 80,
        "cached_tokens": 25,
        "reasoning_tokens": 5,
        "cache_hit_rate": 0.25,
        "by_role": {"generic": 4},
    }
    memory_error = {
        "operation": "usage_stats",
        "path": "/v1/usage/stats",
        "message": "503 Server Error",
        "error_type": "HTTPStatusError",
    }

    monkeypatch.setattr(llm, "llm_usage_log", MagicMock(get_stats=AsyncMock(return_value=backend_stats)))
    monkeypatch.setattr(llm.memory_client, "get_usage_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(llm.memory_client, "get_last_error", MagicMock(return_value=memory_error))

    result = await llm.get_llm_usage_stats()

    assert result["total_calls"] == 4
    assert result["input_tokens"] == 100
    assert result["total_tokens"] == 80
    assert result["cached_tokens"] == 25
    assert result["cache_hit_rate"] == 0.25
    assert result["memory"]["status"] == "error"
    assert result["memory"]["error"] == memory_error
    assert result["combined"]["by_role"] == {"generic": 4}


@pytest.mark.asyncio
async def test_clear_llm_usage_stats_clears_backend_and_memory(monkeypatch):
    backend_usage = MagicMock(clear=AsyncMock(return_value=5))
    memory_result = {"status": "ok", "deleted": 3}
    monkeypatch.setattr(llm, "llm_usage_log", backend_usage)
    monkeypatch.setattr(llm.memory_client, "clear_usage_stats", AsyncMock(return_value=memory_result))
    monkeypatch.setattr(llm.memory_client, "get_last_error", MagicMock(return_value=None))

    result = await llm.clear_llm_usage_stats()

    assert result == {
        "status": "ok",
        "backend": {"deleted": 5},
        "memory": memory_result,
        "total_deleted": 8,
    }


@pytest.mark.asyncio
async def test_clear_llm_usage_stats_returns_partial_when_memory_clear_fails(monkeypatch):
    memory_error = {
        "operation": "clear_usage_stats",
        "path": "/v1/usage/stats",
        "message": "503 Server Error",
        "error_type": "HTTPStatusError",
    }
    monkeypatch.setattr(llm, "llm_usage_log", MagicMock(clear=AsyncMock(return_value=5)))
    monkeypatch.setattr(llm.memory_client, "clear_usage_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(llm.memory_client, "get_last_error", MagicMock(return_value=memory_error))

    result = await llm.clear_llm_usage_stats()

    assert result == {
        "status": "partial",
        "backend": {"deleted": 5},
        "memory": {"status": "error", "error": memory_error},
        "total_deleted": 5,
    }
