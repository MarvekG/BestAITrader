from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from app.api.endpoints.testing import (
    MEMORY_TEST_QUERY,
    MEMORY_TEST_STOCK_CODE,
    MEMORY_TEST_USER_ID,
    list_testing_tools,
    test_pdf_tool as run_pdf_tool_endpoint,
    test_memory as run_memory_write_endpoint,
    test_memory_preview as run_memory_preview_endpoint,
    test_memory_read as run_memory_read_endpoint,
    test_memory_recall_audits as run_memory_recall_audits_endpoint,
    test_skills as run_skills_endpoint,
)
from app.ai.memory_client import memory_client


@pytest.mark.asyncio
async def test_testing_catalog_includes_skills_probe():
    result = await list_testing_tools()

    assert result["status"] == "success"
    fixed_tools = result["fixed_tools"]
    assert any(item["name"] == "skills" and item["test_route"] == "/testing/skills" for item in fixed_tools)
    assert any(item["name"] == "pdf_tool" and item["test_route"] == "/testing/pdf_tool" for item in fixed_tools)
    assert all(item["name"] != "llm" for item in fixed_tools)


@pytest.mark.asyncio
async def test_skills_testing_endpoint_checks_loader_and_script_probe():
    result = await run_skills_endpoint()

    assert result["status"] == "success"
    assert result["skill_count"] >= 1
    assert result["skill_id"]
    assert result["script_probe"]["status"] in {"success", "skipped"}


@pytest.mark.asyncio
async def test_pdf_tool_testing_endpoint_uses_word_engine():
    mock_pdf_tool = AsyncMock(
        return_value={
            "status": "success",
            "engine": "word",
            "markdown": "# Report\n\nRevenue.",
            "markdown_length": 128,
            "truncated": False,
        }
    )

    with patch("app.api.endpoints.testing.tools.parse_pdf_to_markdown", SimpleNamespace(ainvoke=mock_pdf_tool)):
        result = await run_pdf_tool_endpoint(url="https://example.com/report.pdf")

    assert result["status"] == "success"
    assert result["engine"] == "word"
    assert result["markdown_length"] == 128
    payload = mock_pdf_tool.await_args.args[0]
    assert payload["engine"] == "word"
    assert payload["url"] == "https://example.com/report.pdf"
    assert payload["max_chars"] == 40_000


@pytest.mark.asyncio
async def test_pdf_tool_testing_endpoint_requires_url():
    result = await run_pdf_tool_endpoint(url=" ")

    assert result["status"] == "error"
    assert "URL" in result["message"]


@pytest.mark.asyncio
async def test_memory_testing_endpoint_writes_probe_event():
    mock_write = AsyncMock(
        return_value={
            "data": {
                "memory_id": "mem-123",
                "session": f"user:{MEMORY_TEST_USER_ID}:stock:{MEMORY_TEST_STOCK_CODE}",
            },
            "error": None,
        }
    )

    with patch("app.api.endpoints.testing.memory_client.write_memory", mock_write), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_write_endpoint()

    assert result["status"] == "success"
    assert result["memory_id"] == "mem-123"
    assert result["data"]["session"] == f"user:{MEMORY_TEST_USER_ID}:stock:{MEMORY_TEST_STOCK_CODE}"
    assert "event_id" not in result
    assert "observation_id" not in result
    payload = mock_write.await_args.kwargs
    assert payload["user_id"] == MEMORY_TEST_USER_ID
    assert payload["stock_code"] == MEMORY_TEST_STOCK_CODE
    assert payload["content"]


@pytest.mark.asyncio
async def test_memory_testing_endpoint_accepts_memoflux_write_response():
    mock_write = AsyncMock(
        return_value={
            "data": {
                "memory_id": "mem-123",
                "session": f"user:{MEMORY_TEST_USER_ID}:stock:{MEMORY_TEST_STOCK_CODE}",
                "occurred_at": "2026-06-01T00:00:00Z",
                "created_at": "2026-06-01T00:00:00Z",
            },
            "error": None,
        }
    )

    with patch("app.api.endpoints.testing.memory_client.write_memory", mock_write), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_write_endpoint()

    assert result["status"] == "success"
    assert result["memory_id"] == "mem-123"
    assert "observation_id" not in result


@pytest.mark.asyncio
async def test_memory_testing_endpoint_returns_error_when_disabled():
    with patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=False):
        result = await run_memory_write_endpoint()

    assert result["status"] == "error"
    assert "Memory" in result["message"] or "记忆" in result["message"]


