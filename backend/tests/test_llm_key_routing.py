import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.ai.agentic.tool_output_summarizer import summarize_tool_output
from app.ai.experience import workflow
from app.ai.llm_engine.agents.base import BaseAgent
from app.ai.llm_engine.models import AnalystOutput
from app.ai.stock_picker.service import RankedCandidate, StockPickerService


class FakeLLM:
    def __init__(self, responses: list[Any] | None = None) -> None:
        self.bound_tools = []
        self.model_name = "fake-model"
        self.responses = list(responses or [])
        self.ainvoke = AsyncMock(side_effect=self._ainvoke)

    async def _ainvoke(self, _messages):
        if self.responses:
            return self.responses.pop(0)
        return type("Response", (), {"content": "summary", "usage_metadata": {}, "tool_calls": []})()

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self


class FakeProvider:
    def __init__(self, llm: FakeLLM | None = None) -> None:
        self.calls = []
        self.llm = llm or FakeLLM()

    def build_chat_model(self, **kwargs):
        self.calls.append(kwargs)
        return self.llm

    def sanitize_tool_call_response_for_replay(self, response):
        return response, []

    def build_invalid_tool_call_retry_message(self, _invalid_tool_calls):
        return "invalid tool call"


class DummyAgent(BaseAgent):
    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return "dummy prompt"

    def get_output_model(self):
        return AnalystOutput


def _valid_stock_research_payload() -> str:
    return json.dumps(
        {
            "research": [
                {
                    "stock_code": "688021.SH",
                    "ai_score": 86,
                    "thesis": "研究结论",
                    "catalysts": ["催化"],
                    "risks": ["风险"],
                    "style_fit_explanation": "匹配平衡风格",
                    "holding_horizon": "mid_term",
                    "decision": "keep",
                }
            ]
        },
        ensure_ascii=False,
    )


def test_base_agent_uses_litellm_backend_model(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr("app.ai.llm_engine.agents.base.settings.LLM_MODEL", "backend")
    monkeypatch.setattr("app.ai.llm_engine.agents.base.get_llm_provider", lambda: provider)
    monkeypatch.setattr("app.ai.llm_engine.agents.base.get_all_tools", lambda: [])
    monkeypatch.setattr("app.ai.llm_engine.agents.base.build_memory_tools", lambda state: [])
    monkeypatch.setattr("app.ai.llm_engine.agents.base.get_skills_loader_tools", lambda: [])

    DummyAgent(role_name="bull")

    assert provider.calls[0]["model"] == "backend"
    assert "api_key" not in provider.calls[0] or provider.calls[0]["api_key"] is None


@pytest.mark.asyncio
async def test_experience_review_uses_litellm_backend_model_and_usage_lane(monkeypatch):
    provider = FakeProvider(
        FakeLLM(
            [
                AIMessage(
                    content=json.dumps(
                        {
                            "thesis_summary": "PM 结论部分有效。",
                            "recommended_action": "hold",
                            "confidence_score": 70,
                            "debate_correctness": "partially_correct",
                            "correctness_score": 65,
                            "review_triads": {
                                "original_judgment": {
                                    "verdict": "partially_correct",
                                    "score": 65,
                                    "pm_decision": "buy",
                                    "outcome_basis": "20D 收益为正。",
                                    "reasoning": "方向判断部分正确。",
                                },
                                "signal_validation": {
                                    "validated_signals": [],
                                    "invalidated_signals": [],
                                    "noise_signals": [],
                                },
                                "decision_process_improvement": {
                                    "debate_changes": [],
                                    "pm_changes": [],
                                    "risk_control_changes": [],
                                },
                            },
                        },
                        ensure_ascii=False,
                    )
                )
            ]
        )
    )
    usage_calls = []
    monkeypatch.setattr(workflow.settings, "LLM_MODEL", "backend")
    monkeypatch.setattr(workflow, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(workflow, "get_all_tools", lambda: [])
    monkeypatch.setattr(workflow, "get_skills_loader_tools", lambda: [])
    monkeypatch.setattr(workflow, "build_memory_tools", lambda state: [])
    monkeypatch.setattr(workflow, "record_llm_usage", lambda *args, **kwargs: usage_calls.append(kwargs))

    result = await workflow.review_debate_conclusion(
        {
            "stock_code": "601888.SH",
            "stock_name": "中国中免",
            "style_bucket": "swing",
            "full_context": {"pm_decision": {"decision": "buy"}},
        }
    )

    assert result["errors"] == []
    assert provider.calls[0]["model"] == "backend"
    assert "api_key" not in provider.calls[0] or provider.calls[0]["api_key"] is None
    assert usage_calls[0]["cache_lane"] == "research"
    assert usage_calls[0]["api_key_alias"] == "research_llm_api_key"


@pytest.mark.asyncio
async def test_stock_picker_research_uses_litellm_backend_model_and_usage_lane(monkeypatch):
    import app.ai.stock_picker.service as stock_picker_service_module

    provider = FakeProvider(
        FakeLLM(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "tool-1",
                            "name": "query_stock_data",
                            "args": {"stock_code": "688021.SH"},
                        }
                    ],
                ),
                AIMessage(content=_valid_stock_research_payload()),
            ]
        )
    )
    usage_calls = []

    class FakeTool:
        name = "query_stock_data"

        async def ainvoke(self, args):
            return {"stock_code": args.get("stock_code"), "results": {"basic": {}}}

    ranked = [
        RankedCandidate(
            stock_code="688021.SH",
            stock_name="测试一号",
            industry="半导体",
            market="科创板",
            factor_score=61.0,
            ai_score=0.0,
            final_score=61.0,
            decision="watch",
            research_payload={"quant_summary": {}, "quant_support": {}},
        )
    ]
    monkeypatch.setattr(stock_picker_service_module.settings, "LLM_MODEL", "backend")
    monkeypatch.setattr(stock_picker_service_module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(stock_picker_service_module, "get_all_tools", lambda: [FakeTool()])
    monkeypatch.setattr(stock_picker_service_module, "get_skills_loader_tools", lambda: [])
    monkeypatch.setattr(stock_picker_service_module, "record_llm_usage", lambda *args, **kwargs: usage_calls.append(kwargs))

    payload = await StockPickerService()._request_llm_research(ranked, "balanced", 1)

    assert payload is not None
    assert provider.calls[0]["model"] == "backend"
    assert "api_key" not in provider.calls[0] or provider.calls[0]["api_key"] is None
    assert usage_calls[0]["cache_lane"] == "research"
    assert usage_calls[0]["api_key_alias"] == "research_llm_api_key"


@pytest.mark.asyncio
async def test_tool_output_summary_uses_default_shared_llm_without_dedicated_news_key(monkeypatch):
    provider = FakeProvider()
    source_llm = FakeLLM()
    usage_calls = []
    monkeypatch.setattr("app.ai.agentic.tool_output_summarizer.get_llm_provider", lambda: provider)
    monkeypatch.setattr(
        "app.ai.agentic.tool_output_summarizer.record_llm_usage",
        lambda *args, **kwargs: usage_calls.append(kwargs),
    )

    result = await summarize_tool_output(
        source_llm,
        role_name="bull",
        tool_name="search_news",
        content="x" * 12001,
        tool_args={"keyword": "贵州茅台"},
    )

    assert provider.calls[0]["api_key"] is None
    assert provider.llm.ainvoke.await_count == 1
    assert source_llm.ainvoke.await_count == 0
    assert usage_calls[0]["cache_lane"] == "shared"
    assert usage_calls[0]["api_key_alias"] == "shared_llm_api_key"
    assert result == "[Structured Summary of search_news]:\nsummary"
