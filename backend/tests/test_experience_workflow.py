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
        "thesis_summary": "PM 结论部分有效，但复盘后需要降低确定性。",
        "recommended_action": "hold",
        "confidence_score": 70,
        "debate_correctness": "partially_correct",
        "correctness_score": 65,
        "review_triads": {
            "original_judgment": {
                "verdict": "partially_correct",
                "score": 65,
                "pm_decision": "buy",
                "outcome_basis": "20D 收益跑赢指数但回撤较大。",
                "reasoning": "方向判断部分正确，仓位纪律不足。",
            },
            "signal_validation": {
                "validated_signals": [
                    {
                        "signal": "行业相对强势",
                        "evidence": "20D 相对行业收益为正。",
                        "impact": "high",
                        "lesson": "行业相对强势可以提高持有耐心。",
                    }
                ],
                "invalidated_signals": [
                    {
                        "signal": "短线突破",
                        "evidence": "突破后回撤较大。",
                        "impact": "medium",
                        "lesson": "突破信号需要成交确认。",
                    }
                ],
                "noise_signals": [
                    {
                        "signal": "短期情绪",
                        "reason": "未对应价格路径。",
                    }
                ],
            },
            "decision_process_improvement": {
                "debate_changes": ["技术面 Agent 补充成交确认。"],
                "pm_changes": ["PM 加仓前检查回撤承受度。"],
                "risk_control_changes": ["跌破失效位时重新辩论。"],
            },
        },
        "experience_tags": {
            "stock_tags": ["601888.SH"],
            "industry_tags": ["旅游"],
            "strategy_tags": ["swing"],
            "failure_lesson_tags": ["仓位纪律不足"],
            "position_discipline_tags": ["加仓过早"],
            "signal_tags": ["行业相对强势", "短线突破"],
            "market_regime_tags": ["震荡市"],
        },
    }


def test_experience_review_output_requires_triads():
    payload = _valid_review_payload()
    payload.pop("review_triads")

    assert workflow._parse_experience_output(json.dumps(payload, ensure_ascii=False)) is None


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


