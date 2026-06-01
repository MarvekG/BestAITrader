from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol


class LLMProviderPlugin(Protocol):
    """LLM provider 插件协议。"""

    provider_name: str

    def build_chat_model(
        self,
        *,
        model: str | None = None,
        temperature: float | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        extra_body: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        创建 LangChain chat model。

        Args:
            model: 模型名称。
            temperature: 采样温度。
            api_key: API key。
            base_url: API 地址。
            extra_body: OpenAI-compatible provider 扩展参数。
            **kwargs: 透传给具体 LangChain chat model 的参数。

        Returns:
            LangChain chat model 实例。
        """
        ...

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
        构造 OpenAI-compatible chat.completions.create 参数。

        Args:
            model: 模型名称。
            messages: OpenAI-compatible 消息列表。
            temperature: 采样温度。
            max_tokens: 最大输出 token 数。
            response_format: 响应格式。
            extra_body: provider 扩展参数。

        Returns:
            可传给 OpenAI SDK chat.completions.create 的参数。
        """
        ...

    def sanitize_tool_call_response_for_replay(self, response: Any) -> tuple[Any, list[dict[str, Any]]]:
        """
        清理 provider 不能安全回放的 tool call 响应字段。

        Args:
            response: LangChain AIMessage 响应。

        Returns:
            清理后的响应与被移除的 invalid tool calls。
        """
        ...

    def build_invalid_tool_call_retry_message(self, invalid_tool_calls: Iterable[dict[str, Any]]) -> str:
        """
        构造 invalid tool call 重试提示。

        Args:
            invalid_tool_calls: 被清理掉的无效 tool calls。

        Returns:
            发送给 LLM 的重试提示。
        """
        ...
