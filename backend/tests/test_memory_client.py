from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai.memory_client import MemoryServiceClient


def test_memory_service_defaults_to_memo_base_url():
    from app.core.config import Settings

    fresh_settings = Settings()

    assert fresh_settings.MEMORY_SERVICE_BASE_URL == "http://memo:8020"


@pytest.mark.asyncio
async def test_write_memory_posts_minimal_generic_payload(monkeypatch):
    client = MemoryServiceClient()
    mock_post = AsyncMock(return_value={"observation_id": "obs_generic", "status": "pending"})
    monkeypatch.setattr(client, "_post", mock_post)

    await client.write_memory(
        user_id=7,
        stock_code="workspace:atlas",
        content="Atlas rollout retrospective record.",
    )

    assert mock_post.await_args.args[0] == "/v1/ingest"
    payload = mock_post.await_args.args[1]
    assert payload["session"] == "user:7:stock:workspace:atlas"
    assert payload["content"] == "Atlas rollout retrospective record."
    assert isinstance(payload["occurred_at"], str)
    assert payload["occurred_at"].endswith("Z")


@pytest.mark.asyncio
async def test_recall_returns_memoflux_data_with_session(monkeypatch):
    client = MemoryServiceClient()
    mock_post = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "answer": "Atlas rollout 历史记录",
                "references": [
                    {
                        "memory_id": "mem_1",
                        "content": "一条证据",
                        "occurred_at": "2026-05-01T00:00:00Z",
                        "relevance": "一条证据",
                    }
                ],
                "uncertainties": [],
            },
        }
    )
    monkeypatch.setattr(client, "_post", mock_post)

    result = await client.recall(
        user_id=7,
        stock_code="workspace:atlas",
        query="Atlas rollout 最新历史记录是什么？",
    )

    mock_post.assert_awaited_once_with(
        "/v1/recall",
        {"session": "user:7:stock:workspace:atlas", "query": "Atlas rollout 最新历史记录是什么？"},
        timeout_seconds=90.0,
        operation="recall",
    )
    assert result == {
        "answer": "Atlas rollout 历史记录",
        "references": [
            {
                "memory_id": "mem_1",
                "content": "一条证据",
                "occurred_at": "2026-05-01T00:00:00Z",
                "relevance": "一条证据",
            }
        ],
        "uncertainties": [],
        "session": "user:7:stock:workspace:atlas",
        "stock_code": "workspace:atlas",
    }


@pytest.mark.asyncio
async def test_recall_returns_memoflux_response_without_legacy_items(monkeypatch):
    client = MemoryServiceClient()
    mock_post = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "query_id": "q1",
                "answer": "历史经验显示应等待确认。",
                "confidence": 0.82,
                "references": [
                    {
                        "memory_id": "m1",
                        "content": "贵州茅台(600519.SH) 回撤时要等量能确认。",
                        "occurred_at": "2026-05-01T00:00:00Z",
                        "relevance": "直接支持等待确认。",
                    }
                ],
                "audit": {
                    "selected_memory_ids": ["m1"],
                    "answerability": "answerable",
                    "answerability_reason": "有直接记忆支持。",
                },
            },
        }
    )
    monkeypatch.setattr(client, "_post", mock_post)

    result = await client.recall(user_id=7, stock_code="600519.SH", query="risk control lesson")

    assert result["query_id"] == "q1"
    assert result["answer"] == "历史经验显示应等待确认。"
    assert result["confidence"] == 0.82
    assert result["references"][0]["memory_id"] == "m1"
    assert result["audit"]["answerability"] == "answerable"
    assert result["session"] == "user:7:stock:600519.SH"
    assert "items" not in result
    assert "metadata" not in result
    assert "citations" not in result


@pytest.mark.asyncio
async def test_recall_uses_extended_timeout(monkeypatch):
    client = MemoryServiceClient()
    mock_post = AsyncMock(return_value={"data": {"answer": "", "references": []}, "error": None})
    monkeypatch.setattr(client, "_post", mock_post)

    await client.recall(
        user_id=7,
        stock_code="workspace:atlas",
        query="Atlas rollout 最新历史记录是什么？",
    )

    assert mock_post.await_args.kwargs["timeout_seconds"] >= 30.0


def test_record_error_uses_exception_type_when_message_is_empty():
    client = MemoryServiceClient()

    client._record_error("recall", "/memory/recall", httpx.ReadTimeout(""))

    assert client.get_last_error("recall") == {
        "operation": "recall",
        "path": "/memory/recall",
        "message": "ReadTimeout",
        "error_type": "ReadTimeout",
    }


@pytest.mark.asyncio
async def test_recall_ignores_legacy_top_level_response(monkeypatch):
    client = MemoryServiceClient()
    mock_post = AsyncMock(
        return_value={
            "status": "ok",
            "answer": "旧版 recall 响应",
            "key_memory_ids": ["mem_legacy"],
            "supporting_observation_ids": ["obs_legacy"],
            "citations": [{"observation_id": "obs_legacy", "source_memory_ids": ["mem_legacy"]}],
            "uncertainties": [],
        }
    )
    monkeypatch.setattr(client, "_post", mock_post)

    result = await client.recall(
        user_id=7,
        stock_code="workspace:atlas",
        query="Atlas rollout 最新历史记录是什么？",
    )

    assert result == {}