@pytest.mark.asyncio
async def test_memory_read_testing_endpoint_checks_recall_path():
    mock_recall = AsyncMock(
        return_value={
            "answer": "测试回答",
            "references": [
                {
                    "memory_id": "mem-1",
                    "quote": "测试证据",
                    "relevance": "直接相关",
                }
            ],
        }
    )

    with patch("app.api.endpoints.testing.memory_client.recall", mock_recall), \
         patch("app.api.endpoints.testing.memory_client.get_last_error", return_value=None), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_read_endpoint()

    assert result["status"] == "success"
    assert result["count"] == 1
    assert result["data"]["answer"] == "测试回答"
    assert result["data"]["references"][0]["memory_id"] == "mem-1"
    assert "items" not in result
    payload = mock_recall.await_args.kwargs
    assert payload["user_id"] == MEMORY_TEST_USER_ID
    assert payload["stock_code"] == MEMORY_TEST_STOCK_CODE
    assert payload["query"] == MEMORY_TEST_QUERY


@pytest.mark.asyncio
async def test_memory_preview_testing_endpoint_returns_preview_items():
    mock_preview = AsyncMock(
        return_value={
            "data": {
                "items": [
                    {
                        "memory_id": "mem-1",
                        "session": f"user:{MEMORY_TEST_USER_ID}:stock:{MEMORY_TEST_STOCK_CODE}",
                        "content": "Probe content",
                        "occurred_at": "2026-04-13T00:00:00+00:00",
                        "created_at": "2026-04-13T00:00:00+00:00",
                    }
                ],
                "next_cursor": None,
            },
            "error": None,
        }
    )

    with patch("app.api.endpoints.testing.memory_client.preview_memories", mock_preview), \
         patch("app.api.endpoints.testing.memory_client.get_last_error", return_value=None), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_preview_endpoint(
            user_id=MEMORY_TEST_USER_ID,
            stock_code=MEMORY_TEST_STOCK_CODE,
        )

    assert result["status"] == "success"
    assert result["total"] == 1
    assert result["data"]["items"][0]["memory_id"] == "mem-1"
    assert result["data"]["items"][0]["session"] == f"user:{MEMORY_TEST_USER_ID}:stock:{MEMORY_TEST_STOCK_CODE}"
    assert "items" not in result
    payload = mock_preview.await_args.kwargs
    assert payload["user_id"] == MEMORY_TEST_USER_ID
    assert payload["stock_code"] == MEMORY_TEST_STOCK_CODE


@pytest.mark.asyncio
async def test_memory_preview_testing_endpoint_defaults_to_probe_session():
    mock_preview = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})

    with patch("app.api.endpoints.testing.memory_client.preview_memories", mock_preview), \
         patch("app.api.endpoints.testing.memory_client.get_last_error", return_value=None), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_preview_endpoint(
            user_id=None,
            stock_code=None,
            status=None,
            limit=20,
            offset=0,
        )

    assert result["status"] == "success"
    payload = mock_preview.await_args.kwargs
    assert payload["user_id"] == MEMORY_TEST_USER_ID
    assert payload["stock_code"] == MEMORY_TEST_STOCK_CODE


@pytest.mark.asyncio
async def test_memory_recall_audits_testing_endpoint_defaults_to_probe_session():
    mock_audits = AsyncMock(return_value={"data": {"items": [], "next_cursor": None}, "error": None})

    with patch("app.api.endpoints.testing.memory_client.preview_recall_audits", mock_audits), \
         patch("app.api.endpoints.testing.memory_client.get_last_error", return_value=None), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_recall_audits_endpoint(
            user_id=None,
            stock_code=None,
            status=None,
            error_code=None,
            limit=20,
            offset=0,
        )

    assert result["status"] == "success"
    payload = mock_audits.await_args.kwargs
    assert payload["user_id"] == MEMORY_TEST_USER_ID
    assert payload["stock_code"] == MEMORY_TEST_STOCK_CODE


@pytest.mark.asyncio
async def test_memory_preview_testing_endpoint_returns_error_when_disabled():
    with patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=False):
        result = await run_memory_preview_endpoint(
            user_id=None,
            stock_code=None,
            status=None,
            limit=20,
            offset=0,
        )

    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_memory_read_testing_endpoint_surfaces_backend_error():
    with patch("app.api.endpoints.testing.memory_client.recall", AsyncMock(return_value={})), \
         patch("app.api.endpoints.testing.memory_client.get_last_error", return_value={"message": "memory backend timeout"}), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_read_endpoint()

    assert result["status"] == "error"
    assert "timeout" in result["message"]


@pytest.mark.asyncio
async def test_memory_preview_testing_endpoint_surfaces_backend_error():
    with patch("app.api.endpoints.testing.memory_client.preview_memories", AsyncMock(return_value={})), \
         patch("app.api.endpoints.testing.memory_client.get_last_error", return_value={"message": "memory backend timeout"}), \
         patch.object(type(memory_client), "enabled", new_callable=PropertyMock, return_value=True):
        result = await run_memory_preview_endpoint(
            user_id=None,
            stock_code=None,
            status=None,
            limit=20,
            offset=0,
        )

    assert result["status"] == "error"
    assert "timeout" in result["message"]
