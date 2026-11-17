import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.ai.agentic import context_compaction as context_compaction_module
from app.ai.agentic.context_compaction import (
    SUMMARY_STATUS_COMPACTED,
    SUMMARY_STATUS_NOT_TRIGGERED,
    SUMMARY_STATUS_OUTPUT_TRUNCATED,
    SUMMARY_STATUS_SUMMARY_CALL_OVER_WINDOW,
    apply_compaction_outcome,
    compact_and_apply,
    compact_messages,
    estimate_context_tokens,
)
from app.core.config import settings


class FakeSummaryModel:
    """记录摘要调用并返回预设摘要的 fake 模型。"""

    def __init__(self, content="## 1. Goal\n- summary", finish_reason="stop"):
        """初始化 fake 摘要模型。

        Args:
            content: 摘要正文。
            finish_reason: 响应 finish_reason。
        """
        self.content = content
        self.finish_reason = finish_reason
        self.calls = []

    async def ainvoke(self, messages):
        """记录摘要输入并返回预设响应。

        Args:
            messages: 摘要调用消息列表。

        Returns:
            带 finish_reason 的 AIMessage。
        """
        self.calls.append(list(messages))
        return AIMessage(
            content=self.content,
            response_metadata={"finish_reason": self.finish_reason},
        )


def _configure(monkeypatch, *, window=5000, ratio=0.1, output=500):
    """配置测试用上下文窗口参数。

    Args:
        monkeypatch: pytest monkeypatch fixture。
        window: 上下文窗口 token 数。
        ratio: 压缩触发比例。
        output: 摘要输出 token 上限。
    """
    monkeypatch.setattr(settings, "LLM_CONTEXT_WINDOW_TOKENS", window)
    monkeypatch.setattr(settings, "LLM_CONTEXT_COMPACTION_TRIGGER_RATIO", ratio)
    monkeypatch.setattr(settings, "LLM_CONTEXT_COMPACTION_SUMMARY_OUTPUT_TOKENS", output)


