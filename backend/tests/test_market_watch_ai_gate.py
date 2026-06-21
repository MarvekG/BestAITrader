import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.ai.market_watch import ai_gate
from app.ai.market_watch.ai_gate import (
    WatchAiGate,
    build_watch_ai_messages,
    build_watch_ai_prompt,
    parse_watch_ai_decision,
    should_launch_debate,
)
from app.ai.market_watch.schemas import WatchAiDecision


class FakeLlmClient:
    def __init__(self, response):
        self.response = response
        self.messages = None

    async def complete_json(self, messages):
        self.messages = messages
        return self.response


def _start_debate_payload():
    return [{
        "stock_code": "600519",
        "stock_name": "贵州茅台",
        "action": "start_debate",
        "confidence": 0.9,
        "urgency": "high",
        "trigger_reason": "新闻冲击",
        "evidence_summary": "证据",
        "debate_parameters": {
            "trading_frequency": "day",
            "trading_strategy": "trend",
            "simplified": False,
            "debate_focus": ["风险"],
            "risk_notes": ["需验证"],
        },
    }]


def test_build_watch_ai_prompt_forbids_guessing() -> None:
    prompt = build_watch_ai_prompt()

    assert "不得编造" in prompt
    assert "直接输出合法 JSON array" in prompt
    assert "禁止输出 markdown" in prompt
    assert "JSON array" in prompt
    assert "warehouse_stocks[].trading_frequency_code" in prompt
    assert "warehouse_stocks[].trading_strategy_code" in prompt
    assert "recent_debate_launches" in prompt
    assert "24 小时" in prompt
    assert '["day", "swing", "position"]' in prompt
    assert '["value", "trend"]' in prompt
    assert "Pydantic JSON Schema" in prompt
    assert '"required"' in prompt
    assert '"action"' in prompt
    assert '"confidence"' in prompt
    assert "数据来源字段" not in prompt
    assert "quote.change_percent" not in prompt
    assert "news_documents[].markdown" not in prompt


def test_build_watch_ai_prompt_uses_configured_recent_debate_window() -> None:
    prompt = build_watch_ai_prompt(recent_debate_lookback_hours=36)

    assert "过去 36 小时" in prompt


def test_build_watch_ai_prompt_requires_timestamp_aware_recent_debate_deduplication() -> None:
    prompt = build_watch_ai_prompt()

    assert "created_at" in prompt
    assert "判断本轮证据是否属于新增时" in prompt
    assert "应先核对 `recent_debate_launches` 是否已经覆盖" in prompt
    assert "相同或实质相同的事实组合、事件主题或证据摘要" in prompt
    assert "数值相同只是辅助线索" in prompt
    assert "不得将其称为“本轮新增”" in prompt
    assert "不得仅因扫描时间更晚而再次 `start_debate`" in prompt


def test_build_watch_ai_prompt_can_disable_recent_debate_deduplication() -> None:
    prompt = build_watch_ai_prompt(recent_debate_dedup_enabled=False)

    assert "近期辩论判重已关闭" in prompt
    assert "不得使用 `recent_debate_launches` 阻止 `start_debate`" in prompt


def test_build_watch_ai_messages_uses_recent_debate_dedup_setting() -> None:
    messages = build_watch_ai_messages({"settings": {"recent_debate_dedup_enabled": False}})

    assert "近期辩论判重已关闭" in messages[0]["content"]


def test_build_watch_ai_messages_uses_recent_debate_lookback_setting() -> None:
    messages = build_watch_ai_messages({"settings": {"recent_debate_lookback_hours": 48}})

    assert "过去 48 小时" in messages[0]["content"]


def test_should_launch_debate_requires_debate_parameters() -> None:
    decision = WatchAiDecision(
        stock_code="600519",
        stock_name="贵州茅台",
        action="start_debate",
        confidence=0.9,
        urgency="high",
        trigger_reason="新闻冲击",
        evidence_summary="证据",
    )

    assert should_launch_debate(decision) is False


def test_should_launch_debate_accepts_complete_high_confidence_decision() -> None:
    decision = WatchAiDecision(**_start_debate_payload()[0])

    assert should_launch_debate(decision) is True


