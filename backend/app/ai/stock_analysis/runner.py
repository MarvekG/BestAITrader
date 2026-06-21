from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.ai.agentic.skills_loader.runtime import build_skills_catalog_prompt, get_skills_loader_tools
from app.ai.agentic.tool_output_summarizer import should_summarize_tool_output, summarize_tool_output
from app.ai.agentic.tools import get_stock_analysis_tools
from app.ai.json_utils import stable_json_dumps
from app.ai.llm_providers.factory import build_chat_model
from app.core.config import settings
from app.crud.llm_usage_log import record_llm_usage

MAX_STOCK_ANALYSIS_ITERATIONS = 60


def _format_prompt_current_time() -> str:
    """
    生成投研分析提示词中的当前时间基准。

    Returns:
        面向 LLM 的上海时区当前时间文本。
    """
    current_time = datetime.now(ZoneInfo("Asia/Shanghai"))
    return current_time.strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")


def build_stock_analysis_tools() -> list[Any]:
    """
    构建单 LLM 股票分析工具列表。

    Returns:
        已按投研白名单筛选并追加 skill loader 的 LangChain 工具列表。
    """
    tools = list(get_stock_analysis_tools())
    tools.extend(get_skills_loader_tools())
    return tools


def _build_system_prompt(stock_code: str | None, stock_name: str | None) -> str:
    """
    构建单 LLM 股票分析系统提示词。

    Args:
        stock_code: 可选股票代码。
        stock_name: 可选股票名称。

    Returns:
        系统提示词。
    """
    skills_prompt = build_skills_catalog_prompt()
    target_context = (
        f"可选股票上下文: {stock_name} ({stock_code})。\n"
        if stock_code
        else (
            "用户未指定固定股票，请根据用户问题判断分析对象。\n"
        )
    )
    base_prompt = (
        "你是 AI 投研分析助手，目标是回答用户的投研问题。\n"
        f"当前时间: {_format_prompt_current_time()}。\n"
        f"{target_context}"
        "你可以自主调用工具和skills。\n"
        "在引用工具返回的数据前，必须调用 `get_current_time` 获取当前系统时间以判断数据的时效性和有效性；数据过旧时必须说明时效性限制。\n"
        "必须以事实为准绳: 所有判断都要回到可核验的数据、公告、新闻、工具结果或明确来源；"
        "不得把传闻、臆测、经验或模型直觉当成事实。\n"
        "如果信息不足，必须说明缺口和不确定性，不得编造。\n"
        "最终输出 Markdown 报告，至少包含: 结论、关键证据、主要风险、后续观察点、数据缺口。"
    )
    if not skills_prompt:
        return base_prompt
    return f"{base_prompt}\n\n{skills_prompt}"


def _json_tool_result(value: Any) -> str:
    """
    将工具返回值转换为稳定 JSON 文本。

    Args:
        value: 工具返回值。

    Returns:
        可写入 ToolMessage 的文本。
    """
    if isinstance(value, (dict, list)):
        return stable_json_dumps(value)
    return str(value)


async def run_single_stock_analysis(stock_code: str | None, stock_name: str | None, question: str) -> dict[str, Any]:
    """
    运行单 LLM 股票自主分析工具循环。

    Args:
        stock_code: 可选股票代码。
        stock_name: 可选股票名称。
        question: 用户问题。

    Returns:
        包含 Markdown 报告和工具轨迹的结构化结果。
    """
    llm = build_chat_model(model=settings.LLM_MODEL, temperature=0.2)
    tools = build_stock_analysis_tools()
    tool_map = {tool.name: tool for tool in tools}
    llm_with_tools = llm.bind_tools(tools)
    messages: list[Any] = [
        SystemMessage(content=_build_system_prompt(stock_code, stock_name)),
        HumanMessage(content=f"用户问题: {question}"),
    ]
    tool_trace: list[dict[str, Any]] = []

    for iteration_index in range(1, MAX_STOCK_ANALYSIS_ITERATIONS + 1):
        response = await llm_with_tools.ainvoke(messages)
        record_llm_usage(
            response,
            settings.LLM_MODEL,
            "stock_analysis",
            workflow="stock_analysis",
            stage="single_llm_analysis",
            call_kind="agent",
            iteration_index=iteration_index,
        )
        messages.append(response)
        tool_calls = list(getattr(response, "tool_calls", []) or [])
        if not tool_calls:
            return _build_analysis_result(stock_code, stock_name, question, response.content, tool_trace)

        for tool_call in tool_calls:
            tool_trace.append(
                await _execute_stock_analysis_tool_call(
                    llm=llm,
                    tool_map=tool_map,
                    messages=messages,
                    tool_call=tool_call,
                    iteration_index=iteration_index,
                )
            )

    messages.append(
        HumanMessage(
            content=(
                "你已经达到最大迭代次数上限。禁止继续调用任何工具，"
                "请基于已有证据输出最终 Markdown 报告。"
            )
        )
    )
    final_response = await llm.ainvoke(messages)
    record_llm_usage(
        final_response,
        settings.LLM_MODEL,
        "stock_analysis",
        workflow="stock_analysis",
        stage="single_llm_analysis",
        call_kind="final_no_tools",
        iteration_index=MAX_STOCK_ANALYSIS_ITERATIONS + 1,
    )
    return _build_analysis_result(stock_code, stock_name, question, final_response.content, tool_trace)


