import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.ai.experience import workflow


class FakeProvider:
    def __init__(self, raw_llm: Any | None = None) -> None:
        """初始化测试用 LLM Provider。

        Args:
            raw_llm: 测试中用于模拟模型调用的对象。
        """
        self.raw_llm = raw_llm

    def build_chat_model(self, **_kwargs: Any) -> Any:
        """返回测试模型对象。

        Returns:
            初始化时传入的测试模型对象。
        """
        return self.raw_llm

    def sanitize_tool_call_response_for_replay(self, response: AIMessage) -> tuple[AIMessage, list[Any]]:
        return response, []

    def build_invalid_tool_call_retry_message(self, _invalid_tool_calls: list[Any]) -> str:
        """构造无效工具调用的重试提示。

        Args:
            _invalid_tool_calls: 无效工具调用列表，测试中不使用具体内容。

        Returns:
            固定的重试提示文本。
        """
        return "invalid tool call"


class FakeRawLlm:
    def __init__(self, responses: list[AIMessage]) -> None:
        self.responses = responses
        self.call_messages: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.call_messages.append(list(messages))
        return self.responses.pop(0)

    def bind_tools(self, _tools: list[Any]) -> "FakeRawLlm":
        """模拟 LangChain 模型绑定工具后的返回值。

        Args:
            _tools: 待绑定的工具列表，测试中不使用具体内容。

        Returns:
            当前测试模型实例。
        """
        return self


def _valid_review_payload() -> dict[str, Any]:
    return {
        "original_pm_decision": "buy",
        "debate_correctness": "partially_correct",
        "correctness_reasoning": "方向判断部分正确，但回撤较大；行业强势被验证，追涨缺少成交确认。",
    }


def test_experience_review_output_uses_compact_fields():
    parsed = workflow._parse_experience_output(json.dumps(_valid_review_payload(), ensure_ascii=False))

    assert parsed is not None
    assert set(parsed.model_dump()) == {
        "original_pm_decision",
        "debate_correctness",
        "correctness_reasoning",
    }


def test_review_system_prompt_uses_configured_language(monkeypatch):
    monkeypatch.setattr(workflow.settings, "SYSTEM_LANGUAGE", "zh")
    zh_prompt = workflow._build_review_system_prompt("")
    assert "你是一名 A 股投研复盘分析师" in zh_prompt
    assert "You are an A-share review analyst" not in zh_prompt
    assert "最终 JSON Schema" in zh_prompt

    monkeypatch.setattr(workflow.settings, "SYSTEM_LANGUAGE", "en")
    en_prompt = workflow._build_review_system_prompt("")
    assert "You are an A-share review analyst" in en_prompt
    assert "你是一名 A 股投研复盘分析师" not in en_prompt
    assert "Final JSON Schema" in en_prompt


def test_review_system_prompt_keeps_memory_protocol_and_compact_output(monkeypatch):
    monkeypatch.setattr(workflow.settings, "SYSTEM_LANGUAGE", "zh")

    prompt = workflow._build_review_system_prompt("")

    assert "只需要完成三项输出" in prompt
    assert "不要输出记忆文档全文" in prompt
    assert "才调用 `write_memory` 重写整份记忆文档" in prompt
    assert "如果没有新增可复用经验，可以跳过全部记忆写入" in prompt
    assert "如果调用 `write_memory`" in prompt
    assert "原始 PM 结论" in prompt
    assert "区分真正主因、被验证信号、被证伪信号与噪音" in prompt
    assert "记忆写入协议:" in prompt
    assert "1. 写前必读:" in prompt
    assert "2. 整文档替换:" in prompt
    assert "未包含进 `content` 的旧内容会被永久丢弃" in prompt
    assert "3. 自由格式:" in prompt
    assert "不设固定模板" in prompt
    assert "4. 内容要素:" in prompt
    assert "必须同时包含真实股票名和股票代码" in prompt
    assert "若交易频率或交易策略无法确认" in prompt
    assert "5. 容量上限:" in prompt
    assert "6. 版本冲突:" in prompt
    assert "7. 写入次数:" in prompt
    assert "写入后的文档必须直接包含本次复盘的经验教训、可执行规则和适用边界" in prompt
    assert "最终 JSON 只返回 schema 中的三个字段" in prompt
    assert "[MEMORY_TOPIC" not in prompt
    assert "不同主题必须分次调用 `write_memory`" not in prompt

    monkeypatch.setattr(workflow.settings, "SYSTEM_LANGUAGE", "en")
    english_prompt = workflow._build_review_system_prompt("")

    assert "Return only three analytical fields" in english_prompt
    assert "do not repeat the timeline or the memory document" in english_prompt
    assert "Only after extracting reusable profitable experience" in english_prompt
    assert "rewrite the whole memory document" in english_prompt
    assert "you may skip all memory writes" in english_prompt
    assert "If you call `write_memory`" in english_prompt
    assert "Memory write protocol:" in english_prompt
    assert "1. Read before write:" in english_prompt
    assert "2. Whole-document replacement:" in english_prompt
    assert "anything not included in `content` is permanently discarded" in english_prompt
    assert "3. Free-form structure:" in english_prompt
    assert "no fixed template" in english_prompt
    assert "4. Content elements:" in english_prompt
    assert "include both the real stock name and stock code" in english_prompt
    assert "If trading frequency or strategy cannot be confirmed" in english_prompt
    assert "5. Size limit:" in english_prompt
    assert "6. Version conflict:" in english_prompt
    assert "7. Write count:" in english_prompt
    assert "[MEMORY_TOPIC" not in english_prompt
    assert "4. Split rule:" not in english_prompt