@pytest.mark.asyncio
async def test_write_memory_uses_general_scope_sentinel_when_stock_code_missing(monkeypatch):
    client = MemoryServiceClient()
    mock_post = AsyncMock(return_value={"observation_id": "obs_general", "status": "pending"})
    monkeypatch.setattr(client, "_post", mock_post)

    await client.write_memory(
        user_id=7,
        stock_code=None,
        content="通用纪律：先确认证据质量，再决定是否下结论。",
    )

    payload = mock_post.await_args.args[1]
    assert payload["session"] == "user:7:general"
    assert payload["content"] == "通用纪律：先确认证据质量，再决定是否下结论。"
    assert isinstance(payload["occurred_at"], str)
    assert payload["occurred_at"].endswith("Z")


@pytest.mark.asyncio
async def test_recall_uses_general_scope_sentinel_when_stock_code_missing(monkeypatch):
    client = MemoryServiceClient()
    mock_post = AsyncMock(
        return_value={
            "success": True,
            "data": {
                "answer": "通用复盘教训",
                "references": [
                    {
                        "memory_id": "mem_1",
                        "content": "一条证据",
                        "occurred_at": "2026-05-01T00:00:00Z",
                        "relevance": "一条证据",
                    }
                ],
                "uncertainties": [],
            },
        }
    )
    monkeypatch.setattr(client, "_post", mock_post)

    result = await client.recall(
        user_id=7,
        stock_code=None,
        query="通用复盘教训",
    )

    payload = mock_post.await_args.args[1]
    assert payload["session"] == "user:7:general"
    assert result["session"] == "user:7:general"
    assert result["stock_code"] is None
    assert result["answer"] == "通用复盘教训"
    assert result["references"][0]["memory_id"] == "mem_1"


@pytest.mark.asyncio
async def test_preview_memories_uses_stock_session_when_user_and_stock_are_present(monkeypatch):
    client = MemoryServiceClient()
    mock_get = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})
    monkeypatch.setattr(client, "_get", mock_get)

    result = await client.preview_memories(
        user_id=7,
        stock_code="000001.SZ",
        status="active",
        limit=50,
        offset=10,
    )

    assert result["data"]["items"] == []
    assert mock_get.await_args.args[0] == "/v1/preview"
    params = mock_get.await_args.kwargs["params"]
    assert params["session"] == "user:7:stock:000001.SZ"
    assert "status" not in params
    assert params["limit"] == 50
    assert params["offset"] == 10


@pytest.mark.asyncio
async def test_preview_memories_uses_user_session_prefix_when_stock_is_missing(monkeypatch):
    client = MemoryServiceClient()
    mock_get = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})
    monkeypatch.setattr(client, "_get", mock_get)

    await client.preview_memories(user_id=7, stock_code=None)

    params = mock_get.await_args.kwargs["params"]
    assert params["session"] == "user:7:general"


@pytest.mark.asyncio
async def test_preview_memories_omits_session_when_user_is_missing(monkeypatch):
    client = MemoryServiceClient()
    mock_get = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})
    monkeypatch.setattr(client, "_get", mock_get)

    await client.preview_memories(user_id=None, stock_code=None, limit=30, offset=5)

    params = mock_get.await_args.kwargs["params"]
    assert params == {"limit": 30, "offset": 5}


@pytest.mark.asyncio
async def test_preview_memories_passes_offset_to_memoflux(monkeypatch):
    client = MemoryServiceClient()
    mock_get = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})
    monkeypatch.setattr(client, "_get", mock_get)

    await client.preview_memories(user_id=7, stock_code="000001.SZ", limit=25, offset=50)

    params = mock_get.await_args.kwargs["params"]
    assert params["limit"] == 25
    assert params["offset"] == 50


@pytest.mark.asyncio
async def test_preview_recall_audits_uses_memoflux_audits_endpoint(monkeypatch):
    client = MemoryServiceClient()
    mock_get = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})
    monkeypatch.setattr(client, "_get", mock_get)

    result = await client.preview_recall_audits(
        user_id=7,
        stock_code="000001.SZ",
        status="ok",
        error_code="ignored",
        limit=50,
        offset=10,
    )

    assert result["data"]["items"] == []
    assert mock_get.await_args.args[0] == "/v1/audits"
    params = mock_get.await_args.kwargs["params"]
    assert params == {"session": "user:7:stock:000001.SZ", "limit": 50, "offset": 10}


