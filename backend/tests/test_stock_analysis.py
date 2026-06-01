from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError
from app.ai.stock_analysis.schemas import (
    MAX_STOCK_ANALYSIS_QUESTION_LENGTH,
    StockAnalysisRequest,
)
from app.models.data_storage import StockBasic


def test_stock_analysis_request_requires_question() -> None:
    with pytest.raises(ValidationError):
        StockAnalysisRequest(stock_code="600519")


def test_stock_analysis_request_rejects_blank_question() -> None:
    with pytest.raises(ValidationError):
        StockAnalysisRequest(stock_code="600519", question="   ")


def test_stock_analysis_request_allows_missing_stock_code() -> None:
    request = StockAnalysisRequest(question="分析半导体行业机会")

    assert request.stock_code is None
    assert request.question == "分析半导体行业机会"


def test_stock_analysis_request_normalizes_blank_stock_code_to_none() -> None:
    request = StockAnalysisRequest(stock_code="   ", question="推荐几只值得关注的新股")

    assert request.stock_code is None


def test_stock_analysis_request_rejects_question_over_100000_chars() -> None:
    oversized_question = "a" * (MAX_STOCK_ANALYSIS_QUESTION_LENGTH + 1)

    with pytest.raises(ValidationError):
        StockAnalysisRequest(stock_code="600519.SH", question=oversized_question)


def test_stock_analysis_run_rejects_blank_question(client, auth_headers, db_session) -> None:
    db_session.add(StockBasic(stock_code="600519.SH", name="贵州茅台", market="SH"))
    db_session.commit()

    response = client.post(
        "/api/v1/stock-analysis/run",
        headers=auth_headers,
        json={"stock_code": "600519", "question": "   "},
    )

    assert response.status_code == 422


def test_stock_analysis_run_submits_owned_task(client, auth_headers, db_session) -> None:
    db_session.add(StockBasic(stock_code="600519.SH", name="贵州茅台", market="SH"))
    db_session.commit()

    submitted = {}

    def fake_submit_task(**kwargs):
        submitted.update(kwargs)
        return {
            "task_id": "task-stock-1",
            "task_name": "AI Research Analysis - 600519.SH",
            "status": "pending",
            "message": "submitted",
            "new_task": True,
        }

    with (
        patch("app.ai.stock_analysis.service.task_manager.submit_task", side_effect=fake_submit_task),
        patch("app.ai.stock_analysis.service.async_task_runner.submit_task", return_value=True) as submit_runner,
    ):
        response = client.post(
            "/api/v1/stock-analysis/run",
            headers=auth_headers,
            json={"stock_code": "600519", "question": "分析贵州茅台"},
        )

    assert response.status_code == 201
    assert response.json()["task_id"] == "task-stock-1"
    assert submitted["task_type"] == "stock_analysis"
    assert submitted["task_name"] == "AI Research Analysis - 600519.SH"
    assert submitted["parameters"] == {
        "stock_code": "600519.SH",
        "stock_name": "贵州茅台",
        "question": "分析贵州茅台",
    }
    assert submitted["user_id"] is not None
    assert submit_runner.called is True


def test_research_analysis_run_submits_task_without_stock(client, auth_headers) -> None:
    submitted = {}

    def fake_submit_task(**kwargs):
        submitted.update(kwargs)
        return {
            "task_id": "task-research-1",
            "task_name": "AI Research Analysis",
            "status": "pending",
            "message": "submitted",
            "new_task": True,
        }

    with (
        patch("app.ai.stock_analysis.service.task_manager.submit_task", side_effect=fake_submit_task),
        patch("app.ai.stock_analysis.service.async_task_runner.submit_task", return_value=True) as submit_runner,
    ):
        response = client.post(
            "/api/v1/stock-analysis/run",
            headers=auth_headers,
            json={"question": "分析半导体行业最近有没有机会"},
        )

    assert response.status_code == 201
    assert response.json()["task_id"] == "task-research-1"
    assert submitted["task_name"] == "AI Research Analysis"
    assert submitted["parameters"] == {
        "stock_code": None,
        "stock_name": None,
        "question": "分析半导体行业最近有没有机会",
    }
    assert submit_runner.call_args.kwargs["task_kwargs"]["stock_code"] is None
    assert submit_runner.call_args.kwargs["task_kwargs"]["stock_name"] is None