def _long_text(char_count: int) -> str:
    """生成指定长度的可分词文本。

    Args:
        char_count: 目标字符数。

    Returns:
        重复英文短语截断后的文本。
    """
    seed = "alpha beta gamma delta policy catalyst evidence "
    return (seed * (char_count // len(seed) + 1))[:char_count]


@pytest.mark.asyncio
async def test_compaction_not_triggered_keeps_messages_and_skips_summary(monkeypatch):
    """低于触发阈值时不调用摘要模型且不改写消息。"""
    _configure(monkeypatch, window=5000, ratio=0.9, output=500)
    messages = [SystemMessage(content="system"), HumanMessage(content="short input")]
    summary = FakeSummaryModel()

    outcome = await compact_messages(
        messages,
        model_name="fake-model",
        agent_name="test",
        summary_llm=summary,
    )

    assert outcome.status == SUMMARY_STATUS_NOT_TRIGGERED
    assert summary.calls == []
    assert apply_compaction_outcome(messages, outcome) is False
    assert [message.content for message in messages] == ["system", "short input"]


@pytest.mark.asyncio
async def test_summary_factory_is_not_called_when_compaction_is_not_triggered(monkeypatch):
    """未触发压缩时不构造摘要模型。"""
    _configure(monkeypatch, window=5000, ratio=0.9, output=500)
    messages = [SystemMessage(content="system"), HumanMessage(content="short input")]
    factory_calls = []

    def summary_factory():
        factory_calls.append(True)
        return FakeSummaryModel()

    await compact_and_apply(
        messages,
        model_name="fake-model",
        agent_name="test",
        llm_factory=summary_factory,
    )

    assert factory_calls == []


@pytest.mark.asyncio
async def test_triggered_compaction_estimates_exactly_before_and_after_summary(monkeypatch):
    """触发压缩时只估算摘要输入和压缩后的主请求各一次。"""
    _configure(monkeypatch, window=5000, ratio=0.1, output=500)
    messages = [SystemMessage(content="system"), HumanMessage(content=_long_text(4000))]
    summary = FakeSummaryModel()
    estimate_calls = []
    original_estimate = context_compaction_module.estimate_context_tokens

    def track_estimate(messages, *, model_name=None):
        estimate_calls.append(list(messages))
        return original_estimate(messages, model_name=model_name)

    monkeypatch.setattr(context_compaction_module, "estimate_context_tokens", track_estimate)

    outcome = await compact_messages(
        messages,
        model_name="fake-model",
        agent_name="test",
        summary_llm=summary,
    )

    assert outcome.status == SUMMARY_STATUS_COMPACTED
    assert len(estimate_calls) == 2
    assert str(estimate_calls[0][-1].content).startswith("你是上下文压缩器")
    assert estimate_calls[1][1].content.startswith("## 1. Goal")


@pytest.mark.asyncio
async def test_compaction_summarizes_feedback_and_keeps_only_system_and_summary(monkeypatch):
    """控制反馈进入摘要输入，压缩后消息列表严格只保留 system 和摘要。"""
    _configure(monkeypatch, window=5000, ratio=0.1, output=500)
    feedback = HumanMessage(content="retry with valid tool call")
    messages = [
        SystemMessage(content="system prompt"),
        HumanMessage(content=_long_text(4000)),
        feedback,
    ]
    summary = FakeSummaryModel()

    outcome = await compact_messages(
        messages,
        model_name="fake-model",
        agent_name="test",
        summary_llm=summary,
    )

    assert outcome.status == SUMMARY_STATUS_COMPACTED
    assert len(summary.calls) == 1
    summary_input = summary.calls[0]
    assert any(message is feedback for message in summary_input)
    assert str(summary_input[-1].content).startswith("你是上下文压缩器")
    assert apply_compaction_outcome(messages, outcome) is True
    assert len(messages) == 2
    assert messages[0].content == "system prompt"
    assert messages[1].name is None
    assert messages[1].additional_kwargs == {}
    assert messages[1].content.endswith("## 1. Goal\n- summary")


@pytest.mark.asyncio
async def test_summary_input_over_window_fails_without_replacing_tool_results(monkeypatch):
    """摘要输入加输出超窗时直接失败，不改写工具结果。"""
    _configure(monkeypatch, window=5000, ratio=0.1, output=500)
    current_tool = ToolMessage(tool_call_id="current-call", content=_long_text(30000))
    messages = [
        SystemMessage(content="system"),
        AIMessage(
            content="",
            tool_calls=[{"name": "search_news", "args": {}, "id": "current-call"}],
        ),
        current_tool,
    ]
    summary = FakeSummaryModel()

    outcome = await compact_messages(
        messages,
        model_name="fake-model",
        agent_name="test",
        summary_llm=summary,
    )

    assert outcome.status == SUMMARY_STATUS_SUMMARY_CALL_OVER_WINDOW
    assert summary.calls == []
    assert messages[-1] is current_tool


@pytest.mark.asyncio
async def test_compaction_rejects_truncated_summary_output(monkeypatch):
    """finish_reason=length 的摘要不被采用。"""
    _configure(monkeypatch, window=5000, ratio=0.1, output=500)
    messages = [SystemMessage(content="system"), HumanMessage(content=_long_text(4000))]
    summary = FakeSummaryModel(content="truncated summary", finish_reason="length")

    outcome = await compact_messages(
        messages,
        model_name="fake-model",
        agent_name="test",
        summary_llm=summary,
    )

    assert outcome.status == SUMMARY_STATUS_OUTPUT_TRUNCATED
    assert apply_compaction_outcome(messages, outcome) is False


def test_context_estimate_includes_additional_kwargs():
    """token 估算应覆盖 additional_kwargs 中的大字段。"""
    plain = [HumanMessage(content="hello")]
    enriched = [
        HumanMessage(
            content="hello",
            additional_kwargs={"reasoning_content": _long_text(2000)},
        )
    ]

    plain_tokens = estimate_context_tokens(plain, model_name="fake-model").input_tokens
    enriched_tokens = estimate_context_tokens(enriched, model_name="fake-model").input_tokens

    assert enriched_tokens > plain_tokens