def test_review_system_prompt_requires_process_improvements_for_future_pm(monkeypatch):
    monkeypatch.setattr(workflow.settings, "SYSTEM_LANGUAGE", "zh")

    prompt = workflow._build_review_system_prompt("")

    assert "未来 Debate / PM" in prompt
    assert "从本次复盘证据和召回记忆中归纳" in prompt
    assert "不要预设固定问题清单" in prompt
    assert "交易频率和交易策略" in prompt
    assert "止损或反转条件" in prompt
    assert "写入记忆时" in prompt
    assert "才调用 `write_memory` 写入记忆" in prompt
    assert "如果没有新增可复用经验，可以跳过全部记忆写入" in prompt
    assert "如果调用 `write_memory`" in prompt
    assert "原始 PM 结论正确性" in prompt
    assert "未来 Debate / PM / 风控检查项" in prompt
    assert "不要把整个复盘表格原样塞进记忆" in prompt
    assert "[MEMORY_TOPIC: decision_outcome]" in prompt
    assert "[MEMORY_TOPIC: driver_validation]" in prompt
    assert "[MEMORY_TOPIC: risk_control]" in prompt
    assert "[MEMORY_TOPIC: strategy_fit]" in prompt
    assert "[MEMORY_TOPIC: process_improvement]" in prompt
    assert "不同主题必须分次调用 `write_memory`" in prompt
    assert "一条 Memory 只写一个主主题" in prompt
    assert "不要把多个主题揉成一条 Memory" in prompt
    assert "没有新增经验的主题可以跳过" in prompt
    assert "推荐写入顺序" in prompt
    assert "复盘写入必须包含后验市场结果或信号验证证据" in prompt
    assert "先判断哪些主题有新增经验" in prompt
    assert "每个 `write_memory` 调用只承载一个主题" in prompt
    assert "主题之间不要互相夹带" in prompt
    assert "记忆写入协议:" in prompt
    assert "1. 写入前提:" in prompt
    assert "2. 内容要素:" in prompt
    assert "必须同时包含真实股票名和股票代码" in prompt
    assert "交易频率、交易策略" in prompt
    assert "若交易频率或交易策略无法确认" in prompt
    assert "3. 推荐写入顺序:" in prompt
    assert "3.1 [MEMORY_TOPIC: decision_outcome]:" in prompt
    assert "如果原始 PM 结论有明确后验结果" in prompt
    assert "后续收益/回撤/相对收益和结论正确性" in prompt
    assert "3.2 [MEMORY_TOPIC: driver_validation]:" in prompt
    assert "如果能区分被验证、被证伪和噪音信号" in prompt
    assert "被排除伪因" in prompt
    assert "3.3 [MEMORY_TOPIC: risk_control]:" in prompt
    assert "如果仓位、止损、加仓、减仓、退出或回撤管理有教训" in prompt
    assert "板块 Beta" in prompt
    assert "3.4 [MEMORY_TOPIC: strategy_fit]:" in prompt
    assert "如果经验的适用频率、策略或市场环境存在明显边界" in prompt
    assert "经验是否过时及原因" in prompt
    assert "3.5 [MEMORY_TOPIC: process_improvement]:" in prompt
    assert "如果能提炼出未来 Debate / PM / Risk 的流程检查项" in prompt
    assert "Risk Control 要检查哪些否决条件" in prompt
    assert "4. 拆分规则:" in prompt
    assert "5. 推荐结构:" in prompt
    assert "对象:、交易频率:、交易策略:" in prompt
    assert "对象必须同时包含真实股票名和股票代码" in prompt
    assert "6. 写入质量:" in prompt
    assert "7. 适用边界:" in prompt
    assert "不要在代码中硬编码强制主题检查" not in prompt
    assert "关键词匹配判断记忆是否合格" not in prompt

    monkeypatch.setattr(workflow.settings, "SYSTEM_LANGUAGE", "en")
    english_prompt = workflow._build_review_system_prompt("")

    assert "Only after extracting reusable profitable experience" in english_prompt
    assert "you may skip all memory writes" in english_prompt
    assert "If you call `write_memory`" in english_prompt
    assert "Memory write protocol:" in english_prompt
    assert "1. Write precondition:" in english_prompt
    assert "2. Content elements:" in english_prompt
    assert "include both the real stock name and stock code" in english_prompt
    assert "trading frequency, trading strategy" in english_prompt
    assert "If trading frequency or strategy cannot be confirmed" in english_prompt
    assert "3. Recommended write order:" in english_prompt
    assert "3.1 [MEMORY_TOPIC: decision_outcome]:" in english_prompt
    assert "if the original PM conclusion has clear later outcome evidence" in english_prompt
    assert "later return/drawdown/relative return, and correctness" in english_prompt
    assert "3.2 [MEMORY_TOPIC: driver_validation]:" in english_prompt
    assert "if validated, falsified, and noisy signals can be separated" in english_prompt
    assert "rejected false causes" in english_prompt
    assert "3.3 [MEMORY_TOPIC: risk_control]:" in english_prompt
    assert "if sizing, stop-loss, add, reduce, exit, or drawdown control produced a lesson" in english_prompt
    assert "sector beta" in english_prompt
    assert "3.4 [MEMORY_TOPIC: strategy_fit]:" in english_prompt
    assert "if the lesson has clear frequency, strategy, or market-regime boundaries" in english_prompt
    assert "whether the lesson is stale and why" in english_prompt
    assert "3.5 [MEMORY_TOPIC: process_improvement]:" in english_prompt
    assert "if future Debate / PM / Risk checklist items can be extracted" in english_prompt
    assert "which veto checks Risk Control must run" in english_prompt
    assert "4. Split rule:" in english_prompt
    assert "5. Recommended structure:" in english_prompt
    assert "Trading frequency:, Trading strategy:" in english_prompt
    assert "Object must include both the real stock name and stock code" in english_prompt
    assert "6. Write quality:" in english_prompt
    assert "7. Applicability boundary:" in english_prompt
    assert "Do not hard-code topic enforcement in code" not in english_prompt
    assert "keyword matching" not in english_prompt


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
    assert result["analysis_payload"]["recommended_action"] == "hold"
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
    """复盘工具轨迹应保留 Memory 写入标识和股票范围，便于经验库索引。"""

    write_memory_calls = []

    class FakeWriteMemoryTool:
        name = "write_memory"

        async def ainvoke(self, args: dict[str, Any]) -> dict[str, Any]:
            write_memory_calls.append(args)
            return {
                "success": True,
                "status": "accepted",
                "observation_id": "obs_1",
                "memo_session": "stock",
                "stock_code": "601888.SH",
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

    trace_result = result["analysis_payload"]["tool_invocation_summary"][0]["result"]
    trace_args = result["analysis_payload"]["tool_invocation_summary"][0]["args"]
    written_memory = result["analysis_payload"]["written_memories"][0]
    assert write_memory_calls[0]["content"].startswith(
        "时间: 决策时间: 2026-01-02T09:35:00；复盘时间: 2026-01-30T15:30:00；复盘周期: 20d\n"
    )
    assert trace_args["content"] == write_memory_calls[0]["content"]
    assert written_memory["content"] == write_memory_calls[0]["content"]
    assert "event_id" not in trace_result
    assert trace_result["observation_id"] == "obs_1"
    assert trace_result["memo_session"] == "stock"
    assert trace_result["stock_code"] == "601888.SH"
    assert "event_id" not in written_memory
    assert written_memory["observation_id"] == "obs_1"
    assert written_memory["memo_session"] == "stock"
    assert written_memory["stock_code"] == "601888.SH"


@pytest.fixture(autouse=True)
def disable_llm_usage_log(monkeypatch):
    monkeypatch.setattr(workflow, "record_llm_usage", lambda *args, **kwargs: None)


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
        internet_tools_used={"search_news"},
        session_id="9a392c04-e965-41f2-8f74-f1163cedab6b",
        stock_code="601888.SH",
    )

    assert result is not None
    assert result["errors"] == []
    assert result["analysis_payload"]["recommended_action"] == "hold"
    assert result["analysis_payload"]["internet_tools_used"] == ["search_news"]
    assert result["analysis_payload"]["written_memories"][0]["content"] == "复盘经验"
    assert isinstance(raw_llm.call_messages[0][-1], HumanMessage)
    assert "不要再调用任何工具" in raw_llm.call_messages[0][-1].content


@pytest.mark.asyncio
async def test_final_json_retry_records_research_usage_lane(monkeypatch):
    usage_calls = []
    raw_llm = FakeRawLlm([AIMessage(content=json.dumps(_valid_review_payload(), ensure_ascii=False))])
    monkeypatch.setattr(workflow, "record_llm_usage", lambda *args, **kwargs: usage_calls.append(kwargs))

    result = await workflow._retry_final_experience_json(
        raw_llm=raw_llm,
        llm_provider=FakeProvider(),
        messages=[],
        tool_trace=[],
        review_events=[],
        internet_tools_used=set(),
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
        internet_tools_used=set(),
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
