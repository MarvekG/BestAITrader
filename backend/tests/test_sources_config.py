import pytest

from app.api.endpoints import sources


@pytest.mark.asyncio
async def test_tavily_config_test_uses_unsaved_request_config(monkeypatch):
    captured = {}

    def fail_if_saved_config_is_read():
        raise AssertionError("should not read saved config when request config is provided")

    async def fake_search_with_api_keys(api_keys, query, limit):
        captured["api_keys"] = api_keys
        captured["query"] = query
        captured["limit"] = limit
        return [{"title": "ok"}]

    monkeypatch.setattr(sources, "get_cached_data_source_config", fail_if_saved_config_is_read)
    monkeypatch.setattr(sources.tavily, "search_with_api_keys", fake_search_with_api_keys)

    result = await sources.test_tavily_config_key(
        sources.DataSourceConfigTestRequest(
            query="semiconductor",
            config=sources.DataSourceConfigUpdate(tavily_api_key=[" unsaved-key "]),
        )
    )

    assert result["status"] == "completed"
    assert captured == {"api_keys": ["unsaved-key"], "query": "semiconductor", "limit": 1}
    assert result["results"][0]["key"] == "...key"


@pytest.mark.asyncio
async def test_newsapi_config_test_uses_unsaved_request_config(monkeypatch):
    captured = {}

    def fail_if_saved_config_is_read():
        raise AssertionError("should not read saved config when request config is provided")

    async def fake_search_with_api_keys(api_keys, query, limit):
        captured["api_keys"] = api_keys
        captured["query"] = query
        captured["limit"] = limit
        return [{"title": "ok"}]

    monkeypatch.setattr(sources, "get_cached_data_source_config", fail_if_saved_config_is_read)
    monkeypatch.setattr(sources.newsapi, "search_with_api_keys", fake_search_with_api_keys)

    result = await sources.test_newsapi_config_key(
        sources.DataSourceConfigTestRequest(
            query="AI",
            config=sources.DataSourceConfigUpdate(news_api_key=[" unsaved-news-key "]),
        )
    )

    assert result["status"] == "completed"
    assert captured == {"api_keys": ["unsaved-news-key"], "query": "AI", "limit": 1}
    assert result["results"][0]["key"] == "...key"


@pytest.mark.asyncio
async def test_tushare_config_test_uses_unsaved_request_config(monkeypatch):
    captured = {}
    from tushare.pro.client import DataApi

    original_api_url = DataApi._DataApi__http_url

    class FakeProClient:
        def stock_basic(self, **kwargs):
            captured["stock_basic_kwargs"] = kwargs
            return [{"ts_code": "000001.SZ"}]

    async def fake_get_pro_client(token=None, api_url=None):
        captured["token"] = token
        captured["api_url"] = api_url
        DataApi._DataApi__http_url = api_url
        return FakeProClient()

    monkeypatch.setattr(sources.TushareIngestor, "get_pro_client", staticmethod(fake_get_pro_client))

    result = await sources.test_tushare_config_key(
        sources.DataSourceConfigTestRequest(
            config=sources.DataSourceConfigUpdate(
                tushare_token="unsaved-token",
                tushare_api_url="https://example.invalid/tushare",
            ),
        )
    )

    assert result == {"status": "success", "data": [{"ts_code": "000001.SZ"}]}
    assert captured == {
        "token": "unsaved-token",
        "api_url": "https://example.invalid/tushare",
        "stock_basic_kwargs": {
            "ts_code": "000001.SZ",
            "fields": "ts_code,symbol,name,area,industry,list_date",
        },
    }
    assert DataApi._DataApi__http_url == original_api_url
