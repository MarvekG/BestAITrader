from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.endpoints.testing import (
    _run_news_source_test,
    list_testing_tools,
    test_pdf_tool as run_pdf_tool_endpoint,
    test_query_calc as run_query_calc_endpoint,
    test_skills as run_skills_endpoint,
)


@pytest.mark.asyncio
async def test_testing_catalog_includes_skills_probe():
    result = await list_testing_tools()

    assert result["status"] == "success"
    fixed_tools = result["fixed_tools"]
    assert any(item["name"] == "skills" and item["test_route"] == "/testing/skills" for item in fixed_tools)
    assert any(item["name"] == "pdf_tool" and item["test_route"] == "/testing/pdf_tool" for item in fixed_tools)
    assert all(item["name"] != "tavily" for item in fixed_tools)
    assert all(item["name"] != "llm" for item in fixed_tools)
    assert all(not item["name"].startswith("memory") for item in fixed_tools)


@pytest.mark.asyncio
async def test_news_source_test_returns_plugin_fatal_error():
    mock_search_news = AsyncMock(
        return_value=[{"error": "Tavily request failed with HTTP 401", "source": "tavily", "fatal": True}]
    )

    with patch("app.api.endpoints.testing.tools.search_news", SimpleNamespace(ainvoke=mock_search_news)):
        result = await _run_news_source_test(
            "Tavily 通用新闻搜索",
            "tavily",
            ["AI"],
            3,
            "Tavily 通用新闻搜索",
        )

    assert result["status"] == "error"
    assert result["fatal"] is True
    assert result["keyword"] == "AI"
    assert "HTTP 401" in result["message"]


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
async def test_query_calc_testing_endpoint_checks_sandbox_stdout_result():
    mock_fetch_stock_info = AsyncMock(return_value=True)
    mock_query_calc = AsyncMock(return_value={"success": True, "stdout": '{"result": 1}\n', "stderr": ""})

    with patch("app.api.endpoints.testing.ingestor_manager.fetch_and_ingest_stock_info", mock_fetch_stock_info), \
         patch("app.api.endpoints.testing.tools.query_and_calculate", SimpleNamespace(ainvoke=mock_query_calc)):
        result = await run_query_calc_endpoint()

    assert result["status"] == "success"
    payload = mock_query_calc.await_args.args[0]
    assert "print(json.dumps" in payload["compute_code"]


@pytest.mark.asyncio
async def test_query_calc_testing_endpoint_rejects_assignment_only_result():
    with patch(
        "app.api.endpoints.testing.ingestor_manager.fetch_and_ingest_stock_info",
        AsyncMock(return_value=True),
    ), \
         patch(
             "app.api.endpoints.testing.tools.query_and_calculate",
             SimpleNamespace(ainvoke=AsyncMock(return_value={"success": True, "stdout": "", "stderr": ""})),
         ):
        result = await run_query_calc_endpoint()

    assert result["status"] == "error"