def test_stock_analysis_run_returns_404_for_unknown_stock(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/stock-analysis/run",
        headers=auth_headers,
        json={"stock_code": "600519.SH", "question": "分析一下"},
    )

    assert response.status_code == 404
    assert "Stock 600519.SH not found" in response.json()["detail"]


class FakeStockAnalysisLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.bound_tool_names = []
        self.messages_seen = []

    def bind_tools(self, tools):
        bound = FakeStockAnalysisLLM([])
        bound.responses = self.responses
        bound.bound_tool_names = [tool.name for tool in tools]
        return bound

    async def ainvoke(self, messages):
        self.messages_seen.append(messages)
        if not self.responses:
            return AIMessage(
                content=(
                    "# 最终报告\n\n## 结论\n不确定\n\n## 关键证据\n无\n\n"
                    "## 主要风险\n无\n\n## 后续观察点\n无\n\n## 数据缺口\n无"
                )
            )
        return self.responses.pop(0)


class FakeTool:
    name = "fake_stock_tool"

    async def ainvoke(self, args):
        return {"ok": True, "args": args}


def test_stock_analysis_tool_boundary_excludes_memory_and_trade_tools() -> None:
    from app.ai.agentic.tools import get_stock_analysis_tools
    from app.ai.stock_analysis.runner import build_stock_analysis_tools

    tool_names = {tool.name for tool in build_stock_analysis_tools()}
    base_tool_names = {tool.name for tool in get_stock_analysis_tools()}

    assert base_tool_names == {
        "execute_python_sandboxed",
        "browse_web_page_html",
        "parse_pdf_to_markdown",
        "search_news",
    }
    assert "list_skills" in tool_names
    assert "execute_python_sandboxed" in tool_names
    assert "browse_web_page_html" in tool_names
    assert "parse_pdf_to_markdown" in tool_names
    assert "search_news" in tool_names
    assert "query_stock_data" not in tool_names
    assert "query_market_data" not in tool_names
    assert "sync_market_data" not in tool_names
    assert "get_database_schema" not in tool_names
    assert "query_and_calculate" not in tool_names
    assert "recall_memory" not in tool_names
    assert "write_memory" not in tool_names
    assert "execute_trading_order" not in tool_names


@pytest.mark.asyncio
async def test_stock_analysis_runner_executes_tool_and_returns_markdown(monkeypatch) -> None:
    from app.ai.stock_analysis import runner

    fake_llm = FakeStockAnalysisLLM([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "fake_stock_tool",
                    "args": {"value": "ok"},
                    "id": "tool-1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content=(
                "# 最终报告\n\n## 结论\n偏上涨\n\n## 关键证据\n工具已执行\n\n"
                "## 主要风险\n回撤\n\n## 后续观察点\n成交量\n\n## 数据缺口\n无"
            )
        ),
    ])
    monkeypatch.setattr(runner, "build_chat_model", lambda **kwargs: fake_llm)
    monkeypatch.setattr(runner, "build_stock_analysis_tools", lambda: [FakeTool()])
    monkeypatch.setattr(runner, "record_llm_usage", lambda *args, **kwargs: None)

    result = await runner.run_single_stock_analysis(
        stock_code="600519.SH",
        stock_name="贵州茅台",
        question="分析这只股票会涨还是会跌",
    )

    assert "偏上涨" in result["answer_markdown"]
    assert result["tool_trace"][0]["name"] == "fake_stock_tool"
    assert result["tool_trace"][0]["success"] is True
    assert "stock_code" not in result
    assert "stock_name" not in result
    assert "analysis_target" not in result


