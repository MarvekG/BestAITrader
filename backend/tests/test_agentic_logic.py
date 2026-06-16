from datetime import date
import pytest
import httpx
from unittest.mock import MagicMock, patch, AsyncMock
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.tool import invalid_tool_call
from app.ai.agentic.tools import get_all_tools
from app.ai.agentic.memory_tools import build_memory_tools
from app.core.config import settings
from app.ai.memory_client import memory_client
from app.ai.llm_providers.litellm import LiteLLMProvider
from app.ai.llm_engine.agents import base as base_agent_module
from app.ai.llm_engine.agents.base import BaseAgent
from app.ai.llm_engine.models import PMDecision
from app.ai.llm_engine.roles import (
    AGENT_NAME_BULLISH_RESEARCHER,
    AGENT_NAME_CAPITAL_FLOW_ANALYST,
    AGENT_NAME_FUNDAMENTAL_ANALYST,
    AGENT_NAME_NEWS_ANALYST,
    AGENT_NAME_POLICY_ANALYST,
    AGENT_NAME_PORTFOLIO_MANAGER,
    AGENT_NAME_RISK_CONTROL_ANALYST,
    AGENT_NAME_SENTIMENT_ANALYST,
    AGENT_NAME_TECHNICAL_ANALYST,
)


class _FakeLLMProvider(LiteLLMProvider):
    def __init__(self, llm):
        self.llm = llm

    def build_chat_model(self, **kwargs):
        return self.llm


def _patch_base_agent_llm_provider(llm):
    return patch(
        "app.ai.llm_engine.agents.base.get_llm_provider",
        return_value=_FakeLLMProvider(llm),
    )


class _DummyLLMEngineAgent(BaseAgent):
    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return "dummy system prompt"

    def get_output_model(self):
        return str


class _DummyStructuredLLMEngineAgent(BaseAgent):
    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return "dummy structured system prompt"

    def get_output_model(self):
        return PMDecision


@pytest.mark.asyncio
async def test_base_agent_summarizes_long_news_tool_output_with_shared_helper():
    class _FakeTool:
        name = "search_news"

        async def ainvoke(self, args):
            return {"articles": [{"title": "新闻"}], "raw": "N" * 13000}

    first_response = MagicMock()
    first_response.content = ""
    first_response.tool_calls = [
        {
            "id": "tool-1",
            "name": "search_news",
            "args": {"query": "贵州茅台 新闻"},
        }
    ]
    first_response.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    final_response = MagicMock()
    final_response.content = (
        "# 研究报告\n\n"
        "## 核心判断\n"
        "新闻摘要显示近期信息面偏积极。\n\n"
        "这一变化提升了后续研究的证据密度。\n\n"
        "## 证据梳理\n"
        "工具返回内容已经被压缩为结构化摘要，保留了关键事实和数字。\n\n"
        "## 风险提示\n"
        "仍需结合后续公告与成交反馈继续跟踪。"
    )
    final_response.tool_calls = []
    final_response.usage_metadata = {"input_tokens": 20, "output_tokens": 40, "total_tokens": 60}

    mock_llm_with_tools = MagicMock()
    mock_llm_with_tools.ainvoke = AsyncMock(side_effect=[first_response, final_response])
    mock_raw_llm = MagicMock()
    mock_raw_llm.bind_tools.return_value = mock_llm_with_tools

    summary_calls = []

    async def _fake_summarize_tool_output(llm, *, role_name, tool_name, content, tool_args=None, **_kwargs):
        summary_calls.append(
            {
                "llm": llm,
                "role_name": role_name,
                "tool_name": tool_name,
                "content_len": len(content),
                "tool_args": tool_args,
            }
        )
        return "[Structured Summary of search_news]:\n新闻摘要"

    with _patch_base_agent_llm_provider(mock_raw_llm), \
         patch("app.ai.llm_engine.agents.base.get_all_tools", return_value=[_FakeTool()]), \
         patch("app.ai.llm_engine.agents.base.build_memory_tools", return_value=[]), \
         patch("app.ai.llm_engine.agents.base.record_llm_usage"), \
         patch("app.ai.llm_engine.agents.base.summarize_tool_output", new=_fake_summarize_tool_output):
        agent = _DummyLLMEngineAgent(role_name="Dummy Analyst")
        result = await agent.run({"stock_code": "000001.SZ"})

    assert summary_calls
    assert summary_calls[0]["llm"] is mock_raw_llm
    assert summary_calls[0]["role_name"] == "Dummy Analyst"
    assert summary_calls[0]["tool_name"] == "search_news"
    assert summary_calls[0]["content_len"] > 12000
    tool_messages = [
        message.content
        for call in mock_llm_with_tools.ainvoke.await_args_list
        for message in (call.args[0] if call.args else [])
        if message.__class__.__name__ == "ToolMessage"
    ]
    assert tool_messages
    assert tool_messages[0] == "[Structured Summary of search_news]:\n新闻摘要"
    assert result.startswith("# 研究报告")


@pytest.mark.asyncio
async def test_base_agent_skips_summary_after_compacted_news_output_is_small():
    class _FakeTool:
        name = "search_news"

        async def ainvoke(self, args):
            return [
                {
                    "title": "新闻",
                    "content": "短正文",
                    "url": "https://example.com/news",
                    "published_at": "2026-05-20",
                }
            ]

    first_response = MagicMock()
    first_response.content = ""
    first_response.tool_calls = [
        {
            "id": "tool-1",
            "name": "search_news",
            "args": {"query": "贵州茅台 新闻"},
        }
    ]
    first_response.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    final_response = MagicMock()
    final_response.content = (
        "# 研究报告\n\n"
        "## 核心判断\n"
        "短新闻工具输出无需二次摘要。\n\n"
        "这能减少额外 LLM 调用。\n\n"
        "## 证据梳理\n"
        "工具结果已经足够短。\n\n"
        "## 风险提示\n"
        "仍需关注后续变化。"
    )
    final_response.tool_calls = []
    final_response.usage_metadata = {"input_tokens": 20, "output_tokens": 40, "total_tokens": 60}

    mock_llm_with_tools = MagicMock()
    mock_llm_with_tools.ainvoke = AsyncMock(side_effect=[first_response, final_response])
    mock_raw_llm = MagicMock()
    mock_raw_llm.bind_tools.return_value = mock_llm_with_tools

    summary = AsyncMock(return_value="should not be used")

    with _patch_base_agent_llm_provider(mock_raw_llm), \
         patch("app.ai.llm_engine.agents.base.get_all_tools", return_value=[_FakeTool()]), \
         patch("app.ai.llm_engine.agents.base.build_memory_tools", return_value=[]), \
         patch("app.ai.llm_engine.agents.base.record_llm_usage"), \
         patch("app.ai.llm_engine.agents.base.summarize_tool_output", new=summary):
        agent = _DummyLLMEngineAgent(role_name="Dummy Analyst")
        result = await agent.run({"stock_code": "000001.SZ"})

    summary.assert_not_awaited()
    tool_messages = [
        message.content
        for call in mock_llm_with_tools.ainvoke.await_args_list
        for message in (call.args[0] if call.args else [])
        if message.__class__.__name__ == "ToolMessage"
    ]
    assert "短正文" in tool_messages[0]
    assert result.startswith("# 研究报告")


