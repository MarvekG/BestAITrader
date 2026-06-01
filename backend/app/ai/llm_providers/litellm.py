from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from app.ai.llm_providers.base import LLMProviderPlugin
from app.core.config import settings


class LiteLLMProvider(LLMProviderPlugin):
    """LiteLLM gateway provider，统一通过 OpenAI-compatible 协议访问模型。"""

    provider_name = "litellm"

    def build_chat_model(
        self,
        *,
        model: str | None = None,
        temperature: float | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ChatOpenAI:
        """
        创建指向 LiteLLM gateway 的 LangChain chat model。

        Args:
            model: LiteLLM 模型别名；为空时使用后端默认别名。
            temperature: 采样温度。
            api_key: LiteLLM gateway key；为空时使用集中配置。
            base_url: LiteLLM OpenAI-compatible 地址；为空时使用集中配置。
            extra_body: 需要透传给 LiteLLM 的扩展参数。
            **kwargs: 透传给 `ChatOpenAI` 的其它参数。

        Returns:
            `ChatOpenAI` 实例。
        """

        model_kwargs: dict[str, Any] = {
            "model": model or settings.LLM_MODEL,
            "api_key": api_key or settings.LLM_API_KEY,
            "base_url": base_url or settings.LLM_BASE_URL,
            **kwargs,
        }
        if temperature is not None:
            model_kwargs["temperature"] = temperature
        if extra_body is not None:
            model_kwargs["extra_body"] = dict(extra_body)
        return LiteLLMChatOpenAI(**model_kwargs)

    def build_chat_completion_kwargs(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: Mapping[str, Any] | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        构造 LiteLLM OpenAI-compatible chat completion 参数。

        Args:
            model: LiteLLM 模型别名。
            messages: OpenAI-compatible 消息列表。
            temperature: 采样温度。
            max_tokens: 最大输出 token 数。
            response_format: 响应格式。
            extra_body: 需要透传给 LiteLLM 的扩展参数。

        Returns:
            可传给 OpenAI SDK `chat.completions.create` 的参数。
        """

        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if temperature is not None:
            request_kwargs["temperature"] = temperature
        if max_tokens is not None:
            request_kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            request_kwargs["response_format"] = response_format
        if extra_body is not None:
            request_kwargs["extra_body"] = dict(extra_body)
        return request_kwargs

    def sanitize_tool_call_response_for_replay(self, response: Any) -> tuple[Any, list[dict[str, Any]]]:
        """
        清理不能安全回放的 invalid tool calls。

        Args:
            response: LangChain AIMessage 响应。

        Returns:
            清理后的响应与被移除的 invalid tool calls。
        """

        invalid_tool_calls = list(getattr(response, "invalid_tool_calls", []) or [])
        if not invalid_tool_calls:
            return response, []

        sanitized_additional_kwargs = _sanitize_additional_kwargs_tool_calls(response, invalid_tool_calls)
        if hasattr(response, "model_copy"):
            return response.model_copy(
                update={
                    "invalid_tool_calls": [],
                    "additional_kwargs": sanitized_additional_kwargs,
                }
            ), invalid_tool_calls

        if hasattr(response, "invalid_tool_calls"):
            response.invalid_tool_calls = []
        if hasattr(response, "additional_kwargs"):
            response.additional_kwargs = sanitized_additional_kwargs
        return response, invalid_tool_calls

    def build_invalid_tool_call_retry_message(self, invalid_tool_calls: Iterable[dict[str, Any]]) -> str:
        """
        构造 invalid tool call 重试提示。

        Args:
            invalid_tool_calls: 被清理掉的无效 tool calls。

        Returns:
            给 LLM 的重试提示。
        """

        details = []
        for index, tool_call in enumerate(invalid_tool_calls, start=1):
            name = tool_call.get("name") or "unknown_tool"
            tool_call_id = tool_call.get("id") or f"invalid-{index}"
            args = tool_call.get("args")
            error = tool_call.get("error") or "tool arguments were not valid JSON"
            args_text = str(args) if args not in (None, "") else "<empty>"
            details.append(
                f"{index}. tool={name}, id={tool_call_id}, error={error}, raw_args={args_text}"
            )

        detail_text = "\n".join(details)
        return (
            "你上一条消息里包含无效的 tool 调用参数。"
            "请用合法 JSON 参数重新发起需要的工具调用；如果不需要工具，请直接给出最终答案。\n"
            "Your previous reply included invalid tool-call arguments. "
            "Retry needed tool calls with valid JSON arguments; if no tools are needed, return the final answer.\n"
            f"{detail_text}"
        )


class LiteLLMChatOpenAI(ChatOpenAI):
    """
    LiteLLM 专用 ChatOpenAI 适配器。

    LiteLLM 会把多家 provider 的 reasoning 输出标准化为 `reasoning_content`。
    当 provider 要求工具调用后的多轮请求回放该字段时，这里负责在 LangChain
    消息和 OpenAI-compatible payload 之间保留并回传该标准字段。
    """

    def _create_chat_result(self, response: Any, generation_info: dict[str, Any] | None = None) -> Any:
        result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        choices = response_dict.get("choices") or []

        for generation, choice in zip(result.generations, choices):
            message_payload = choice.get("message") or {}
            if not isinstance(message_payload, dict) or "reasoning_content" not in message_payload:
                continue
            reasoning_content = message_payload.get("reasoning_content")
            if reasoning_content is not None:
                generation.message.additional_kwargs["reasoning_content"] = reasoning_content
        return result

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        source_messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        payload_messages = payload.get("messages")
        if not isinstance(payload_messages, list):
            return payload

        for source_message, payload_message in zip(source_messages, payload_messages):
            if not isinstance(source_message, AIMessage) or not isinstance(payload_message, dict):
                continue
            reasoning_content = source_message.additional_kwargs.get("reasoning_content")
            if reasoning_content is not None and payload_message.get("role") == "assistant":
                payload_message["reasoning_content"] = reasoning_content
        return payload


def _sanitize_additional_kwargs_tool_calls(
    response: Any,
    invalid_tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    additional_kwargs = dict(getattr(response, "additional_kwargs", {}) or {})
    raw_tool_calls = additional_kwargs.get("tool_calls")
    if not isinstance(raw_tool_calls, list):
        return additional_kwargs

    invalid_tool_call_ids = {
        tool_call.get("id")
        for tool_call in invalid_tool_calls
        if tool_call.get("id")
    }
    valid_tool_call_ids = {
        tool_call.get("id")
        for tool_call in list(getattr(response, "tool_calls", []) or [])
        if tool_call.get("id")
    }

    filtered_tool_calls = []
    for raw_tool_call in raw_tool_calls:
        tool_call_id = raw_tool_call.get("id") if isinstance(raw_tool_call, dict) else None
        if tool_call_id in invalid_tool_call_ids:
            continue
        if valid_tool_call_ids and tool_call_id and tool_call_id not in valid_tool_call_ids:
            continue
        if not valid_tool_call_ids and invalid_tool_calls:
            continue
        filtered_tool_calls.append(raw_tool_call)

    if filtered_tool_calls:
        additional_kwargs["tool_calls"] = filtered_tool_calls
    else:
        additional_kwargs.pop("tool_calls", None)
    return additional_kwargs
