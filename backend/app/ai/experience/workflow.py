from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from app.ai.agentic.memory_tools import build_memory_tools
from app.ai.llm_providers import get_llm_provider
from app.ai.agentic.tool_output_summarizer import (
    should_summarize_tool_output,
    summarize_tool_output,
)
from app.ai.agentic.tools import get_all_tools, make_json_serializable
from app.ai.agentic.skills_loader.runtime import (
    build_skills_catalog_prompt,
    get_skills_loader_tools,
)
from app.ai.llm_routing import get_research_usage_lane
from app.ai.json_utils import stable_json_dumps
from app.core.config import settings
from app.core.logger import get_logger
from app.crud.llm_usage_log import record_llm_usage
from app.ai.llm_engine.roles import AGENT_NAME_PORTFOLIO_MANAGER
from app.websocket.manager import ws_manager

logger = get_logger(__name__)

EXPERIENCE_REVIEW_MAX_ITERATIONS = 50
EXPERIENCE_REVIEW_FINAL_RETRY_LIMIT = 3


class ExperienceReviewOutput(BaseModel):
    original_pm_decision: str = Field(
        default="",
        description="原始 PM 的简短动作或结论，例如 buy、hold 或 sell。 / The original PM action or conclusion in a short form, such as buy, hold, or sell."
    )
    debate_correctness: str = Field(
        pattern="^(correct|partially_correct|incorrect|inconclusive)$",
        description="原始 PM 结论相对后验市场结果的正确性。 / Correctness of the original PM conclusion against the posterior market outcome."
    )
    correctness_reasoning: str = Field(
        default="",
        description="用 1-3 句说明原始结论为何正确、部分正确或错误，并提炼最重要的涨跌原因与可复用教训。不要重复记忆文档全文。 / In 1-3 concise sentences, explain why the original conclusion was correct, partially correct, or incorrect, and state the key price driver and reusable lesson. Do not repeat the memory document."
    )


class ExperienceWorkflowState(TypedDict, total=False):
    user_id: int
    session_id: str
    review_run_id: str
    stock_code: str
    stock_name: str
    industry: Optional[str]
    style_bucket: str
    trading_frequency: Optional[str]
    trading_strategy: Optional[str]
    debate_review_context: Dict[str, Any]
    full_context: Dict[str, Any]
    analysis_payload: Dict[str, Any]
    tool_trace: List[Dict[str, Any]]
    review_events: List[Dict[str, Any]]
    event_callback: Callable[..., Awaitable[None]]
    errors: List[str]