@pytest.mark.asyncio
async def test_base_agent_drops_invalid_tool_calls_from_replayed_history():
    first_response = AIMessage(
        content="tool args malformed",
        additional_kwargs={
            "tool_calls": [
                {
                    "id": "bad-tool-1",
                    "type": "function",
                    "function": {
                        "name": "search_news",
                        "arguments": '{"query"',
                    },
                }
            ]
        },
        invalid_tool_calls=[
            invalid_tool_call(
                name="search_news",
                id="bad-tool-1",
                args='{"query"',
                error="unexpected end of JSON input",
            )
        ],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    final_response = AIMessage(
        content=(
            "# 研究报告\n\n"
            "## 核心判断\n"
            "模型在修正无效工具调用后直接给出完整报告。\n\n"
            "这一轮没有继续保留坏掉的 tool_call 历史。\n\n"
            "## 证据梳理\n"
            "回放历史里不再包含 invalid_tool_calls，因此 provider 不会报工具链条缺失。\n\n"
            "## 风险提示\n"
            "仍需关注模型后续是否继续产生非法 JSON 参数。"
        ),
        usage_metadata={"input_tokens": 20, "output_tokens": 40, "total_tokens": 60},
    )

    call_messages = []
    mock_llm_with_tools = MagicMock()

    async def _fake_ainvoke(messages):
        call_messages.append(list(messages))
        return first_response if len(call_messages) == 1 else final_response

    mock_llm_with_tools.ainvoke = AsyncMock(side_effect=_fake_ainvoke)
    mock_raw_llm = MagicMock()
    mock_raw_llm.bind_tools.return_value = mock_llm_with_tools

    with _patch_base_agent_llm_provider(mock_raw_llm), \
         patch("app.ai.llm_engine.agents.base.get_all_tools", return_value=[]), \
         patch("app.ai.llm_engine.agents.base.build_memory_tools", return_value=[]), \
         patch("app.ai.llm_engine.agents.base.record_llm_usage"):
        agent = _DummyLLMEngineAgent(role_name="Dummy Analyst")
        result = await agent.run({"stock_code": "000001.SZ"})

    assert len(call_messages) == 2
    replayed_ai_messages = [
        message
        for message in call_messages[1]
        if isinstance(message, AIMessage)
    ]
    assert replayed_ai_messages
    assert all(not message.invalid_tool_calls for message in replayed_ai_messages)
    assert all("tool_calls" not in message.additional_kwargs for message in replayed_ai_messages)
    retry_messages = [
        message.content
        for message in call_messages[1]
        if isinstance(message, HumanMessage)
    ]
    assert any("invalid tool-call arguments" in content for content in retry_messages)
    assert result.startswith("# 研究报告")


@pytest.mark.asyncio
async def test_base_agent_forces_final_answer_after_iteration_limit():
    class _FakeTool:
        name = "search_news"

        def __init__(self) -> None:
            self.calls = []

        async def ainvoke(self, args):
            self.calls.append(args)
            return {"ok": True, "query": args["query"]}

    tool = _FakeTool()

    tool_responses = []
    for idx in range(base_agent_module.MAX_LLM_ITERATIONS):
        response = MagicMock()
        response.content = ""
        response.tool_calls = [
            {
                "id": f"tool-{idx + 1}",
                "name": "search_news",
                "args": {"query": f"query-{idx + 1}"},
            }
        ]
        response.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        tool_responses.append(response)

    final_response = AIMessage(
        content=(
            "# 研究报告\n\n"
            "## 核心判断\n"
            "工具预算耗尽后，模型基于当前记录直接给出最终结论。\n\n"
            "已有证据已经足够支撑本轮分析收尾。\n\n"
            "## 证据梳理\n"
            "回答仅依赖上下文和前二十次工具返回，不再继续补查。\n\n"
            "## 风险提示\n"
            "若后续需要新增信息，应在新一轮任务中重新分配工具预算。"
        ),
        usage_metadata={"input_tokens": 20, "output_tokens": 40, "total_tokens": 60},
    )

    mock_llm_with_tools = MagicMock()
    mock_llm_with_tools.ainvoke = AsyncMock(side_effect=tool_responses)

    mock_raw_llm = MagicMock()
    mock_raw_llm.bind_tools.return_value = mock_llm_with_tools
    mock_raw_llm.ainvoke = AsyncMock(return_value=final_response)

    with _patch_base_agent_llm_provider(mock_raw_llm), \
         patch("app.ai.llm_engine.agents.base.get_all_tools", return_value=[tool]), \
         patch("app.ai.llm_engine.agents.base.build_memory_tools", return_value=[]), \
        patch("app.ai.llm_engine.agents.base.record_llm_usage"):
        agent = _DummyLLMEngineAgent(role_name="Dummy Analyst")
        result = await agent.run({"stock_code": "000001.SZ"})

    assert len(tool.calls) == base_agent_module.MAX_LLM_ITERATIONS
    assert tool.calls[-1]["query"] == f"query-{base_agent_module.MAX_LLM_ITERATIONS}"
    assert mock_llm_with_tools.ainvoke.await_count == base_agent_module.MAX_LLM_ITERATIONS
    assert mock_raw_llm.ainvoke.await_count == 1
    finalize_messages = mock_raw_llm.ainvoke.await_args.args[0]
    finalize_human_messages = [
        message.content
        for message in finalize_messages
        if isinstance(message, HumanMessage)
    ]
    assert any("最大迭代次数上限" in content for content in finalize_human_messages)
    assert any("基于当前对话记录" in content for content in finalize_human_messages)
    assert result.startswith("# 研究报告")


@pytest.mark.asyncio
async def test_base_agent_repairs_structured_markdown_final_output_without_recalling_tools():
    class _FakeTool:
        name = "search_news"

        def __init__(self) -> None:
            self.calls = []

        async def ainvoke(self, args):
            self.calls.append(args)
            return {"success": True, "summary": "trade context confirmed"}

    tool = _FakeTool()

    tool_response = MagicMock()
    tool_response.content = ""
    tool_response.tool_calls = [
        {
            "id": "tool-1",
            "name": "search_news",
            "args": {"query": "601888.SH"},
        }
    ]
    tool_response.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    markdown_response = MagicMock()
    markdown_response.content = "## 交易执行确认\n\n| 项目 | 内容 |\n|:---|:---|\n| 动作 | 买入 |"
    markdown_response.tool_calls = []
    markdown_response.usage_metadata = {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50}

    invalid_retry_response_1 = AIMessage(
        content="仍然不是 JSON",
        usage_metadata={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
    )
    invalid_retry_response_2 = AIMessage(
        content="## 还是 Markdown",
        usage_metadata={"input_tokens": 35, "output_tokens": 10, "total_tokens": 45},
    )
    json_response = AIMessage(
        content=PMDecision(
            decision="buy",
            confidence_score=82,
            target_position=0.15,
            verdict_summary="Bull case is stronger after execution confirmation.",
            investment_plan="Keep a 15% pilot position and review the next report.",
            price_range="66.28",
            stop_loss=60.0,
            take_profit=78.0,
            holding_horizon_days=20,
            risk_assessment=0.42,
            execution_details="Trade executed successfully; no additional tool call needed.",
            report_markdown="# PM report\n\n## 1. Verdict\nExecuted a 15% pilot buy.",
        ).model_dump_json(),
        usage_metadata={"input_tokens": 30, "output_tokens": 40, "total_tokens": 70},
    )

    mock_llm_with_tools = MagicMock()
    mock_llm_with_tools.ainvoke = AsyncMock(side_effect=[tool_response, markdown_response])
    mock_raw_llm = MagicMock()
    mock_raw_llm.bind_tools.return_value = mock_llm_with_tools
    mock_raw_llm.ainvoke = AsyncMock(side_effect=[
        invalid_retry_response_1,
        invalid_retry_response_2,
        json_response,
    ])

    with _patch_base_agent_llm_provider(mock_raw_llm), \
         patch("app.ai.llm_engine.agents.base.get_all_tools", return_value=[tool]), \
         patch("app.ai.llm_engine.agents.base.build_memory_tools", return_value=[]), \
         patch("app.ai.llm_engine.agents.base.record_llm_usage"):
        agent = _DummyStructuredLLMEngineAgent(role_name="Portfolio Manager")
        result = await agent.run({"stock_code": "601888.SH"})

    assert result.decision == "buy"
    assert result.target_position == 0.15
    assert tool.calls == [{"query": "601888.SH"}]
    assert mock_llm_with_tools.ainvoke.await_count == 2
    assert mock_raw_llm.ainvoke.await_count == 3

    initial_human_messages = [
        message.content
        for message in mock_llm_with_tools.ainvoke.await_args_list[0].args[0]
        if isinstance(message, HumanMessage)
    ]
    assert any(content.startswith("STATIC_CONTEXT:\n") for content in initial_human_messages)
    assert any(content.startswith("RUNTIME_CONTEXT:\n") for content in initial_human_messages)

    repair_messages = [
        message.content
        for message in mock_raw_llm.ainvoke.await_args.args[0]
        if isinstance(message, HumanMessage)
    ]
    assert sum("不是合法 JSON" in content for content in repair_messages) == 3


@pytest.mark.asyncio
async def test_sync_base_info_func_batch_logic():
    """
    测试同步函数的批量逻辑 (仅测试逻辑支路，Mock 外部调用)
    Test batch logic of sync function (Mock external calls)
    """
    # Mock ingestor_manager and task_manager
    with patch("app.data.ingestors.manager.ingestor_manager") as mock_ingestor, \
         patch("app.tasks.task_manager.task_manager"), \
         patch("app.core.database.SessionLocal") as mock_session:

        # 模拟数据库查询返回 2 只股票 (Mock DB returns 2 stocks)
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_query = mock_db.query.return_value.filter.return_value.all
        mock_query.return_value = [("600519.SH",), ("000001.SZ",)]

        # 模拟 fetch_and_ingest_all_stock_basic 成功
        mock_ingestor.fetch_and_ingest_all_stock_basic.return_value = True

        # 调用不传参数的同步 (Call sync without stock_code)
        with patch("app.tasks.task_functions.sync_base_info_func", side_effect=[
            # 第一次调用（Batch 模式）
            {"status": "success"},
            # 随后的单股同步
            {"status": "success"}, {"status": "success"}
        ]):
            pass


def test_agentic_tools_registered():
    tool_names = {tool.name for tool in get_all_tools()}

    assert "query_stock_data" in tool_names
    assert "query_market_data" in tool_names
    assert "sync_market_data" in tool_names
    assert "browse_web_page_html" in tool_names
    assert "parse_pdf_to_markdown" in tool_names
    assert "search_news" in tool_names
    assert "search_tavily" not in tool_names
    assert "get_latest_indicators" not in tool_names
    assert "sync_source_financial_report" not in tool_names


def test_base_agent_can_attach_runtime_recall_tools_for_bull_role():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()
    with _patch_base_agent_llm_provider(mock_llm):
        agent = _DummyLLMEngineAgent(
            role_name=AGENT_NAME_BULLISH_RESEARCHER,
            state={
                "session_id": "sess-1",
                "user_id": 1,
                "stock_code": "000001.SZ",
            },
        )

    tool_names = {tool.name for tool in agent.tools}
    assert "recall_memory" in tool_names
    assert "write_memory" in tool_names


def test_base_agent_can_attach_runtime_write_tools_for_pm_role():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()
    with _patch_base_agent_llm_provider(mock_llm):
        agent = _DummyLLMEngineAgent(
            role_name=AGENT_NAME_PORTFOLIO_MANAGER,
            state={
                "session_id": "sess-1",
                "user_id": 1,
                "stock_code": "000001.SZ",
            },
        )

    tool_names = {tool.name for tool in agent.tools}
    assert "recall_memory" in tool_names
    assert "write_memory" in tool_names


def test_recall_memory_tool_description_requires_focused_natural_query():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    recall_tool = next(tool for tool in tools if tool.name == "recall_memory")

    assert "真实股票名 + 股票代码" in recall_tool.description
    assert "同时取真实股票名和股票代码写进 query" in recall_tool.description
    assert "要复用的经验主题" in recall_tool.description
    assert "2-5 个关键变量/动作/触发器" in recall_tool.description
    assert "中远海控(601919.SH) PM裁决 HOLD 仓位 止损 加仓触发" in recall_tool.description
    assert "时间意图" in recall_tool.description
    assert "问题类型" in recall_tool.description
    assert "不是必填项" in recall_tool.description
    assert "不要只写" in recall_tool.description
    assert "_target_stock_name" in recall_tool.description
    assert "_target_stock_code" in recall_tool.description
    assert "[MEMORY_TOPIC: risk_control]" in recall_tool.description
    assert "[MEMORY_TOPIC: driver_validation]" in recall_tool.description
    assert "[MEMORY_TOPIC: process_improvement]" in recall_tool.description
    assert "不按 Agent 角色固定召回主题" in recall_tool.description
    assert "自主决定是否召回以及召回哪些主题" in recall_tool.description
    assert "记忆召回协议:" in recall_tool.description
    assert "1. 使用时机:" in recall_tool.description
    assert "2. Query 结构:" in recall_tool.description
    assert "3. 主题召回:" in recall_tool.description
    assert "3.1 [MEMORY_TOPIC: decision_outcome]:" in recall_tool.description
    assert "当需要对比历史类似 PM 决策结果" in recall_tool.description
    assert "3.2 [MEMORY_TOPIC: risk_control]:" in recall_tool.description
    assert "当需要输出仓位、止损、`buy`/`sell`/`hold` 或失效条件时召回" in recall_tool.description
    assert "3.3 [MEMORY_TOPIC: driver_validation]:" in recall_tool.description
    assert "当需要判断当前核心驱动、信号或噪音是否已有历史验证/证伪经验时召回" in recall_tool.description
    assert "3.4 [MEMORY_TOPIC: strategy_fit]:" in recall_tool.description
    assert "当需要判断历史经验是否适配当前交易频率、交易策略或市场环境时召回" in recall_tool.description
    assert "3.5 [MEMORY_TOPIC: process_improvement]:" in recall_tool.description
    assert "当需要检查本轮 Debate / PM / 风控流程是否可能重复历史流程缺陷时召回" in recall_tool.description
    assert "4. Query 示例:" in recall_tool.description
    assert "5. 限制:" in recall_tool.description
    assert "交通银行(601328.SH) [MEMORY_TOPIC: risk_control] 中长线 价值投资 银行Beta 仓位 止损 加仓" in recall_tool.description
    assert "交通银行(601328.SH) [MEMORY_TOPIC: driver_validation] 业绩说明会 高股息 PB低估 板块资金流" in recall_tool.description
    assert "交通银行(601328.SH) [MEMORY_TOPIC: process_improvement] Debate PM 风控 检查项 催化验证" in recall_tool.description
    assert "当前目标股票 PM决策经验" not in recall_tool.description
    assert "真实股票名/代码 + 经验主体" not in recall_tool.description
    assert "不要在代码中硬编码强制主题检查" not in recall_tool.description
    assert "关键词匹配判断记忆是否合格" not in recall_tool.description


def test_write_memory_tool_description_requires_reusable_auditable_experience():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    write_tool = next(tool for tool in tools if tool.name == "write_memory")

    assert "能让系统持续进步的记忆" in write_tool.description
    assert "必须同时包含真实股票名和股票代码" in write_tool.description
    assert "交易频率" in write_tool.description
    assert "交易策略" in write_tool.description
    assert "若交易频率或交易策略无法确认" in write_tool.description
    assert "对象必须同时包含真实股票名和股票代码" in write_tool.description
    assert "场景" in write_tool.description
    assert "关键证据" in write_tool.description
    assert "触发器" in write_tool.description
    assert "失效条件" in write_tool.description
    assert "常见误判" in write_tool.description
    assert "执行纪律" in write_tool.description
    assert "中远海控(601919.SH) PM裁决经验" in write_tool.description
    assert "高股息是后视镜数据" in write_tool.description
    assert "[MEMORY_TOPIC: risk_control]" in write_tool.description
    assert "[MEMORY_TOPIC: strategy_fit]" in write_tool.description
    assert "一条 Memory 只写一个主主题" in write_tool.description
    assert "不同主题分次调用" in write_tool.description
    assert "不要把多个主题揉成一条" in write_tool.description
    assert "对象:" in write_tool.description
    assert "经验:" in write_tool.description
    assert "触发条件:" in write_tool.description
    assert "未来动作:" in write_tool.description
    assert "失效边界:" in write_tool.description
    assert "证据:" in write_tool.description
    assert "复盘写入必须包含后验市场结果或信号验证证据" in write_tool.description
    assert "Debate 内部写入不能伪造未来后验结果" in write_tool.description
    assert "记忆写入协议:" in write_tool.description
    assert "1. 写入前提:" in write_tool.description
    assert "2. 内容要素:" in write_tool.description
    assert "3. 协议主题:" in write_tool.description
    assert "3.1 [MEMORY_TOPIC: decision_outcome]:" in write_tool.description
    assert "如果原始 PM 结论有明确后验结果" in write_tool.description
    assert "后续收益/回撤/相对收益和结论正确性" in write_tool.description
    assert "3.2 [MEMORY_TOPIC: driver_validation]:" in write_tool.description
    assert "如果能区分被验证、被证伪和噪音信号" in write_tool.description
    assert "被排除伪因" in write_tool.description
    assert "3.3 [MEMORY_TOPIC: risk_control]:" in write_tool.description
    assert "如果仓位、止损、`buy`/`sell`/`hold` 或回撤管理有教训" in write_tool.description
    assert "板块 Beta" in write_tool.description
    assert "3.4 [MEMORY_TOPIC: strategy_fit]:" in write_tool.description
    assert "如果经验的适用频率、策略或市场环境存在明显边界" in write_tool.description
    assert "经验是否过时及原因" in write_tool.description
    assert "3.5 [MEMORY_TOPIC: process_improvement]:" in write_tool.description
    assert "如果能提炼出未来 Debate / PM / Risk 的流程检查项" in write_tool.description
    assert "Risk Control 要检查哪些否决条件" in write_tool.description
    assert "4. 拆分规则:" in write_tool.description
    assert "5. 推荐结构:" in write_tool.description
    assert "6. 禁止事项:" in write_tool.description
    assert "7. 异步语义:" in write_tool.description
    assert "不要在代码中硬编码强制主题检查" not in write_tool.description
    assert "关键词匹配判断记忆是否合格" not in write_tool.description


def test_base_agent_can_attach_runtime_recall_and_write_tools_for_risk_role():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()
    with _patch_base_agent_llm_provider(mock_llm):
        agent = _DummyLLMEngineAgent(
            role_name=AGENT_NAME_RISK_CONTROL_ANALYST,
            state={
                "session_id": "sess-1",
                "user_id": 1,
                "stock_code": "000001.SZ",
            },
        )

    tool_names = {tool.name for tool in agent.tools}
    assert "recall_memory" in tool_names
    assert "write_memory" in tool_names


def test_base_agent_does_not_attach_runtime_memory_tools_for_fundamental_role():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()
    with _patch_base_agent_llm_provider(mock_llm):
        agent = _DummyLLMEngineAgent(
            role_name=AGENT_NAME_FUNDAMENTAL_ANALYST,
            state={
                "session_id": "sess-1",
                "user_id": 1,
                "stock_code": "000001.SZ",
            },
        )

    tool_names = {tool.name for tool in agent.tools}
    assert "recall_memory" not in tool_names
    assert "write_memory" not in tool_names


def test_base_agent_does_not_attach_runtime_memory_tools_for_fact_first_roles():
    disabled_roles = [
        AGENT_NAME_FUNDAMENTAL_ANALYST,
        AGENT_NAME_TECHNICAL_ANALYST,
        AGENT_NAME_CAPITAL_FLOW_ANALYST,
        AGENT_NAME_SENTIMENT_ANALYST,
        AGENT_NAME_NEWS_ANALYST,
        AGENT_NAME_POLICY_ANALYST,
    ]

    for role_name in disabled_roles:
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = MagicMock()
        with _patch_base_agent_llm_provider(mock_llm):
            agent = _DummyLLMEngineAgent(
                role_name=role_name,
                state={
                    "session_id": "sess-1",
                    "user_id": 1,
                    "stock_code": "000001.SZ",
                },
            )

        tool_names = {tool.name for tool in agent.tools}
        assert "recall_memory" not in tool_names, role_name
        assert "write_memory" not in tool_names, role_name


def test_base_agent_does_not_attach_runtime_memory_tools_without_stock_code():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = MagicMock()
    with _patch_base_agent_llm_provider(mock_llm):
        agent = _DummyLLMEngineAgent(
            role_name=AGENT_NAME_PORTFOLIO_MANAGER,
            state={
                "session_id": "sess-1",
                "user_id": 1,
            },
        )

    tool_names = {tool.name for tool in agent.tools}
    assert "recall_memory" not in tool_names
    assert "write_memory" not in tool_names


@pytest.mark.asyncio
async def test_memory_client_records_last_error_for_failed_requests():
    class _FailingAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json, headers=None, timeout=None):
            del headers, timeout
            raise httpx.ConnectTimeout("memory backend timeout")

    memory_client.clear_last_error("recall")
    with patch("app.ai.memory_client.settings.MEMORY_SERVICE_ENABLED", True), \
         patch("app.ai.memory_client.settings.MEMORY_SERVICE_BASE_URL", "http://memo"), \
         patch("app.ai.memory_client.httpx.AsyncClient", _FailingAsyncClient):
        result = await memory_client.recall(
            user_id=1,
            stock_code="000001.SZ",
            query="history lesson",
        )

    assert result == {}
    assert memory_client.get_last_error("recall") == {
        "operation": "recall",
        "path": "/v1/recall",
        "message": "memory backend timeout",
        "error_type": "ConnectTimeout",
    }


@pytest.mark.asyncio
async def test_recall_memory_tool_surfaces_memory_backend_failure():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    recall_tool = next(tool for tool in tools if tool.name == "recall_memory")

    with patch("app.ai.agentic.memory_tools.memory_client.recall", new=AsyncMock(return_value={})), \
         patch("app.ai.agentic.memory_tools.memory_client.get_last_error", return_value={"message": "memory backend timeout"}):
        result = await recall_tool.ainvoke({"query": "history lesson"})

    assert result["data"] == {}
    assert result["count"] == 0
    assert result["memo_session"] == "stock"
    assert result["stock_code"] == "000001.SZ"
    assert result["error"] == "memory backend timeout"


@pytest.mark.asyncio
async def test_write_memory_tool_passes_minimal_session_inputs():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
            "trading_strategy": "momentum",
            "trading_frequency": "swing",
        },
    )
    write_tool = next(tool for tool in tools if tool.name == "write_memory")

    with patch(
        "app.ai.agentic.memory_tools.memory_client.write_memory",
        new=AsyncMock(return_value={"observation_id": "obs_1", "status": "pending"}),
    ) as mock_write:
        result = await write_tool.ainvoke({
            "content": "This breakout only works when northbound inflow confirms within 2 sessions.",
            "importance": "high",
        })

    assert result["success"] is True
    payload = mock_write.await_args.kwargs
    assert payload["user_id"] == 7
    assert payload["stock_code"] == "000001.SZ"
    assert payload["content"] == "This breakout only works when northbound inflow confirms within 2 sessions."
    assert result["memo_session"] == "stock"
    assert result["stock_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_write_memory_tool_returns_observation_id_without_event_id_alias():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    write_tool = next(tool for tool in tools if tool.name == "write_memory")

    with patch(
        "app.ai.agentic.memory_tools.memory_client.write_memory",
        new=AsyncMock(return_value={"observation_id": "obs_1", "status": "accepted"}),
    ):
        result = await write_tool.ainvoke({
            "content": "复盘经验：趋势没有确认前，不扩大仓位。",
            "importance": "high",
        })

    assert result["success"] is True
    assert result["observation_id"] == "obs_1"
    assert "event_id" not in result
    assert result["stock_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_write_memory_tool_uses_state_stock_code_without_tool_args():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    write_tool = next(tool for tool in tools if tool.name == "write_memory")

    with patch(
        "app.ai.agentic.memory_tools.memory_client.write_memory",
        new=AsyncMock(return_value={"observation_id": "obs_2", "status": "pending"}),
    ) as mock_write:
        result = await write_tool.ainvoke({
            "content": "通用规则：先看证据质量，再决定是否扩大仓位。",
            "importance": "medium",
        })

    payload = mock_write.await_args.kwargs
    assert payload["stock_code"] == "000001.SZ"
    assert payload["content"] == "通用规则：先看证据质量，再决定是否扩大仓位。"
    assert result["memo_session"] == "stock"
    assert result["stock_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_recall_memory_tool_returns_memoflux_data_shape():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    recall_tool = next(tool for tool in tools if tool.name == "recall_memory")

    with patch("app.ai.memory_client.settings.MEMORY_SERVICE_ENABLED", True), \
         patch("app.ai.memory_client.settings.MEMORY_SERVICE_BASE_URL", "http://memo"), \
         patch.object(memory_client, "_post", new=AsyncMock(return_value={
             "success": True,
             "data": {
                 "answer": "通用规则：先看证据质量，再决定是否扩大仓位。",
                 "references": [
                     {
                         "memory_id": "mem_1",
                         "content": "证据",
                         "occurred_at": "2026-05-01T00:00:00Z",
                         "relevance": "证据",
                     }
                 ],
                 "uncertainties": ["contradicting_memory:mem_2"],
             },
          })):
        result = await recall_tool.ainvoke({"query": "之前的通用规则是什么？"})

    assert result["count"] == 1
    assert result["memo_session"] == "stock"
    assert result["stock_code"] == "000001.SZ"
    assert result["data"]["answer"] == "通用规则：先看证据质量，再决定是否扩大仓位。"
    assert result["data"]["references"][0]["memory_id"] == "mem_1"
    assert result["data"]["uncertainties"] == ["contradicting_memory:mem_2"]
    assert "items" not in result


@pytest.mark.asyncio
async def test_write_memory_tool_uses_memory_client_request_adapter():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
            "trading_strategy": "momentum",
            "trading_frequency": "swing",
        },
    )
    write_tool = next(tool for tool in tools if tool.name == "write_memory")

    with patch("app.ai.memory_client.settings.MEMORY_SERVICE_ENABLED", True), \
         patch("app.ai.memory_client.settings.MEMORY_SERVICE_BASE_URL", "http://memo"), \
         patch.object(
             memory_client,
             "_post",
             new=AsyncMock(return_value={"observation_id": "obs_3", "status": "accepted"}),
         ) as mock_post:
        result = await write_tool.ainvoke({
            "content": "通用纪律：证据不一致时，不扩大仓位。",
            "importance": "medium",
        })

    assert result["success"] is True
    assert result["memo_session"] == "stock"
    assert result["stock_code"] == "000001.SZ"
    payload = mock_post.await_args.args[1]
    assert mock_post.await_args.args[0] == "/v1/ingest"
    assert payload["session"] == "user:7:stock:000001.SZ"
    assert payload["content"] == "通用纪律：证据不一致时，不扩大仓位。"
    assert "occurred_at" in payload


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_write_memory_tool_surfaces_memory_backend_failure():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-1",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    write_tool = next(tool for tool in tools if tool.name == "write_memory")

    with patch("app.ai.agentic.memory_tools.memory_client.write_memory", new=AsyncMock(return_value={})), \
         patch("app.ai.agentic.memory_tools.memory_client.get_last_error", return_value={"message": "memory backend timeout"}):
        result = await write_tool.ainvoke({
            "content": "high value lesson",
            "importance": "high",
        })

    assert result["success"] is False
    assert result["error"] == "memory backend timeout"


@pytest.mark.asyncio
async def test_write_memory_tool_rejects_invalid_importance():
    tools = build_memory_tools(
        state={
            "user_id": 7,
            "stock_code": "000001.SZ",
            "session_id": "sess-2",
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
        },
    )
    write_tool = next(tool for tool in tools if tool.name == "write_memory")

    with pytest.raises(Exception) as exc_info:
        await write_tool.ainvoke({
            "content": "short note",
            "importance": "urgent",
        })

    assert "low" in str(exc_info.value)
    assert "high" in str(exc_info.value)


@pytest.mark.asyncio
async def test_agentic_tools_new_entities():
    """测试统一市场查询工具可覆盖期货和宏观数据"""
    from app.ai.agentic.tools import query_market_data
    from app.ai.agentic.tooling.stock_tools import StockTools

    mock_data = [{"symbol": "AU2406", "close": 550.0}]
    with patch.object(StockTools, "get_generic_db_data", return_value=mock_data):
        res = query_market_data.invoke({
            "queries": [{
                "data_type": "futures",
                "identifier": "AU2406",
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
                "extra_params": {"futures_type": "internal"},
            }]
        })
        assert len(res) == 1
        assert res[0]["results"][0]["symbol"] == "AU2406"

    mock_index = [{"index_code": "000001.SH", "close": 3210.5}]
    with patch.object(StockTools, "get_generic_db_data", return_value=mock_index):
        res = query_market_data.invoke({
            "queries": [{
                "data_type": "index_daily",
                "identifier": "000001.SH",
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
            }]
        })
        assert len(res) == 1
        assert res[0]["results"][0]["index_code"] == "000001.SH"


@pytest.mark.asyncio
async def test_agentic_tools_analysis_suite():
    """测试统一股票查询工具可覆盖深度分析类数据"""
    from app.ai.agentic.tools import query_stock_data
    from app.ai.agentic.tooling.stock_tools import StockTools

    mock_insider = [{"stock_code": "600519", "direction": "buy"}]
    mock_pledge = [{"stock_code": "600519", "pledge_ratio": 12.5}]
    with patch.object(StockTools, "get_generic_db_data", side_effect=[mock_insider, mock_pledge]) as mock_generic:
        res = await query_stock_data.ainvoke({
            "stock_code": "600519",
            "data_configs": {
                "insider": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                },
                "pledge": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                },
            },
        })
        assert res["results"]["insider"][0]["direction"] == "buy"
        assert res["results"]["pledge"][0]["pledge_ratio"] == 12.5
        mock_generic.assert_any_call(
            "StockInsiderTrading", "600519", 20,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )
        mock_generic.assert_any_call(
            "StockPledge", "600519", 20,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )


