from __future__ import annotations

from typing import Any, Mapping

from app.ai.llm_providers.base import LLMProviderPlugin
from app.ai.llm_providers.litellm import LiteLLMProvider
from app.core.config import settings


def get_llm_provider(provider_name: str | None = None) -> LLMProviderPlugin:
    """
    获取 LLM provider 插件。

    Args:
        provider_name: provider 名称；为空时使用配置中的固定 provider `litellm`。

    Returns:
        LiteLLM provider 插件。

    Raises:
        ValueError: provider 不受支持。
    """

    resolved_name = _normalize_provider_name(provider_name or settings.LLM_PROVIDER)
    for plugin in _provider_plugins():
        if resolved_name == _normalize_provider_name(plugin.provider_name):
            return plugin

    supported = sorted(plugin.provider_name for plugin in _provider_plugins())
    raise ValueError(f"Unsupported LLM provider: {provider_name}. Supported: {supported}")


def build_chat_model(
    *,
    provider_name: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    extra_body: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """
    通过 LiteLLM provider 创建 LangChain chat model。

    Args:
        provider_name: provider 名称；为空时使用配置中的固定 provider `litellm`。
        model: 模型别名。
        temperature: 采样温度。
        api_key: LiteLLM gateway key。
        base_url: LiteLLM OpenAI-compatible 地址。
        extra_body: 透传给 LiteLLM 的扩展参数。
        **kwargs: 透传给 provider。

    Returns:
        LangChain chat model 实例。
    """

    provider = get_llm_provider(provider_name)
    return provider.build_chat_model(
        model=model,
        temperature=temperature,
        api_key=api_key,
        base_url=base_url,
        extra_body=extra_body,
        **kwargs,
    )


def build_chat_completion_kwargs(
    *,
    provider_name: str | None = None,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    response_format: Mapping[str, Any] | None = None,
    extra_body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    通过 LiteLLM provider 构造 OpenAI SDK Chat Completions 参数。

    Args:
        provider_name: provider 名称；为空时使用配置中的固定 provider `litellm`。
        model: 模型别名。
        messages: OpenAI-compatible 消息列表。
        temperature: 采样温度。
        max_tokens: 最大输出 token 数。
        response_format: 响应格式。
        extra_body: 透传给 LiteLLM 的扩展参数。

    Returns:
        OpenAI SDK 请求参数。
    """

    provider = get_llm_provider(provider_name)
    return provider.build_chat_completion_kwargs(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        extra_body=extra_body,
    )


def _provider_plugins() -> tuple[LLMProviderPlugin, ...]:
    return (LiteLLMProvider(),)


def _normalize_provider_name(provider_name: str) -> str:
    return provider_name.strip().lower().replace("-", "_")