@pytest.mark.asyncio
async def test_preview_recall_audits_omits_session_when_user_is_missing(monkeypatch):
    client = MemoryServiceClient()
    mock_get = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})
    monkeypatch.setattr(client, "_get", mock_get)

    await client.preview_recall_audits(user_id=None, stock_code=None, limit=30, offset=5)

    params = mock_get.await_args.kwargs["params"]
    assert params == {"limit": 30, "offset": 5}


@pytest.mark.asyncio
async def test_memory_observability_uses_memoflux_endpoints(monkeypatch):
    client = MemoryServiceClient()
    usage_stats = {"status": "ok", "total_calls": 2, "input_tokens": 30}
    clear_result = {"status": "ok", "deleted": 1}
    mock_get = AsyncMock(return_value={"data": usage_stats, "error": None})
    mock_delete = AsyncMock(return_value={"data": clear_result, "error": None})
    monkeypatch.setattr(client, "_get", mock_get)
    monkeypatch.setattr(client, "_delete", mock_delete)

    await client.check_embedding_health()
    stats_result = await client.get_usage_stats(hours=24)
    clear_result_response = await client.clear_usage_stats()

    assert mock_get.await_args_list[0].args[0] == "/v1/health"
    assert mock_get.await_args_list[1].args[0] == "/v1/usage/stats"
    assert "params" not in mock_get.await_args_list[1].kwargs
    assert mock_delete.await_args.args[0] == "/v1/usage/stats"
    assert stats_result == usage_stats
    assert clear_result_response == clear_result


@pytest.mark.asyncio
async def test_memory_client_reuses_async_client_between_requests(monkeypatch):
    client = MemoryServiceClient()
    created_clients = []

    class _ReusableAsyncClient:
        def __init__(self, *args, **kwargs):
            created_clients.append(self)

        async def post(self, url, json, headers=None, timeout=None):
            del url, json, headers, timeout
            return httpx.Response(
                200,
                request=httpx.Request("POST", "http://memoflux/v1/recall"),
                json={"data": {"answer": "ok", "references": []}},
            )

    monkeypatch.setattr("app.ai.memory_client.httpx.AsyncClient", _ReusableAsyncClient)
    with patch("app.ai.memory_client.settings.MEMORY_SERVICE_ENABLED", True), \
         patch("app.ai.memory_client.settings.MEMORY_SERVICE_BASE_URL", "http://memo"):
        await client.recall(user_id=7, stock_code="000001.SZ", query="first query")
        await client.recall(user_id=7, stock_code="000001.SZ", query="second query")

    assert len(created_clients) == 1


@pytest.mark.asyncio
async def test_memory_client_passes_per_request_timeout_to_reused_client(monkeypatch):
    client = MemoryServiceClient()
    request_timeouts = []

    class _TimeoutRecordingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def post(self, url, json, headers=None, timeout=None):
            del url, json, headers
            request_timeouts.append(timeout)
            return httpx.Response(
                200,
                request=httpx.Request("POST", "http://memoflux/v1/recall"),
                json={"data": {"answer": "ok", "references": []}},
            )

    monkeypatch.setattr("app.ai.memory_client.httpx.AsyncClient", _TimeoutRecordingAsyncClient)
    with patch("app.ai.memory_client.settings.MEMORY_SERVICE_ENABLED", True), \
         patch("app.ai.memory_client.settings.MEMORY_SERVICE_BASE_URL", "http://memo"), \
         patch("app.ai.memory_client.settings.MEMORY_SERVICE_TIMEOUT_SECONDS", 5.0):
        await client.write_memory(user_id=7, stock_code="000001.SZ", content="risk lesson")
        await client.recall(user_id=7, stock_code="000001.SZ", query="risk lesson")

    assert request_timeouts == [5.0, 30.0]


@pytest.mark.asyncio
async def test_memory_client_close_resets_reused_async_client(monkeypatch):
    client = MemoryServiceClient()
    closed_clients = []

    class _ClosableAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def aclose(self):
            closed_clients.append(self)

    monkeypatch.setattr("app.ai.memory_client.httpx.AsyncClient", _ClosableAsyncClient)

    created_client = client._get_client()
    await client.close()

    assert closed_clients == [created_client]
    assert client._client is None


@pytest.mark.asyncio
async def test_memory_client_close_resets_client_without_aclose(monkeypatch):
    client = MemoryServiceClient()

    class _NonClosableAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

    monkeypatch.setattr("app.ai.memory_client.httpx.AsyncClient", _NonClosableAsyncClient)

    client._get_client()
    await client.close()

    assert client._client is None


def test_record_error_uses_stable_http_status_message():
    client = MemoryServiceClient()
    request = httpx.Request("POST", "http://memoflux/v1/recall")
    response = httpx.Response(503, request=request, json={"detail": "backend warming up"})

    client._record_error(
        "recall",
        "/v1/recall",
        httpx.HTTPStatusError("raw upstream text", request=request, response=response),
    )

    assert client.get_last_error("recall") == {
        "operation": "recall",
        "path": "/v1/recall",
        "message": "Memory service HTTP 503: backend warming up",
        "error_type": "HTTPStatusError",
        "status_code": 503,
    }