def test_get_generic_db_data_selects_requested_columns(db_session, test_db, monkeypatch):
    from app.ai.agentic.tooling import stock_tools as stock_tools_module
    from app.ai.agentic.tooling.stock_tools import StockTools
    from app.models.data_storage import KlineData, StockBasic

    monkeypatch.setattr(stock_tools_module, "SessionLocal", test_db)
    db_session.add(
        StockBasic(
            stock_code="000001.SZ",
            name="平安银行",
            industry="银行",
            market="SZ",
        )
    )
    db_session.commit()
    db_session.add(
        KlineData(
            stock_code="000001.SZ",
            date=date(2024, 1, 2),
            open=10.0,
            close=10.5,
            volume=1000000,
        )
    )
    db_session.commit()

    rows = StockTools.get_generic_db_data(
        "KlineData",
        "000001.SZ",
        limit=20,
        start_time="2024-01-01",
        end_time="2024-12-31",
        columns=["date", "close"],
    )

    assert rows == [{"date": date(2024, 1, 2), "close": 10.5}]


def test_get_stock_basic_info_returns_share_fields(db_session, test_db, monkeypatch):
    from app.ai.agentic.tooling import stock_tools as stock_tools_module
    from app.ai.agentic.tooling.stock_tools import StockTools
    from app.models.data_storage import StockBasic, StockValuationHistory

    monkeypatch.setattr(stock_tools_module, "SessionLocal", test_db)
    db_session.add(
        StockBasic(
            stock_code="000001.SZ",
            name="平安银行",
            industry="银行",
            market="SZ",
        )
    )
    db_session.add(
        StockValuationHistory(
            stock_code="000001.SZ",
            data_date=date(2026, 6, 10),
            total_share=5_000_000_000,
            float_share=4_500_000_000,
        )
    )
    db_session.commit()

    result = StockTools.get_stock_basic_info("000001.SZ")

    assert result["total_share"] == 5_000_000_000
    assert result["float_share"] == 4_500_000_000
    assert result["share_unit"] == "shares"
    assert result["share_source"] == "stock_valuation_history"


