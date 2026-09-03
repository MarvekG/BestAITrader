from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.experience import service as experience_service_module
from app.ai.experience.service import ExperienceService


def test_normalize_analysis_payload_keeps_only_compact_fields():
    service = ExperienceService()

    result = service._normalize_analysis_payload(
        {
            "original_pm_decision": "buy",
            "debate_correctness": "partially_correct",
            "correctness_reasoning": "行业强势被验证，但追涨缺少成交确认。",
            "review_triads": {"original_judgment": {"score": 70}},
            "experience_tags": {"strategy_tags": ["trend"]},
            "market_experience_summary": "旧格式字段",
            "written_memories": [
                {
                    "content": "交通银行复盘经验：先确认成交确认，再扩大仓位。",
                    "importance": "high",
                    "memo_session": "stock",
                    "stock_code": "601328.SH",
                    "version": 2,
                }
            ],
        },
        debate_review_context={"pm_decision": {"decision": "sell"}},
        tool_trace=[],
    )

    assert result == {
        "original_pm_decision": "buy",
        "debate_correctness": "partially_correct",
        "correctness_reasoning": "行业强势被验证，但追涨缺少成交确认。",
        "written_memories": [
            {
                "status": "success",
                "stock_code": "601328.SH",
                "max_chars": experience_service_module.settings.MEMORY_DOC_MAX_CHARS,
                "size_chars": len("交通银行复盘经验：先确认成交确认，再扩大仓位。"),
                "version": 2,
            }
        ],
    }


def test_normalize_analysis_payload_falls_back_to_pm_decision_and_trace():
    service = ExperienceService()
    trace = [
        {
            "name": "write_memory",
            "args": {
                "stock_code": "601328.SH",
                "content": "交通银行复盘经验",
            },
            "result": {
                "status": "failed",
                "error": "version conflict",
                "version": 3,
                "max_chars": 8000,
            },
        }
    ]

    result = service._normalize_analysis_payload(
        {"debate_correctness": "not-a-bucket", "written_memories": []},
        debate_review_context={"pm_decision": {"decision": "hold"}},
        tool_trace=trace,
    )

    assert result["original_pm_decision"] == "hold"
    assert result["debate_correctness"] == "inconclusive"
    assert result["written_memories"] == [
        {
            "status": "failed",
            "stock_code": "601328.SH",
            "max_chars": 8000,
            "size_chars": len("交通银行复盘经验"),
            "error": "version conflict",
            "version": 3,
        }
    ]
    assert "content" not in result["written_memories"][0]


def test_completed_event_payload_keeps_tool_trace_separate_from_analysis():
    service = ExperienceService()
    tool_trace = [{"name": "read_memory", "args": {"stock_code": "601328.SH"}}]
    result = {
        "review_horizon": "20d",
        "market_day_count": 21,
        "tool_trace": tool_trace,
        "analysis_payload": {
            "original_pm_decision": "buy",
            "debate_correctness": "correct",
            "correctness_reasoning": "趋势判断得到验证。",
            "written_memories": [],
        },
    }

    payload = service._build_completed_event_payload(
        result=result,
        recommended_action="buy",
        debate_correctness="correct",
    )

    assert payload["tool_trace"] == tool_trace
    assert payload["original_pm_decision"] == "buy"
    assert payload["correctness_reasoning"] == "趋势判断得到验证。"
    assert set(payload["result"]["analysis_payload"]) == {
        "original_pm_decision",
        "debate_correctness",
        "correctness_reasoning",
        "written_memories",
    }


@pytest.mark.asyncio
async def test_get_review_run_result_normalizes_historical_payload_with_mocked_db(monkeypatch):
    service = ExperienceService()
    review_run_id = "review-run-1"
    completed_event = SimpleNamespace(
        stage="experience_review",
        status="completed",
        payload={
            "recommended_action": "buy",
            "tool_trace": [
                {
                    "name": "write_memory",
                    "args": {
                        "stock_code": "601328.SH",
                        "content": "交通银行复盘经验",
                    },
                    "result": {"status": "success", "version": 2, "size_chars": 8, "max_chars": 8000},
                }
            ],
            "result": {
                "review_run_id": review_run_id,
                "analysis_payload": {
                    "recommended_action": "sell",
                    "market_experience_summary": "旧历史字段",
                    "written_memories": [],
                },
            },
        },
    )
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = [completed_event]
    mocked_db = MagicMock()
    mocked_db.execute = AsyncMock(return_value=execute_result)
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=mocked_db)
    session_context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(experience_service_module.database_module, "AsyncSessionLocal", lambda: session_context)

    normalized = await service.get_review_run_result(user_id=7, review_run_id=review_run_id)

    assert normalized is not None
    assert normalized["analysis_payload"] == {
        "original_pm_decision": "buy",
        "debate_correctness": "inconclusive",
        "correctness_reasoning": "旧历史字段",
        "written_memories": [
            {
                "status": "success",
                "stock_code": "601328.SH",
                "max_chars": 8000,
                "size_chars": 8,
                "version": 2,
            }
        ],
    }
    assert normalized["tool_trace"] == completed_event.payload["tool_trace"]
    mocked_db.execute.assert_awaited_once()