@pytest.mark.asyncio
async def test_research_runner_supports_question_without_stock(monkeypatch) -> None:
    from app.ai.stock_analysis import runner

    fake_llm = FakeStockAnalysisLLM([
        AIMessage(content="# 行业分析\n\n## 结论\n关注半导体设备")
    ])
    monkeypatch.setattr(runner, "build_chat_model", lambda **kwargs: fake_llm)
    monkeypatch.setattr(runner, "build_stock_analysis_tools", lambda: [FakeTool()])
    monkeypatch.setattr(runner, "record_llm_usage", lambda *args, **kwargs: None)

    result = await runner.run_single_stock_analysis(
        stock_code=None,
        stock_name=None,
        question="分析半导体行业机会",
    )

    assert "stock_code" not in result
    assert "stock_name" not in result
    assert "analysis_target" not in result
    assert "行业分析" in result["answer_markdown"]


@pytest.mark.asyncio
async def test_stock_analysis_runner_uses_tool_output_compression(monkeypatch) -> None:
    from app.ai.stock_analysis import runner

    fake_llm = FakeStockAnalysisLLM([
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "fake_stock_tool",
                    "args": {"value": "ok"},
                    "id": "tool-1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(
            content=(
                "# 最终报告\n\n## 结论\n不确定\n\n## 关键证据\n压缩完成\n\n"
                "## 主要风险\n无\n\n## 后续观察点\n无\n\n## 数据缺口\n无"
            )
        ),
    ])
    monkeypatch.setattr(runner, "build_chat_model", lambda **kwargs: fake_llm)
    monkeypatch.setattr(runner, "build_stock_analysis_tools", lambda: [FakeTool()])
    monkeypatch.setattr(runner, "record_llm_usage", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "should_summarize_tool_output", lambda tool_name, content: True)

    async def fake_summarize_tool_output(*args, **kwargs):
        return "compressed-result"

    monkeypatch.setattr(runner, "summarize_tool_output", fake_summarize_tool_output)

    result = await runner.run_single_stock_analysis(
        stock_code="600519.SH",
        stock_name="贵州茅台",
        question="分析",
    )

    assert result["tool_trace"][0]["summarized"] is True


@pytest.mark.asyncio
async def test_stock_analysis_runner_enters_final_mode_at_iteration_limit(monkeypatch) -> None:
    from app.ai.stock_analysis import runner

    tool_call_responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "fake_stock_tool",
                    "args": {"iteration": index},
                    "id": f"tool-{index}",
                    "type": "tool_call",
                }
            ],
        )
        for index in range(runner.MAX_STOCK_ANALYSIS_ITERATIONS)
    ]
    fake_llm = FakeStockAnalysisLLM([
        *tool_call_responses,
        AIMessage(
            content=(
                "# 最终报告\n\n## 结论\n不确定\n\n## 关键证据\n达到上限\n\n"
                "## 主要风险\n无\n\n## 后续观察点\n无\n\n## 数据缺口\n无"
            )
        ),
    ])
    monkeypatch.setattr(runner, "build_chat_model", lambda **kwargs: fake_llm)
    monkeypatch.setattr(runner, "build_stock_analysis_tools", lambda: [FakeTool()])
    monkeypatch.setattr(runner, "record_llm_usage", lambda *args, **kwargs: None)

    result = await runner.run_single_stock_analysis(
        stock_code="600519.SH",
        stock_name="贵州茅台",
        question="分析",
    )

    assert len(result["tool_trace"]) == runner.MAX_STOCK_ANALYSIS_ITERATIONS
    assert "达到上限" in result["answer_markdown"]


@pytest.mark.asyncio
async def test_stock_analysis_task_returns_result_for_async_task_runner(monkeypatch) -> None:
    from app.ai.stock_analysis import runner

    expected_result = {
        "stock_code": "600519.SH",
        "stock_name": "贵州茅台",
        "question": "分析",
        "answer_markdown": "# 报告",
    }

    async def fake_run_single_stock_analysis(*args, **kwargs):
        return expected_result

    def fail_session_local():
        raise AssertionError("async_task_runner owns task status updates")

    monkeypatch.setattr(runner, "run_single_stock_analysis", fake_run_single_stock_analysis)
    monkeypatch.setattr(runner, "SessionLocal", fail_session_local, raising=False)

    result = await runner.run_stock_analysis_task(
        task_id="task-stock-1",
        stock_code="600519.SH",
        stock_name="贵州茅台",
        question="分析",
        task_name="Stock Analysis - 600519.SH",
    )

    assert result == expected_result
