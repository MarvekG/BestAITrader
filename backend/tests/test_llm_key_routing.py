import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.ai.agentic.tool_output_summarizer import summarize_tool_output
from app.ai.experience import workflow
from app.ai.llm_engine.agents.base import BaseAgent


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
        return str


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