@pytest.mark.asyncio
async def test_query_stock_data_passes_columns_to_generic_db_data():
    from app.ai.agentic.tools import query_stock_data
    from app.ai.agentic.tooling.stock_tools import StockTools

    mock_kline = [{"date": "2024-01-02", "close": 10.5}]

    with patch.object(StockTools, "get_generic_db_data", return_value=mock_kline) as mock_generic:
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "kline": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                    "columns": ["date", "close"],
                },
            },
        })

    assert res["results"]["kline"] == [{"date": "2024-01-02", "close": 10.5}]
    mock_generic.assert_called_once_with(
        "KlineData",
        "000001.SZ",
        20,
        start_time="2024-01-01 00:00:00",
        end_time="2024-12-31 23:59:59",
        columns=["date", "close"],
    )


def test_get_generic_db_data_rejects_unknown_columns():
    from app.ai.agentic.tooling.stock_tools import StockTools, UnsupportedColumnsError

    with pytest.raises(UnsupportedColumnsError) as exc_info:
        StockTools.get_generic_db_data("KlineData", columns=["date", "missing_column"])

    error_payload = exc_info.value.to_dict()
    assert error_payload["error"] == "Unsupported columns"
    assert error_payload["model_name"] == "KlineData"
    assert error_payload["unsupported_columns"] == ["missing_column"]
    assert "date" in error_payload["available_columns"]
    assert "Use get_database_schema" in error_payload["hint"]