@pytest.mark.asyncio
async def test_review_allows_final_json_without_memory_write(monkeypatch):
    """无新增可复用经验时，复盘可以不调用 write_memory 直接返回最终 JSON。"""
    raw_llm = FakeRawLlm(
        [
            AIMessage(content=json.dumps(_valid_review_payload(), ensure_ascii=False)),
        ]
    )
    provider = FakeProvider(raw_llm)
    monkeypatch.setattr(workflow, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(workflow, "get_all_tools", lambda: [])
    monkeypatch.setattr(workflow, "build_memory_tools", lambda state: [])
    monkeypatch.setattr(workflow, "get_skills_loader_tools", lambda: [])
    monkeypatch.setattr(workflow, "build_skills_catalog_prompt", lambda: "")

    result = await workflow.review_debate_conclusion(
        {
            "user_id": 7,
            "session_id": "9a392c04-e965-41f2-8f74-f1163cedab6b",
            "stock_code": "601888.SH",
            "stock_name": "中国中免",
            "style_bucket": "swing",
            "trading_frequency": "波段",
            "trading_strategy": "趋势追踪",
            "full_context": {
                "pm_decision": {"decision": "buy"},
                "market_outcome_summary": {"return_20d": 0.03},
            },
        }
    )

    assert result["errors"] == []
    assert result["analysis_payload"]["original_pm_decision"] == "buy"
    assert result["analysis_payload"]["debate_correctness"] == "partially_correct"
    assert set(result["analysis_payload"]) == {
        "original_pm_decision",
        "debate_correctness",
        "correctness_reasoning",
        "written_memories",
    }
    assert result["analysis_payload"]["written_memories"] == []
    assert len(raw_llm.call_messages) == 1
    retry_messages = [
        message.content
        for message in raw_llm.call_messages[0]
        if isinstance(message, HumanMessage)
    ]
    assert not any("你还没有调用 `write_memory`" in content for content in retry_messages)


@pytest.mark.asyncio
async def test_review_records_write_memory_result_metadata(monkeypatch):
    """复盘工具轨迹应保留记忆写入标识和股票范围，便于结果审计。"""

    write_memory_calls = []

    class FakeWriteMemoryTool:
        name = "write_memory"

        async def ainvoke(self, args: dict[str, Any]) -> dict[str, Any]:
            write_memory_calls.append(args)
            return {
                "success": True,
                "status": "success",
                "memory_id": "md_abc123",
                "stock_code": "601888.SH",
                "version": 5,
                "size_chars": 1234,
            }

    raw_llm = FakeRawLlm(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_memory",
                        "args": {
                            "content": "中国中免(601888.SH)复盘经验：事件催化需要成交确认。",
                            "importance": "high",
                            "base_version": 4,
                        },
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content=json.dumps(_valid_review_payload(), ensure_ascii=False)),
        ]
    )
    provider = FakeProvider(raw_llm)
    monkeypatch.setattr(workflow, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(workflow, "get_all_tools", lambda: [])
    monkeypatch.setattr(workflow, "build_memory_tools", lambda state: [FakeWriteMemoryTool()])
    monkeypatch.setattr(workflow, "get_skills_loader_tools", lambda: [])
    monkeypatch.setattr(workflow, "build_skills_catalog_prompt", lambda: "")

    result = await workflow.review_debate_conclusion(
        {
            "user_id": 7,
            "session_id": "9a392c04-e965-41f2-8f74-f1163cedab6b",
            "stock_code": "601888.SH",
            "stock_name": "中国中免",
            "style_bucket": "swing",
            "trading_frequency": "波段",
            "trading_strategy": "趋势追踪",
            "full_context": {
                "pm_decision": {
                    "decision": "buy",
                    "created_at": "2026-01-02T09:35:00",
                },
                "market_outcome_summary": {"return_20d": 0.03},
            },
            "review_horizon": "20d",
            "reviewed_at": "2026-01-30T15:30:00",
        }
    )

    trace_result = result["tool_trace"][0]["result"]
    trace_args = result["tool_trace"][0]["args"]
    written_memory = result["analysis_payload"]["written_memories"][0]
    assert not write_memory_calls[0]["content"].startswith("时间:")
    assert trace_args["content"] == write_memory_calls[0]["content"]
    assert "event_id" not in trace_result
    assert trace_result["memory_id"] == "md_abc123"
    assert trace_result["stock_code"] == "601888.SH"
    assert written_memory == {
        "stock_code": "601888.SH",
        "status": "success",
        "size_chars": 1234,
        "max_chars": workflow.settings.MEMORY_DOC_MAX_CHARS,
        "version": 5,
    }
    assert "content" not in written_memory
    assert "memory_id" not in written_memory


@pytest.fixture(autouse=True)
def disable_llm_usage_log(monkeypatch):
    async def _noop_record_llm_usage(*args, **kwargs):
        return None

    monkeypatch.setattr(workflow, "record_llm_usage", _noop_record_llm_usage)


@pytest.mark.asyncio
async def test_final_json_retry_uses_raw_llm_without_tools():
    raw_llm = FakeRawLlm(
        [
            AIMessage(content=json.dumps(_valid_review_payload(), ensure_ascii=False)),
        ]
    )

    result = await workflow._retry_final_experience_json(
        raw_llm=raw_llm,
        llm_provider=FakeProvider(),
        messages=[],
        tool_trace=[
            {
                "name": "write_memory",
                "args": {"content": "复盘经验", "importance": "high"},
                "result": {"status": "success"},
            }
        ],
        review_events=[],
        session_id="9a392c04-e965-41f2-8f74-f1163cedab6b",
        stock_code="601888.SH",
    )

    assert result is not None
    assert result["errors"] == []
    assert result["analysis_payload"]["original_pm_decision"] == "buy"
    assert "internet_tools_used" not in result["analysis_payload"]
    assert result["analysis_payload"]["written_memories"][0] == {
        "stock_code": None,
        "status": "success",
        "size_chars": len("复盘经验"),
        "max_chars": workflow.settings.MEMORY_DOC_MAX_CHARS,
    }
    assert isinstance(raw_llm.call_messages[0][-1], HumanMessage)
    assert "不要再调用任何工具" in raw_llm.call_messages[0][-1].content


@pytest.mark.asyncio
async def test_final_json_retry_records_research_usage_lane(monkeypatch):
    usage_calls = []
    raw_llm = FakeRawLlm([AIMessage(content=json.dumps(_valid_review_payload(), ensure_ascii=False))])

    async def _record_usage(*args, **kwargs):
        usage_calls.append(kwargs)

    monkeypatch.setattr(workflow, "record_llm_usage", _record_usage)

    result = await workflow._retry_final_experience_json(
        raw_llm=raw_llm,
        llm_provider=FakeProvider(),
        messages=[],
        tool_trace=[],
        review_events=[],
        session_id="9a392c04-e965-41f2-8f74-f1163cedab6b",
        stock_code="601888.SH",
    )

    assert result is not None
    assert usage_calls[0]["cache_lane"] == "research"
    assert usage_calls[0]["api_key_alias"] == "research_llm_api_key"


@pytest.mark.asyncio
async def test_final_json_retry_retries_when_model_attempts_tool_call():
    raw_llm = FakeRawLlm(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_news",
                        "args": {"keyword": "中国中免"},
                        "id": "call_1",
                    }
                ],
            ),
            AIMessage(content=json.dumps(_valid_review_payload(), ensure_ascii=False)),
        ]
    )

    result = await workflow._retry_final_experience_json(
        raw_llm=raw_llm,
        llm_provider=FakeProvider(),
        messages=[],
        tool_trace=[],
        review_events=[],
        session_id="9a392c04-e965-41f2-8f74-f1163cedab6b",
        stock_code="601888.SH",
    )

    assert result is not None
    assert len(raw_llm.call_messages) == 2
    assert result["analysis_payload"]["debate_correctness"] == "partially_correct"
    retry_human_messages = [
        message.content
        for message in raw_llm.call_messages[1]
        if isinstance(message, HumanMessage)
    ]
    assert any("工具已经关闭" in content for content in retry_human_messages)
