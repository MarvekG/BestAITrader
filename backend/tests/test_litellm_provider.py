from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import invalid_tool_call

from app.ai.llm_providers.litellm import LiteLLMChatOpenAI, LiteLLMProvider


def test_build_chat_completion_kwargs_uses_litellm_shape() -> None:
    provider = LiteLLMProvider()

    kwargs = provider.build_chat_completion_kwargs(
        model="backend",
        messages=[{"role": "user", "content": "hello"}],
        temperature=0.2,
        max_tokens=128,
        response_format={"type": "json_object"},
        extra_body={"thinking": {"type": "disabled"}},
    )

    assert kwargs == {
        "model": "backend",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.2,
        "max_tokens": 128,
        "response_format": {"type": "json_object"},
        "extra_body": {"thinking": {"type": "disabled"}},
    }


def test_build_chat_completion_kwargs_does_not_inject_thinking_default() -> None:
    provider = LiteLLMProvider()

    kwargs = provider.build_chat_completion_kwargs(
        model="backend",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert kwargs == {
        "model": "backend",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_build_chat_model_uses_configured_timeout_and_retries() -> None:
    provider = LiteLLMProvider()

    llm = provider.build_chat_model(
        model="backend",
        api_key="sk-test",
        base_url="http://litellm:4000/v1",
    )

    assert llm.request_timeout == 240.0
    assert llm.max_retries == 3


def test_litellm_chat_model_preserves_reasoning_content_from_response() -> None:
    llm = LiteLLMChatOpenAI(
        model="backend",
        api_key="sk-test",
        base_url="http://litellm:4000/v1",
    )

    result = llm._create_chat_result(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1,
            "model": "backend",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "final answer",
                        "reasoning_content": "reasoning trace",
                    },
                }
            ],
        }
    )

    message = result.generations[0].message
    assert message.additional_kwargs["reasoning_content"] == "reasoning trace"


def test_litellm_chat_model_replays_reasoning_content_in_request_payload() -> None:
    llm = LiteLLMChatOpenAI(
        model="backend",
        api_key="sk-test",
        base_url="http://litellm:4000/v1",
    )
    assistant_message = AIMessage(
        content="",
        additional_kwargs={"reasoning_content": "reasoning trace"},
        tool_calls=[
            {
                "id": "call-1",
                "name": "search_news",
                "args": {"query": "平安银行"},
                "type": "tool_call",
            }
        ],
    )

    payload = llm._get_request_payload(
        [
            HumanMessage(content="查新闻"),
            assistant_message,
            ToolMessage(tool_call_id="call-1", content="新闻结果"),
        ]
    )

    assistant_payload = payload["messages"][1]
    assert assistant_payload["role"] == "assistant"
    assert assistant_payload["reasoning_content"] == "reasoning trace"


def test_sanitize_tool_call_response_removes_invalid_raw_tool_calls() -> None:
    response = AIMessage(
        content="mixed tool calls",
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "tool-good-1",
                    "type": "function",
                    "function": {
                        "name": "search_news",
                        "arguments": '{"query":"平安银行"}',
                    },
                },
                {
                    "id": "tool-bad-1",
                    "type": "function",
                    "function": {
                        "name": "search_news",
                        "arguments": '{"query"',
                    },
                },
            ]
        },
        tool_calls=[
            {
                "name": "search_news",
                "args": {"query": "平安银行"},
                "id": "tool-good-1",
                "type": "tool_call",
            }
        ],
        invalid_tool_calls=[
            invalid_tool_call(
                name="search_news",
                id="tool-bad-1",
                args='{"query"',
                error="unexpected end of JSON input",
            )
        ],
    )

    provider = LiteLLMProvider()
    sanitized_response, invalid_tool_calls = provider.sanitize_tool_call_response_for_replay(response)

    assert len(invalid_tool_calls) == 1
    assert not sanitized_response.invalid_tool_calls
    assert sanitized_response.additional_kwargs["tool_calls"] == [
        {
            "id": "tool-good-1",
            "type": "function",
            "function": {
                "name": "search_news",
                "arguments": '{"query":"平安银行"}',
            },
        }
    ]
