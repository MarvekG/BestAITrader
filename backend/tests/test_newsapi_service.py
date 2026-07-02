from unittest.mock import AsyncMock, patch

import pytest

from app.ai.agentic.tooling.news_plugins import get_available_news_sources, invoke_news_plugin
from app.ai.agentic.tooling.news_plugins import newsapi


def test_newsapi_source_is_registered():
    assert "newsapi" in get_available_news_sources()


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

    async def get(self, url, params=None):
        return _FakeResponse(
            {
                "status": "ok",
                "articles": [
                    {
                        "title": "AI breakthrough news",
                        "description": "Summary of AI news",
                        "url": "https://example.com/ai-news",
                        "publishedAt": "2025-01-01T00:00:00Z",
                        "source": {"name": "Test News"},
                        "content": "AI content here...",
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_search_returns_normalized_results(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.newsapi.get_data_source_config_list", AsyncMock(return_value=["test-key"]))

    with patch("app.ai.agentic.tooling.news_plugins.newsapi.httpx.AsyncClient", _FakeAsyncClient):
        results = await newsapi.search("AI", limit=1, from_date="2026-05-01", to_date="2026-05-09")

    assert len(results) == 1
    assert results[0]["title"] == "AI breakthrough news"
    assert results[0]["content"] == "Summary of AI news"
    assert results[0]["url"] == "https://example.com/ai-news"
    assert results[0]["published_at"] == "2025-01-01T00:00:00Z"
    assert results[0]["publisher"] == "Test News"
    assert results[0]["source"] == "newsapi"


@pytest.mark.asyncio
async def test_search_passes_date_params_to_request(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.newsapi.get_data_source_config_list", AsyncMock(return_value=["test-key"]))
    captured_params = {}

    class _CapturingClient(_FakeAsyncClient):
        async def get(self, url, params=None):
            captured_params["url"] = url
            captured_params["params"] = dict(params) if params else {}
            return _FakeResponse({"status": "ok", "articles": []})

    with patch("app.ai.agentic.tooling.news_plugins.newsapi.httpx.AsyncClient", _CapturingClient):
        await newsapi.search("test", limit=5, from_date="2026-05-01", to_date="2026-05-09")

    assert captured_params["params"].get("from") == "2026-05-01"
    assert captured_params["params"].get("to") == "2026-05-09"
    assert captured_params["params"].get("q") == "test"
    assert captured_params["params"].get("pageSize") == 5


@pytest.mark.asyncio
async def test_search_tries_next_key_when_response_is_not_200(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.newsapi.get_data_source_config_list", AsyncMock(return_value=["bad-key", "good-key"]))
    used_keys = []

    class _FailoverClient(_FakeAsyncClient):
        async def get(self, url, params=None):
            used_keys.append(params["apiKey"])
            if params["apiKey"] == "bad-key":
                return _FakeResponse({"status": "error"}, status_code=401)
            return _FakeResponse({"status": "ok", "articles": []})

    with patch("app.ai.agentic.tooling.news_plugins.newsapi.httpx.AsyncClient", _FailoverClient):
        results = await newsapi.search("test", limit=1, from_date="2026-05-01", to_date="2026-05-09")

    assert results == []
    assert used_keys == ["bad-key", "good-key"]


@pytest.mark.asyncio
async def test_invoke_news_plugin_uses_newsapi_source(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.newsapi.get_data_source_config_list", AsyncMock(return_value=["test-key"]))

    with patch("app.ai.agentic.tooling.news_plugins.newsapi.httpx.AsyncClient", _FakeAsyncClient):
        results = await invoke_news_plugin(
            source="newsapi", keyword="AI", limit=1, from_date="2026-05-01", to_date="2026-05-09"
        )

    assert results[0]["source"] == "newsapi"
    assert results[0]["title"] == "AI breakthrough news"


@pytest.mark.asyncio
async def test_search_returns_empty_when_api_key_missing(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.newsapi.get_data_source_config_list", AsyncMock(return_value=[]))

    results = await newsapi.search("AI", limit=1, from_date="2026-05-01", to_date="2026-05-09")

    assert results == [{"error": "NEWS_API_KEY is not configured", "source": "newsapi", "fatal": True}]


@pytest.mark.asyncio
async def test_search_returns_fatal_error_when_all_keys_fail(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.newsapi.get_data_source_config_list", AsyncMock(return_value=["expired-key"]))

    class _ExpiredKeyClient(_FakeAsyncClient):
        async def get(self, url, params=None):
            return _FakeResponse({"status": "error", "message": "invalid api key"}, status_code=401)

    with patch("app.ai.agentic.tooling.news_plugins.newsapi.httpx.AsyncClient", _ExpiredKeyClient):
        results = await newsapi.search("AI", limit=1, from_date="2026-05-01", to_date="2026-05-09")

    assert results[0]["fatal"] is True
    assert results[0]["source"] == "newsapi"
    assert "HTTP 401" in results[0]["error"]


@pytest.mark.asyncio
async def test_search_skips_removed_articles(monkeypatch):
    monkeypatch.setattr("app.ai.agentic.tooling.news_plugins.newsapi.get_data_source_config_list", AsyncMock(return_value=["test-key"]))

    class _FakeClientWithRemoved(_FakeAsyncClient):
        async def get(self, url, params=None):
            return _FakeResponse(
                {
                    "status": "ok",
                    "articles": [
                        {
                            "title": "[Removed]",
                            "description": "",
                            "url": "",
                            "publishedAt": "",
                            "source": {"name": ""},
                        },
                        {
                            "title": "Valid article",
                            "description": "Valid summary",
                            "url": "https://example.com/valid",
                            "publishedAt": "2025-01-01T00:00:00Z",
                            "source": {"name": "Valid Source"},
                        },
                    ],
                }
            )

    with patch("app.ai.agentic.tooling.news_plugins.newsapi.httpx.AsyncClient", _FakeClientWithRemoved):
        results = await newsapi.search("test", limit=2, from_date="2026-05-01", to_date="2026-05-09")

    assert len(results) == 1
    assert results[0]["title"] == "Valid article"