def test_parse_watch_ai_decision_validates_json_string() -> None:
    payload = _start_debate_payload()
    text = WatchAiDecision(**payload[0]).model_dump_json()

    decisions = parse_watch_ai_decision(f"[{text}]")

    assert len(decisions) == 1
    assert decisions[0].action == "start_debate"
    assert decisions[0].stock_code == "600519"
    assert decisions[0].debate_parameters is not None
    assert decisions[0].debate_parameters.trading_frequency == "day"


def test_parse_watch_ai_decision_strips_fenced_json_array() -> None:
    payload = _start_debate_payload()
    text = WatchAiDecision(**payload[0]).model_dump_json()

    decisions = parse_watch_ai_decision(f"```json\n[{text}]\n```")

    assert len(decisions) == 1
    assert decisions[0].stock_code == "600519"
    assert decisions[0].action == "start_debate"


def test_parse_watch_ai_decision_rejects_object_root() -> None:
    with pytest.raises(ValidationError):
        parse_watch_ai_decision({"action": "start_debate", "confidence": 2})


def test_parse_watch_ai_decision_logs_invalid_json_response(monkeypatch: pytest.MonkeyPatch) -> None:
    exception_calls = []
    response = "```json\nnot json\n```"

    def fake_exception(message: str, **kwargs) -> None:
        exception_calls.append((message, kwargs))

    monkeypatch.setattr(ai_gate, "logger", SimpleNamespace(exception=fake_exception), raising=False)

    with pytest.raises(json.JSONDecodeError):
        parse_watch_ai_decision(response)

    assert exception_calls == [
        (
            "Failed to parse Watch AI JSON response",
            {
                "extra": {
                    "response_length": len(response),
                    "response_preview": response,
                },
            },
        )
    ]


def test_parse_watch_ai_decision_rejects_long_trading_preferences() -> None:
    payload = _start_debate_payload()
    payload[0]["debate_parameters"]["trading_frequency"] = "日内交易 (Day Trading)"
    payload[0]["debate_parameters"]["trading_strategy"] = "趋势追踪 (Trend Following)"

    with pytest.raises(ValidationError):
        parse_watch_ai_decision(payload)


@pytest.mark.asyncio
async def test_watch_ai_gate_sends_prompt_and_payload_to_llm() -> None:
    client = FakeLlmClient(_start_debate_payload())
    gate = WatchAiGate(client)

    decisions = await gate.decide({
        "warehouse_stocks": [{"stock_code": "600519", "stock_name": "贵州茅台"}],
        "news_documents": [{"title": "新闻快讯"}],
    })

    assert len(decisions) == 1
    assert decisions[0].action == "start_debate"
    assert client.messages is not None
    assert client.messages[0]["role"] == "system"
    assert "盯盘触发 AI" in client.messages[0]["content"]
    assert client.messages[1]["role"] == "user"
    assert client.messages[1]["content"].startswith("DATABASE_CONTEXT\n")
    assert '"warehouse_stocks"' in client.messages[1]["content"]
    assert '"news_items"' not in client.messages[1]["content"]
    assert client.messages[2]["role"] == "user"
    assert client.messages[2]["content"].startswith("SOURCE_DOCUMENT_CONTEXT\n")
    assert '"news_documents"' in client.messages[2]["content"]
    assert '"news_items"' not in client.messages[2]["content"]


def test_build_watch_ai_messages_separates_database_context_from_source_documents() -> None:
    messages = build_watch_ai_messages({
        "user_id": 7,
        "settings": {},
        "warehouse_stocks": [{
            "stock_code": "000001",
            "trading_frequency_code": "position",
            "trading_strategy_code": "value",
        }],
        "account_summary": {"total_assets": 1000.0},
        "positions": [{"stock_code": "000001", "market_value": 500.0}],
        "data_documents": [{"id": "data-1", "markdown": "data"}],
        "news_documents": [{"id": "news-1", "markdown": "news"}],
    })

    assert [message["role"] for message in messages] == ["system", "user", "user"]
    assert messages[1]["content"].startswith("DATABASE_CONTEXT\n")
    assert '"warehouse_stocks"' in messages[1]["content"]
    assert '"positions"' in messages[1]["content"]
    assert '"news_documents"' not in messages[1]["content"]
    assert messages[2]["content"].startswith("SOURCE_DOCUMENT_CONTEXT\n")
    assert '"news_documents"' in messages[2]["content"]
    assert '"warehouse_stocks"' not in messages[2]["content"]
