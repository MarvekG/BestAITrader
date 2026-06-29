from unittest.mock import patch

import pytest

from app.ai.agentic.tooling.news_plugins import get_available_news_sources, invoke_news_plugin
from app.ai.agentic.tooling.news_plugins import tavily


def test_tavily_source_is_registered():
    assert "tavily" in get_available_news_sources()


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json):
        return _FakeResponse(
            {
                "results": [
                    {
                        "title": "AI search result",
                        "content": "summary",
                        "url": "https://example.com/ai",
                        "score": 0.95,
                    }
                ]
            }
        )


@pytest.mark.asyncio
async def test_search_returns_normalized_results(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.tavily.settings.TAVILY_API_KEY", "test-key")

    with patch("app.ai.agentic.tooling.news_plugins.tavily.httpx.AsyncClient", _FakeAsyncClient):
        results = await tavily.search("AI", limit=1)

    assert results == [
        {
            "title": "AI search result",
            "content": "summary",
            "url": "https://example.com/ai",
            "score": 0.95,
            "source": "tavily",
        }
    ]


@pytest.mark.asyncio
async def test_invoke_news_plugin_uses_tavily_source(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.tavily.settings.TAVILY_API_KEY", "test-key")

    with patch("app.ai.agentic.tooling.news_plugins.tavily.httpx.AsyncClient", _FakeAsyncClient):
        results = await invoke_news_plugin(source="tavily", keyword="AI", limit=1)

    assert results[0]["source"] == "tavily"
    assert results[0]["title"] == "AI search result"


@pytest.mark.asyncio
async def test_search_tries_next_key_when_response_is_not_200(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.tavily.settings.TAVILY_API_KEY", "bad-key, good-key")
    used_keys = []

    class _FailoverClient(_FakeAsyncClient):
        async def post(self, url, json):
            used_keys.append(json["api_key"])
            if json["api_key"] == "bad-key":
                return _FakeResponse({"results": []}, status_code=429)
            return _FakeResponse({"results": []})

    with patch("app.ai.agentic.tooling.news_plugins.tavily.httpx.AsyncClient", _FailoverClient):
        results = await tavily.search("AI", limit=1)

    assert results == []
    assert used_keys == ["bad-key", "good-key"]


@pytest.mark.asyncio
async def test_search_returns_empty_when_api_key_missing(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.tavily.settings.TAVILY_API_KEY", "")

    results = await tavily.search("AI", limit=1)

    assert results == [{"error": "TAVILY_API_KEY is not configured", "source": "tavily", "fatal": True}]


@pytest.mark.asyncio
async def test_search_returns_fatal_error_when_all_keys_fail(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.tavily.settings.TAVILY_API_KEY", "expired-key")

    class _ExpiredKeyClient(_FakeAsyncClient):
        async def post(self, url, json):
            return _FakeResponse({"error": "invalid api key"}, status_code=401)

    with patch("app.ai.agentic.tooling.news_plugins.tavily.httpx.AsyncClient", _ExpiredKeyClient):
        results = await tavily.search("AI", limit=1)

    assert results[0]["fatal"] is True
    assert results[0]["source"] == "tavily"
    assert "HTTP 401" in results[0]["error"]