@pytest.mark.asyncio
async def test_query_stock_data_returns_structured_column_error_without_traceback(caplog):
    from app.ai.agentic.tools import query_stock_data

    with caplog.at_level("ERROR"):
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "money_flow": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                    "columns": ["date", "net_inflow_retail"],
                },
            },
        })

    error_payload = res["results"]["money_flow"]
    assert error_payload["error"] == "Unsupported columns"
    assert error_payload["model_name"] == "StockMoneyFlow"
    assert error_payload["unsupported_columns"] == ["date", "net_inflow_retail"]
    assert "trade_date" in error_payload["available_columns"]
    assert "Use get_database_schema" in error_payload["hint"]
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_query_stock_data_jsonb_report_rejects_nested_metric_columns():
    from app.ai.agentic.tools import query_stock_data, SUPPORTED_STOCK_QUERY_TYPES

    assert "financial" not in SUPPORTED_STOCK_QUERY_TYPES
    assert "income_statement" not in SUPPORTED_STOCK_QUERY_TYPES
    assert "balance_sheet" not in SUPPORTED_STOCK_QUERY_TYPES
    assert "cashflow_statement" not in SUPPORTED_STOCK_QUERY_TYPES

    res = await query_stock_data.ainvoke({
        "stock_code": "000001.SZ",
        "data_configs": {
            "financial": {
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
                "columns": ["report_date", "eps", "roe"],
            },
        },
    })

    assert res["results"]["financial"]["error"] == "Unsupported data type: financial"