def _extract_written_memories(tool_trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for entry in tool_trace:
        if not isinstance(entry, dict) or entry.get("name") != "write_memory":
            continue
        args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
        result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
        content = str(args.get("content") or "").strip()
        if not content and not result.get("error") and result.get("success") is not False:
            continue
        stock_code = str(result.get("stock_code") or "").strip() or None
        status = result.get("status")
        if not status:
            status = "success" if result.get("success") is True else "failed" if result.get("success") is False or result.get("error") else "unknown"
        item: dict[str, Any] = {
            "stock_code": stock_code,
            "status": status,
            "size_chars": result.get("size_chars") or len(content),
            "max_chars": result.get("max_chars") or settings.MEMORY_DOC_MAX_CHARS,
        }
        for key in ("error", "version"):
            value = result.get(key)
            if value not in (None, ""):
                item[key] = value
        items.append(item)
    return items


def _build_experience_analysis_payload(
    validated_output: ExperienceReviewOutput,
    tool_trace: List[Dict[str, Any]],
) -> Dict[str, Any]:
    payload = validated_output.model_dump(mode="python")
    payload["written_memories"] = _extract_written_memories(tool_trace)
    return payload


def _build_final_json_retry_message() -> str:
    return (
        "工具调用阶段已经结束。不要再调用任何工具。"
        "请只基于当前对话里的 review_input 和工具结果，返回一个严格合法的 JSON 对象。"
        "不要输出 markdown、代码围栏或解释文字。\n"
        "The tool phase is closed. Do not call tools. Return exactly one valid JSON object only.\n\n"
        f"JSON Schema: {stable_json_dumps(ExperienceReviewOutput.model_json_schema())}"
    )


def _parse_json_response_content(content: Any) -> Optional[Dict[str, Any]]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    if isinstance(content, list):
        text_parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if text:
                    text_parts.append(text)
            elif isinstance(item, str):
                text_parts.append(item)
        if not text_parts:
            return None
        try:
            parsed = json.loads("".join(text_parts))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _parse_experience_output(content: Any) -> Optional[ExperienceReviewOutput]:
    payload = _parse_json_response_content(content)
    if not isinstance(payload, dict):
        return None
    try:
        return ExperienceReviewOutput.model_validate(payload)
    except Exception as exc:
        logger.warning("Invalid experience review payload: %s", exc)
        return None


async def _retry_final_experience_json(
    *,
    raw_llm: Any,
    llm_provider: Any,
    messages: List[Any],
    tool_trace: List[Dict[str, Any]],
    review_events: List[Dict[str, Any]],
    session_id: Optional[str],
    stock_code: str,
) -> Optional[Dict[str, Any]]:
    retry_messages = list(messages)

    for retry_index in range(EXPERIENCE_REVIEW_FINAL_RETRY_LIMIT):
        logger.warning(
            "experience review final JSON retry=%s session=%s stock=%s",
            retry_index + 1,
            session_id,
            stock_code,
        )
        retry_messages.append(HumanMessage(content=_build_final_json_retry_message()))
        response = await raw_llm.ainvoke(retry_messages)
        cache_lane, api_key_alias = get_research_usage_lane()
        await record_llm_usage(
            response,
            settings.LLM_MODEL,
            "experience_debate_review",
            workflow="experience_review",
            stage="final_json_retry",
            call_kind="json_retry",
            iteration_index=retry_index + 1,
            cache_lane=cache_lane,
            api_key_alias=api_key_alias,
        )
        response, invalid_tool_calls = llm_provider.sanitize_tool_call_response_for_replay(response)

        if getattr(response, "tool_calls", None) or invalid_tool_calls:
            retry_messages.append(
                HumanMessage(
                    content=(
                        "上一条回复仍然包含工具调用。工具已经关闭，请不要调用工具，只返回最终 JSON。"
                        "Your previous response still attempted tool calls. Return JSON only."
                    )
                )
            )
            continue

        retry_messages.append(response)
        validated_output = _parse_experience_output(response.content)
        if validated_output is not None:
            return {
                "analysis_payload": _build_experience_analysis_payload(
                    validated_output,
                    tool_trace,
                ),
                "tool_trace": tool_trace,
                "review_events": review_events,
                "errors": [],
            }

        retry_messages.append(
            HumanMessage(
                content=(
                    "上一条回复不是合法 JSON 或不符合 schema。请只返回一个可解析的 JSON 对象。"
                    "The previous response was not valid schema-compliant JSON. Return JSON only."
                )
            )
        )

    return None


async def _push_review_update(
    state: ExperienceWorkflowState,
    *,
    stage: str,
    status: str,
    message: str = "",
    message_key: Optional[str] = None,
    message_params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    event_callback = state.get("event_callback")
    try:
        if event_callback:
            await event_callback(
                stage=stage,
                status=status,
                message_key=message_key,
                message_params=message_params,
                payload=payload or {},
            )
        debate_session_id = state.get("session_id")
        if not debate_session_id:
            return
        await ws_manager.send_experience_review_update(
            debate_session_id=debate_session_id,
            review_run_id=state.get("review_run_id"),
            stage=stage,
            status=status,
            message=message,
            message_key=message_key,
            message_params=message_params,
            payload=payload or {},
        )
    except Exception:
        logger.exception("experience review websocket push failed")


async def fetch_full_context(state: ExperienceWorkflowState) -> Dict[str, Any]:
    try:
        review_context = state.get("debate_review_context") or {}
        full_context = {
            "session": review_context.get("session") or {},
            "pm_decision": review_context.get("pm_decision") or {},
            "debate_timeline": review_context.get("debate_timeline") or [],
            "execution_summary": review_context.get("execution_summary") or {},
            "market_outcome_summary": review_context.get("market_outcome_summary") or {},
        }
        await _push_review_update(
            state,
            stage="fetch_context",
            status="completed",
            message_key="experience.live_messages.fetch_context_ready",
            payload={
                "timeline_count": len(full_context["debate_timeline"]),
                "has_market_outcome": bool(full_context["market_outcome_summary"]),
            },
        )
        return {"full_context": full_context, "errors": []}
    except Exception as exc:
        logger.exception("experience fetch_full_context failed")
        return {"errors": [f"fetch_full_context failed: {exc}"]}


def _build_review_system_prompt(skills_prompt_suffix: str) -> str:
    """构建经验复盘工作流的系统提示词。

    Args:
        skills_prompt_suffix: 技能目录提示词补充内容。

    Returns:
        根据系统语言生成的完整系统提示词。
    """
    schema = stable_json_dumps(ExperienceReviewOutput.model_json_schema())
    if str(settings.SYSTEM_LANGUAGE).lower().startswith("zh"):
        return (
            "你是一名 A 股投研复盘分析师，围绕当前股票的长期记忆文档复盘已有 debate / PM 结论。"
            "你只需要完成三项输出：原始 PM 动作、结论正确性、以及 1-3 句简短复盘解释。"
            "解释必须同时覆盖后验市场结果、最主要的涨跌原因和一条可复用教训；不要复述 timeline，不要输出记忆文档全文。"
            "你的主输入是 agent timeline、PM 交易字段、执行结果和决策后的市场结果，必须把 market_outcome_summary 的收益、回撤和相对收益作为核心证据。"
            "凡是决策时点的历史事实，包括 timeline、PM 字段、执行结果、价格路径、收益和回撤，一律以 review_input 为准；工具返回的信息只能补充或验证，不能改写历史事实。"
            "后验检查 PM 的止盈、止损和持有周期是否匹配实际价格路径；如果发现退出纪律或周期明显不匹配，把结论压缩进 correctness_reasoning。系统固定的 5d/20d/60d 复盘周期不得改变。"
            "分析涨跌原因时，优先判断政策、行业、宏观、业绩、估值、资金、板块环境、情绪、事件、商品成本和利率汇率等因素，区分真正主因、被验证信号、被证伪信号与噪音。"
            "如果上下文不足以解释涨跌，可以调用外部工具补证据；已有证据足够时不要机械搜索。使用行情、财务或基本面工具前，先调用 get_current_time 判断时效。"
            "历史经验只能通过记忆工具读取和写入：每只股票只有一份自由格式 Markdown 记忆文档。只有历史经验能降低不确定性时才 read_memory，不要机械调用。"
            "只有在总结出可复用的赚钱经验、失败教训、仓位纪律或 debate 流程改进规则后，才调用 `write_memory` 重写整份记忆文档；如果没有新增可复用经验，可以跳过全部记忆写入。"
            "调用 `write_memory` 前，先提炼 1-3 条可独立复用的高信息密度教训，避免空泛套话。"
            "记忆工具已自动绑定到当前股票，只允许写入当前股票记忆，不支持通用记忆，也不要尝试传入 `stock_code`。"
            "\n记忆写入协议:\n"
            "1. 写前必读: 调用 `write_memory` 前必须先 `read_memory` 获取最新全文，并把返回的 `version` 作为 `base_version` 传入。\n"
            "2. 整文档替换: `content` 必须是重写后的完整文档全文；未包含进 `content` 的旧内容会被永久丢弃，重写时必须保留仍然有效的历史经验，并把本次复盘新增经验合并进去。\n"
            "3. 自由格式: 文档结构由你自主组织，不设固定模板；建议按时间组织、标注决策与复盘日期、保留后验收益与信号验证证据、合并过时或重复内容。\n"
            "4. 内容要素: 新增经验必须同时包含真实股票名和股票代码，并清楚覆盖对象、交易频率、交易策略、原始 PM 结论正确性、决策后实际涨跌结果、`take_profit` 与 `holding_horizon_days` 的后验评价、主导驱动、被验证信号、被证伪信号、可复用规则、失效条件与适用边界；决策时间、复盘时间和复盘周期由你在文档中自行标注。若交易频率或交易策略无法确认，必须在正文中说明缺失。\n"
            "5. 容量上限: 文档超过系统上限时写入失败，必须在同一轮先精炼合并旧内容再重试，不要直接放弃。\n"
            "6. 版本冲突: 写入失败并返回最新全文时，把你的新增经验合并进最新全文后用返回的版本号重试；禁止覆盖其他来源新增的内容。\n"
            "7. 写入次数: 通常一次 `write_memory` 即可完成本次复盘的记忆更新；只有确实需要再次补充时才多次调用。\n"
            "如果调用 `write_memory`，写入后的文档必须直接包含本次复盘的经验教训、可执行规则和适用边界，不要只重复结论标签。不要写普通背景或流水账。"
            "最终 JSON 只返回 schema 中的三个字段；工具调用轨迹由系统单独保留，不要复制进 JSON。"
            "不要输出 markdown，不要输出额外解释，只返回严格合法的 JSON 对象。"
            f"{skills_prompt_suffix}"
            f"最终 JSON Schema: {schema}"
        )
    return (
        "You are an A-share review analyst. Review the existing debate / PM conclusion around the current stock's long-term memory document. "
        "Return only three analytical fields: the original PM action, correctness, and a concise 1-3 sentence review explanation. "
        "The explanation must cover the posterior market outcome, the main price driver, and one reusable lesson; do not repeat the timeline or the memory document. "
        "The input contains agent timeline conclusions, PM trading fields, execution outcome, and post-decision market outcome. Treat the returns, drawdowns, and relative-performance fields in `market_outcome_summary` as core evidence. "
        "For decision-time historical facts, `review_input` is the source of truth. Tool output is supplementary current-time information and may only explain or corroborate it, never overwrite it. "
        "Evaluate the PM's take-profit, stop-loss, and holding-period design against the actual price path. Compress any mismatch or missing exit discipline into `correctness_reasoning`; do not change the fixed 5d/20d/60d review horizons. "
        "When explaining the move, check policy, industry, macro, earnings, valuation, flow, sector beta, sentiment, events, commodity costs, and rates/FX, separating true drivers, validated signals, falsified signals, and noise. "
        "If the current context is insufficient to explain the move, you may call external tools for evidence; otherwise do not search mechanically. "
        "Before using market, financial, or fundamental data from tools, call `get_current_time` to confirm the current system time and assess data freshness and validity. "
        "Historical experience can only be read or written through memory tools: each stock keeps one free-form memory document. Do not assume any extra experience tables exist. "
        "Whether to call `read_memory` is your decision; only do so when prior experience can materially reduce uncertainty. "
        "Only after extracting reusable profitable experience, failed lessons, position discipline, or debate-process improvement rules should you call `write_memory` to rewrite the whole memory document; if there is no new reusable lesson, you may skip all memory writes. "
        "Before calling `write_memory`, distill 1-3 self-contained, high-density lessons and avoid vague wording. "
        "The memory tools are already bound to the current stock. Only stock-bound memory is supported here, general memory is not supported, and you must not try to pass `stock_code`. "
        "\nMemory write protocol:\n"
        "1. Read before write: before calling `write_memory`, call `read_memory` to get the latest full text, and pass its `version` as `base_version`.\n"
        "2. Whole-document replacement: `content` must be the complete rewritten document; anything not included in `content` is permanently discarded, so preserve still-valid historical experience and merge this review's new lessons into it.\n"
        "3. Free-form structure: you organize the document yourself with no fixed template; prefer time-ordered sections with decision/review dates, preserved posterior return and signal-validation evidence, and merged outdated or duplicated content.\n"
        "4. Content elements: new lessons must include both the real stock name and stock code, and clearly cover the object, trading frequency, trading strategy, original PM correctness, actual post-decision outcome, hindsight evaluation of `take_profit` and `holding_horizon_days`, dominant drivers, validated signals, falsified signals, reusable rules, failure conditions, and applicability boundaries; you must annotate decision time, review time, and review horizon inside the document yourself. If trading frequency or strategy cannot be confirmed, state the missing field in the content.\n"
        "5. Size limit: when the document exceeds the system limit the write fails; consolidate outdated content in the same turn and retry instead of giving up.\n"
        "6. Version conflict: when the write fails and returns the latest full text, merge your new lessons into it and retry with the returned version; never overwrite content added by other sources.\n"
        "7. Write count: one `write_memory` call is normally enough for this review; only call it again when a supplement is truly needed.\n"
        "If you call `write_memory`, the resulting document must directly capture the reusable lesson, executable rule, and applicability boundary from this review instead of merely repeating verdict labels. "
        "Do not write generic background information or diary-style notes into memory. "
        "The final JSON contains only the three fields in the schema; the system retains the tool invocation trace separately, so do not duplicate it in the JSON. "
        "Do not output markdown or extra explanation; return only a strictly valid JSON object. "
        f"{skills_prompt_suffix}"
        f"Final JSON Schema: {schema}"
    )


async def review_debate_conclusion(state: ExperienceWorkflowState) -> Dict[str, Any]:
    """复盘单次 debate / PM 决策并返回结构化经验分析。

    Args:
        state: 经验复盘工作流状态，包含目标股票、交易配置、复盘上下文和回调信息。

    Returns:
        复盘分析结果、工具调用轨迹、复盘事件和错误列表。
    """
    if state.get("errors"):
        return {}

    stock_code = state["stock_code"]
    stock_name = state.get("stock_name") or stock_code
    industry = state.get("industry") or ""
    style_bucket = state["style_bucket"]
    trading_frequency = state.get("trading_frequency") or ""
    trading_strategy = state.get("trading_strategy") or ""
    full_context = state.get("full_context") or {}

    memory_tools = build_memory_tools(
        state={
            "agent_role": AGENT_NAME_PORTFOLIO_MANAGER,
            "user_id": state.get("user_id"),
            "stock_code": stock_code,
            "session_id": state.get("session_id"),
            "trading_strategy": trading_strategy,
            "trading_frequency": trading_frequency,
        }
    )
    skills_catalog_prompt = build_skills_catalog_prompt()
    skills_prompt_suffix = f"\n\n{skills_catalog_prompt}" if skills_catalog_prompt else ""
    tools = [*get_all_tools(), *memory_tools, *get_skills_loader_tools()]
    tool_map = {tool_obj.name: tool_obj for tool_obj in tools}
    llm_provider = get_llm_provider()
    raw_llm = llm_provider.build_chat_model(
        model=settings.LLM_MODEL,
        temperature=0.2,
    )
    llm = raw_llm.bind_tools(tools)

    messages: List[Any] = [
        SystemMessage(content=_build_review_system_prompt(skills_prompt_suffix)),
        HumanMessage(
            content=stable_json_dumps(
                {
                    "task": "Review one existing debate conclusion. Focus on why the stock actually rose/fell after the PM decision, what reusable price-move experience can be extracted, and how the debate flow should improve.",
                    "session_id": state.get("session_id"),
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "industry": industry,
                    "style_bucket": style_bucket,
                    "trading_frequency": trading_frequency,
                    "trading_strategy": trading_strategy,
                    "review_input": full_context,
                },
            )
        ),
    ]

    tool_trace: list[dict[str, Any]] = []
    review_events: list[dict[str, Any]] = []

    try:
        for iteration in range(EXPERIENCE_REVIEW_MAX_ITERATIONS):
            logger.info(
                "experience debate review iteration=%s session=%s stock=%s",
                iteration + 1,
                state.get("session_id"),
                stock_code,
            )
            response = await llm.ainvoke(messages)
            cache_lane, api_key_alias = get_research_usage_lane()
            await record_llm_usage(
                response,
                settings.LLM_MODEL,
                "experience_debate_review",
                workflow="experience_review",
                stage="tool_loop",
                call_kind="agent",
                iteration_index=iteration + 1,
                cache_lane=cache_lane,
                api_key_alias=api_key_alias,
            )
            response, invalid_tool_calls = llm_provider.sanitize_tool_call_response_for_replay(response)
            messages.append(response)

            if response.tool_calls or invalid_tool_calls:
                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_func = tool_map.get(tool_name)
                    tool_args = make_json_serializable(tool_call["args"])
                    tool_trace_entry = {"name": tool_name, "args": tool_args}
                    tool_trace.append(tool_trace_entry)
                    await _push_review_update(
                        state,
                        stage="tool_call",
                        status="running",
                        message_key="experience.live_messages.tool_call",
                        message_params={"tool": tool_name},
                        payload={
                            "tool_name": tool_name,
                            "args": tool_args,
                            "is_key_step": tool_name == "write_memory",
                            "index": len(tool_trace),
                        },
                    )
                    review_events.append(
                        {
                            "event_type": "experience_review_update",
                            "stage": "tool_call",
                            "status": "running",
                            "message_key": "experience.live_messages.tool_call",
                            "message_params": {"tool": tool_name},
                            "payload": {
                                "tool_name": tool_name,
                                "args": tool_args,
                                "is_key_step": tool_name == "write_memory",
                                "index": len(tool_trace),
                            },
                        }
                    )

                    if not tool_func:
                        messages.append(
                            ToolMessage(
                                tool_call_id=tool_call["id"],
                                content=stable_json_dumps({"error": f"unsupported tool: {tool_name}"}),
                            )
                        )
                        continue

                    tool_result = await tool_func.ainvoke(tool_args)
                    tool_payload = stable_json_dumps(make_json_serializable(tool_result))
                    if tool_name == "write_memory":
                        if isinstance(tool_result, dict):
                            tool_trace_entry["result"] = {
                                "success": tool_result.get("success"),
                                "status": tool_result.get("status"),
                                "memory_id": tool_result.get("memory_id"),
                                "stock_code": tool_result.get("stock_code"),
                                "error": tool_result.get("error"),
                                "version": tool_result.get("version"),
                                "size_chars": tool_result.get("size_chars"),
                                "max_chars": tool_result.get("max_chars") or settings.MEMORY_DOC_MAX_CHARS,
                            }
                    if should_summarize_tool_output(tool_name, tool_payload):
                        tool_payload = await summarize_tool_output(
                            raw_llm,
                            role_name="experience_debate_review",
                            tool_name=tool_name,
                            content=tool_payload,
                            tool_args=tool_args,
                            workflow="experience_review",
                            stage="tool_summary",
                            iteration_index=iteration + 1,
                        )
                    messages.append(
                        ToolMessage(
                            tool_call_id=tool_call["id"],
                            content=tool_payload,
                        )
                    )

                if invalid_tool_calls:
                    messages.append(
                        HumanMessage(
                            content=llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls)
                        )
                    )
                continue

            validated_output = _parse_experience_output(response.content)
            if validated_output is not None:
                return {
                    "analysis_payload": _build_experience_analysis_payload(
                        validated_output,
                        tool_trace,
                    ),
                    "tool_trace": tool_trace,
                    "review_events": review_events,
                    "errors": [],
                }

            logger.warning(
                "experience review output failed structured validation "
                "iteration=%s session=%s stock=%s content_type=%s",
                iteration + 1,
                state.get("session_id"),
                stock_code,
                type(response.content).__name__,
            )
            messages.append(
                HumanMessage(
                    content=(
                        "你的输出未通过结构化校验。请严格按给定 JSON Schema 返回对象，不要输出 markdown，不要输出额外解释。"
                        "Your output did not pass structured validation. Return only a valid JSON object."
                    )
                )
            )

        fallback_result = await _retry_final_experience_json(
            raw_llm=raw_llm,
            llm_provider=llm_provider,
            messages=messages,
            tool_trace=tool_trace,
            review_events=review_events,
            session_id=state.get("session_id"),
            stock_code=stock_code,
        )
        if fallback_result is not None:
            return fallback_result
    except Exception as exc:
        logger.exception("experience review_debate_conclusion failed")
        return {
            "tool_trace": tool_trace,
            "review_events": review_events,
            "errors": [f"review_debate_conclusion failed: {exc}"],
        }

    return {
        "tool_trace": tool_trace,
        "review_events": review_events,
        "errors": ["review_debate_conclusion failed: max iterations reached without valid structured output"],
    }


def should_continue_after_context(state: ExperienceWorkflowState) -> str:
    if state.get("errors"):
        return END
    return "review_debate_conclusion"


def create_experience_workflow():
    workflow = StateGraph(ExperienceWorkflowState)
    workflow.add_node("fetch_full_context", fetch_full_context)
    workflow.add_node("review_debate_conclusion", review_debate_conclusion)
    workflow.set_entry_point("fetch_full_context")
    workflow.add_conditional_edges(
        "fetch_full_context",
        should_continue_after_context,
        {
            END: END,
            "review_debate_conclusion": "review_debate_conclusion",
        },
    )
    workflow.add_edge("review_debate_conclusion", END)
    return workflow.compile()
