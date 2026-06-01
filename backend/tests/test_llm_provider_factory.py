from app.ai.llm_providers.factory import get_llm_provider
from app.ai.llm_providers.litellm import LiteLLMProvider


def test_get_llm_provider_returns_litellm_provider_by_default() -> None:
    assert isinstance(get_llm_provider(), LiteLLMProvider)


def test_get_llm_provider_accepts_litellm_name() -> None:
    assert isinstance(get_llm_provider("litellm"), LiteLLMProvider)


def test_get_llm_provider_uses_configured_default(monkeypatch) -> None:
    monkeypatch.setattr("app.ai.llm_providers.factory.settings.LLM_PROVIDER", "litellm")

    assert isinstance(get_llm_provider(), LiteLLMProvider)


def test_get_llm_provider_rejects_deepseek() -> None:
    try:
        get_llm_provider("deepseek")
    except ValueError as exc:
        assert "Unsupported LLM provider: deepseek" in str(exc)
    else:
        raise AssertionError("deepseek provider should not be supported")