def test_query_market_data_passes_columns_to_generic_db_data():
    from app.ai.agentic.tools import query_market_data
    from app.ai.agentic.tooling.stock_tools import StockTools

    mock_index_data = [{"date": "2024-01-02", "close": 3210.0}]
    with patch.object(StockTools, "get_generic_db_data", return_value=mock_index_data) as mock_generic:
        res = query_market_data.invoke({
            "queries": [{
                "data_type": "index_daily",
                "identifier": "000001.SH",
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
                "columns": ["date", "close"],
            }]
        })

    assert res[0]["results"] == [{"date": "2024-01-02", "close": 3210.0}]
    mock_generic.assert_called_once_with(
        "IndexDaily",
        "000001.SH",
        20,
        start_time="2024-01-01 00:00:00",
        end_time="2024-12-31 23:59:59",
        columns=["date", "close"],
    )


def test_query_market_data_passes_columns_to_custom_queries():
    from app.ai.agentic.tools import query_market_data
    from app.ai.agentic.tooling.stock_tools import StockTools

    with patch.object(StockTools, "get_generic_db_data", side_effect=[
        [{"trade_date": "2024-01-02", "stock_code": "000001.SZ"}],
        [{"datetime": "2024-01-02 09:00:00", "close": 550.0}],
    ]) as mock_generic:
        res = query_market_data.invoke({
            "queries": [
                {
                    "data_type": "limit_pool",
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                    "columns": ["trade_date", "stock_code"],
                    "extra_params": {"pool_type": "up"},
                },
                {
                    "data_type": "futures",
                    "identifier": "AU2406",
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                    "columns": ["datetime", "close"],
                    "extra_params": {"futures_type": "internal"},
                },
            ]
        })

    assert res[0]["results"] == [{"trade_date": "2024-01-02", "stock_code": "000001.SZ"}]
    assert res[1]["results"] == [{"datetime": "2024-01-02 09:00:00", "close": 550.0}]
    mock_generic.assert_any_call(
        "StockLimitUpPool",
        "",
        limit=20,
        start_time="2024-01-01 00:00:00",
        end_time="2024-12-31 23:59:59",
        columns=["trade_date", "stock_code"],
    )
    mock_generic.assert_any_call(
        "InternalFuturesData",
        "AU2406",
        20,
        start_time="2024-01-01 00:00:00",
        end_time="2024-12-31 23:59:59",
        columns=["datetime", "close"],
    )


