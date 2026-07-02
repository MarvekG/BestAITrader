from unittest.mock import AsyncMock

import pytest

from app.ai.agentic.tool_output_summarizer import summarize_tool_output


@pytest.mark.asyncio
async def test_summarize_tool_output_records_usage(monkeypatch):
    usage_recorder = AsyncMock()

    class _FakeResponse:
        content = "摘要结果"
        usage_metadata = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}

    class _FakeLLM:
        model_name = "test-summary-model"
        messages = None

        async def ainvoke(self, messages):
            self.messages = messages
            return _FakeResponse()

    class _FakeProvider:
        def __init__(self):
            self.calls = []
            self.llm = _FakeLLM()

        def build_chat_model(self, **kwargs):
            self.calls.append(kwargs)
            return self.llm

    monkeypatch.setattr("app.ai.agentic.tool_output_summarizer.record_llm_usage", usage_recorder)
    provider = _FakeProvider()
    monkeypatch.setattr("app.ai.agentic.tool_output_summarizer.get_llm_provider", lambda: provider)
    source_llm = _FakeLLM()

    result = await summarize_tool_output(
        source_llm,
        role_name="stock_picker_research",
        tool_name="search_news",
        content="N" * 9001,
        tool_args={"z": 1, "a": "贵州茅台 新闻"},
    )

    assert result == "[Structured Summary of search_news]:\n摘要结果"
    assert provider.calls[0]["api_key"] is None
    assert source_llm.messages is None
    assert provider.llm.messages is not None
    system_prompt = provider.llm.messages[0][1]
    user_input = provider.llm.messages[1][1]
    assert "{role_name}" not in system_prompt
    assert "{tool_name}" not in system_prompt
    assert "{tool_args_json}" not in system_prompt
    assert 'tool_args_json: {"a":"贵州茅台 新闻","z":1}' in user_input
    usage_recorder.assert_called_once()
    assert usage_recorder.call_args.args[1] == "test-summary-model"
    assert usage_recorder.call_args.args[2] == "stock_picker_research_tool_summary"
    assert usage_recorder.call_args.kwargs["cache_lane"] == "shared"
    assert usage_recorder.call_args.kwargs["api_key_alias"] == "shared_llm_api_key"