def _build_analysis_result(
    stock_code: str | None,
    stock_name: str | None,
    question: str,
    content: Any,
    tool_trace: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    构建可持久化的股票分析结果。

    Args:
        stock_code: 可选股票代码。
        stock_name: 可选股票名称。
        question: 用户问题。
        content: LLM 最终输出。
        tool_trace: 工具调用轨迹。

    Returns:
        股票分析结果。
    """
    return {
        "question": question,
        "answer_markdown": str(content or "").strip(),
        "tool_trace": tool_trace,
        "model": settings.LLM_MODEL,
        "completed_at": datetime.now().isoformat(),
    }


async def _execute_stock_analysis_tool_call(
    *,
    llm: Any,
    tool_map: dict[str, Any],
    messages: list[Any],
    tool_call: dict[str, Any],
    iteration_index: int,
) -> dict[str, Any]:
    """
    执行一次工具调用并把结果写回消息列表。

    Args:
        llm: 未绑定工具的 LLM，用于长工具输出压缩。
        tool_map: 工具名到工具对象的映射。
        messages: 当前对话消息列表。
        tool_call: LangChain 工具调用载荷。
        iteration_index: 当前工具循环序号。

    Returns:
        单次工具调用轨迹。
    """
    tool_name = str(tool_call.get("name") or "")
    tool_args = tool_call.get("args") or {}
    tool_call_id = str(tool_call.get("id") or f"tool-{iteration_index}-{len(messages)}")
    trace_item = {"name": tool_name, "args": tool_args, "success": False, "summarized": False}
    tool_func = tool_map.get(tool_name)
    if tool_func is None:
        result_text = f"Error: Tool {tool_name} not found"
        trace_item["error"] = result_text
    else:
        try:
            raw_result = await tool_func.ainvoke(tool_args)
            result_text = _json_tool_result(raw_result)
            if should_summarize_tool_output(tool_name, result_text):
                result_text = await summarize_tool_output(
                    llm,
                    role_name="stock_analysis",
                    tool_name=tool_name,
                    content=result_text,
                    tool_args=tool_args,
                    workflow="stock_analysis",
                    stage="single_llm_analysis",
                    iteration_index=iteration_index,
                )
                trace_item["summarized"] = True
            trace_item["success"] = True
        except Exception as exc:
            result_text = f"Error: {exc}"
            trace_item["error"] = str(exc)
    messages.append(ToolMessage(tool_call_id=tool_call_id, content=result_text))
    return trace_item


async def run_stock_analysis_task(
    task_id: str,
    stock_code: str | None,
    stock_name: str | None,
    question: str,
    task_name: str | None = None,
) -> dict[str, Any]:
    """
    执行后台单 LLM 股票分析任务并返回可由任务框架持久化的结果。

    Args:
        task_id: 异步任务 ID。
        stock_code: 可选股票代码。
        stock_name: 可选股票名称。
        question: 用户问题。
        task_name: 任务框架注入的展示名称，当前仅用于兼容统一任务签名。

    Returns:
        股票分析结果。
    """
    return await run_single_stock_analysis(stock_code, stock_name, question)