@pytest.mark.asyncio
async def test_query_stock_data_localizes_financial_report_data_keys():
    from app.ai.agentic.tools import query_stock_data

    res = await query_stock_data.ainvoke({
        "stock_code": "000001.SZ",
        "data_configs": {
            "income_statement": {
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
            },
        },
    })

    assert res["results"]["income_statement"]["error"] == "Unsupported data type: income_statement"


@pytest.mark.asyncio
async def test_agentic_tools_completion_suite():
    """验证统一查询工具覆盖技术指标、大宗交易、板块资金流、市场情绪、基金持仓"""
    from app.ai.agentic.tools import query_stock_data, query_market_data

    with patch("app.ai.agentic.tooling.stock_tools.StockTools.get_generic_db_data") as mock_generic:
        mock_generic.return_value = [{"indicator": "MACD", "value": 0.5}]
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "technical": {
                    "limit": 30,
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                }
            },
        })
        assert res["results"]["technical"][0]["indicator"] == "MACD"
        mock_generic.assert_any_call(
            "StockIndicators", "000001.SZ", 30,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )

        mock_generic.return_value = [{"price": 10.5, "volume": 1000000}]
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "block_trade": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                }
            },
        })
        assert res["results"]["block_trade"][0]["price"] == 10.5
        mock_generic.assert_any_call(
            "StockBlockTrade", "000001.SZ", 20,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )

        mock_generic.return_value = [{"sector_name": "电力", "net_inflow": 5000}]
        res = query_market_data.invoke({
            "queries": [{
                "data_type": "sector_money_flow",
                "identifier": "电力",
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
            }]
        })
        assert res[0]["results"][0]["sector_name"] == "电力"
        mock_generic.assert_any_call(
            "SectorMoneyFlow", "电力", 20,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )

        mock_generic.return_value = [{"fund_name": "中邮", "shares": 50000}]
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "fund_holding": {
                    "limit": 10,
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                }
            },
        })
        assert res["results"]["fund_holding"][0]["fund_name"] == "中邮"
        mock_generic.assert_any_call(
            "StockFundHolding", "000001.SZ", 10,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )


@pytest.mark.asyncio
async def test_agentic_tools_final_push_suite():
    """验证统一查询工具覆盖融资融券、指数、舆情、解禁、回购"""
    from app.ai.agentic.tools import query_stock_data, query_market_data

    with patch("app.ai.agentic.tooling.stock_tools.StockTools.get_generic_db_data") as mock_generic:
        mock_generic.return_value = [{"margin_balance": 1000000}]
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "margin": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                }
            },
        })
        assert res["results"]["margin"][0]["margin_balance"] == 1000000
        mock_generic.assert_any_call(
            "StockMargin", "000001.SZ", 20,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )

        mock_generic.return_value = [{"close": 3000}]
        res = query_market_data.invoke({
            "queries": [{
                "data_type": "index_daily",
                "identifier": "000001.SH",
                "limit": 30,
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
            }]
        })
        assert res[0]["results"][0]["close"] == 3000
        mock_generic.assert_any_call(
            "IndexDaily", "000001.SH", 30,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )

        mock_generic.return_value = [{"score": 0.8}]
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "sentiment": {
                    "limit": 10,
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                }
            },
        })
        assert res["results"]["sentiment"][0]["score"] == 0.8
        mock_generic.assert_any_call(
            "StockSentiment", "000001.SZ", 10,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )

        mock_generic.return_value = [{"release_date": "2024-01-01"}]
        res = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "lockup_release": {
                    "limit": 10,
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                }
            },
        })
        assert res["results"]["lockup_release"][0]["release_date"] == "2024-01-01"
        mock_generic.assert_any_call(
            "StockRelease", "000001.SZ", 10,
            start_time="2024-01-01 00:00:00",
            end_time="2024-12-31 23:59:59",
        )

@pytest.mark.asyncio
async def test_unified_agentic_tools():
    from app.ai.agentic.tools import query_stock_data, query_market_data, sync_market_data
    from app.ai.agentic.tooling.stock_tools import StockTools

    with patch.object(StockTools, "check_data_status", return_value={"basic_info": "exists"}), \
         patch.object(
             StockTools,
             "get_generic_db_data",
             return_value=[{"roe": 12.5}],
         ):
        result = await query_stock_data.ainvoke({
            "stock_code": "000001.SZ",
            "data_configs": {
                "status": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                },
                "financial": {
                    "start_time": "2024-01-01 00:00:00",
                    "end_time": "2024-12-31 23:59:59",
                },
            },
        })
        assert result["results"]["status"]["basic_info"] == "exists"
        assert result["results"]["financial"]["error"] == "Unsupported data type: financial"

    with patch.object(StockTools, "get_generic_db_data", return_value=[{"index_code": "000001.SH", "close": 3201.0}]):
        result = query_market_data.invoke({
            "queries": [{
                "data_type": "index_daily",
                "identifier": "000001.SH",
                "start_time": "2024-01-01 00:00:00",
                "end_time": "2024-12-31 23:59:59",
            }]
        })
        assert result[0]["results"][0]["index_code"] == "000001.SH"
        assert result[0]["data_type"] == "index_daily"

    result = await sync_market_data.ainvoke({"task_type": "financial", "target": "000001.SZ"})
    assert result["success"] is False
    assert result["error"] == "Unsupported sync task type."

    with patch(
        "app.ai.agentic.tools.ingestor_manager.fetch_and_ingest_realtime_market",
        new=AsyncMock(return_value=True),
    ) as mock_realtime_sync:
        result = await sync_market_data.ainvoke({"task_type": "realtime", "target": "000001.SZ"})
        assert result["success"] is True
        assert result["resolved_method"] == "fetch_and_ingest_realtime_market"
        mock_realtime_sync.assert_awaited_once_with(stock_code="000001.SZ")


@pytest.mark.asyncio
async def test_llm_engine_agent_retries_when_final_text_is_too_short():
    short_response = MagicMock()
    short_response.content = "现在让我计算估值历史分位："
    short_response.tool_calls = []
    short_response.usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

    full_response = MagicMock()
    full_response.content = (
        "# 技术面分析报告\n\n"
        "短线趋势维持偏强，但更适合在回踩确认后寻找盈亏比更优的参与点。\n\n"
        "## 核心判断\n"
        "当前趋势延续，量能温和放大，短期均线维持多头排列，MACD 与 RSI 没有出现明显顶背离。\n\n"
        "## 证据梳理\n"
        "股价仍运行在中期上升通道内，支撑位与阻力位相对清晰，回踩确认后的盈亏比优于追高。\n\n"
        "## 风险提示\n"
        "后续需要继续跟踪成交额变化、板块联动和回撤幅度，避免在情绪冲高时扩大仓位。"
    )
    full_response.tool_calls = []
    full_response.usage_metadata = {"input_tokens": 20, "output_tokens": 40, "total_tokens": 60}

    mock_llm_with_tools = MagicMock()
    mock_llm_with_tools.ainvoke = AsyncMock(side_effect=[short_response, full_response])

    mock_raw_llm = MagicMock()
    mock_raw_llm.bind_tools.return_value = mock_llm_with_tools
    with _patch_base_agent_llm_provider(mock_raw_llm), \
         patch("app.ai.llm_engine.agents.base.record_llm_usage"):
        agent = _DummyLLMEngineAgent(role_name="Dummy Analyst")
        result = await agent.run({"stock_code": "000001.SZ"})

    assert result == full_response.content
    assert mock_llm_with_tools.ainvoke.call_count == 2
    retry_messages = mock_llm_with_tools.ainvoke.await_args_list[1].args[0]
    assert any(
        "你的上一条回复结构不完整：" in getattr(message, "content", "")
        and "请继续补全为一份完整的 Markdown 分析报告" in getattr(message, "content", "")
        for message in retry_messages
    )


if __name__ == "__main__":
    pytest.main([__file__])
