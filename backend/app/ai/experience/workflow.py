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
        description="原始 PM 结论在所选复盘周期和 PM 持有期内的正确性；最新交易日结果只作补充。 / Correctness of the original PM conclusion over the selected review horizon and the PM holding horizon; the latest trading-day outcome is supporting context."
    )
    correctness_reasoning: str = Field(
        default="",
        description="用 2-4 句说明所选复盘周期和 PM 原持有期内的判断是否正确，再补充最新交易日的后续验证、主要涨跌原因和可复用教训。`debate_correctness` 以所选周期和 PM 持有期为主，最新交易日结果只作补充。不要重复记忆文档全文。 / In 2-4 concise sentences, explain whether the decision was correct over the selected review horizon and the PM holding horizon, then add the latest trading-day follow-through, key price driver, and reusable lesson. Judge correctness mainly on the selected horizon and the holding horizon; treat the latest outcome as supporting context. Do not repeat the memory document."
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
            "orders": review_context.get("orders") or [],
            "trades": review_context.get("trades") or [],
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
            "你只需要完成三项输出：原始 PM 动作、结论正确性、以及 2-4 句简短复盘解释。"
            "解释必须同时覆盖所选复盘周期、PM 原持有期和最新交易日的后验结果，以及最主要的涨跌原因和一条可复用教训；不要复述 timeline，不要输出记忆文档全文。"
            "你的主输入是 agent timeline、PM 交易字段、`orders`/`trades` 原始订单成交，以及 `market_outcome_summary` 中的 `review_window`、`holding_window`、`latest_window` 和 `benchmark_window` 原始行情。"
            "充分调研是硬性要求：不能只看固定窗口或记忆文档。必须核对最新交易日行情、决策后关键价格路径、止盈止损、持有期到期状态，以及 60d 之后到最新交易日的走势，并在需要时用新闻、政策、行业、资金或财务工具交叉验证。"
            "决策时点的历史事实，包括 timeline、PM 字段、`orders`/`trades` 和原始价格路径，一律以 review_input 为准，不要用工具返回改写这些原始数据。最新行情、财务、新闻等当前事实以 `latest_window` 和工具返回为准，可以用它们评估历史决策的后续结果，但不得用旧窗口结果否定最新事实。收益、回撤和相对收益由你基于原始数据自行计算。"
            "`debate_correctness` 以所选复盘周期和 PM 原持有期为主，判断决策当时及持有期内的判断是否正确；最新交易日结果只用于补充后续验证、解释涨跌和提炼教训，不改变所选周期的主判定口径。如果最新结果与所选周期结论明显冲突，在 correctness_reasoning 中说明日期、价格和差异；止损/止盈若在 PM 持有期内触发，应计入持有期判断，持有期之后才触发则只作后续验证，不改写主判定。"
            "后验检查 PM 的止盈、止损和持有周期是否匹配实际价格路径；如果发现退出纪律或周期明显不匹配，把结论压缩进 correctness_reasoning。系统固定的 5d/20d/60d 复盘周期保留，但不得以固定周期为由忽略最新数据。"
            "系统只提供数据库原始数据，不提供任何计算好的收益、回撤、相对收益或持有期结果。所有计算都必须由你基于 `review_window`、`holding_window`、`latest_window`、`benchmark_window` 的原始行自行完成，优先调用 `execute_python_sandboxed` 或 `query_and_calculate`，不要心算。"
            "`data_range` 和 `time_range` 只描述本次原始数据的覆盖范围，用来判断数据是否完整或落后，不要把它们当成收益或结果。`review_window` 是决策日到所选复盘日（第 5/20/60 个交易日）的原始日 K：5D 含 6 根、20D 含 21 根、60D 含 61 根。`holding_window` 是决策日到 PM 持有期到期后第一个交易日的原始日 K。`latest_window` 是最新最多 30 根原始日 K，只用于后续验证，不得和 `review_window` 混用。"
            "入场价规则：有买入成交时以 `trades` 的成交价为准；没有成交时以决策日次日开盘价为准。`orders`/`trades` 是原始订单和成交，成交均价、费用和已实现盈亏由你自行计算。指数相对收益使用 `benchmark_window` 的沪深300原始行，按相同交易日对齐；缺少对应日期时不得补前值或后值。"
            "需要更长路径时，先用 `get_current_time` 判断时效，必要时用 `sync_market_data` 同步当前股票的 `daily_kline` 或 `realtime`，再用 `query_stock_data` 查询。"
            "分析涨跌原因时，优先判断政策、行业、宏观、业绩、估值、资金、板块环境、情绪、事件、商品成本和利率汇率等因素，区分真正主因、被验证信号、被证伪信号与噪音。"
            "即使最新行情快照可用，也要充分调研：至少核对最新交易日行情，并在需要时调用新闻、政策、行业、资金或财务工具交叉验证。避免机械搜索，但关键证据缺失时不得跳过调研。"
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
        "Return only three analytical fields: the original PM action, correctness, and a concise 2-4 sentence review explanation. "
        "The explanation must cover the selected review horizon, the PM holding horizon, and the latest trading-day outcome, plus the main price driver and one reusable lesson; do not repeat the timeline or the memory document. "
        "The input contains agent timeline conclusions, PM trading fields, raw `orders`/`trades`, and raw market rows in `market_outcome_summary`: `review_window`, `holding_window`, `latest_window`, and `benchmark_window`. "
        "Thorough research is mandatory: do not review only fixed windows or memory documents. Check the latest trading-day quote, the key post-decision price path, stop-loss/take-profit triggers, holding-horizon expiry, and the move after 60d through the latest trading day; cross-check with news, policy, industry, flow, or financial tools when needed. "
        "For decision-time historical facts, including the timeline, PM fields, raw `orders`/`trades`, and raw price rows, `review_input` is the source of truth; do not let tool output rewrite those raw facts. Current-time facts such as the latest market, financial, or news data must come from `latest_window` and tool results and may be used to evaluate what happened after the historical decision; never let an old fixed-window result override newer facts. Returns, drawdowns, and relative returns are your own calculations from the raw rows. "
        "The 5d/20d/60d periods remain fixed review slices. Judge `debate_correctness` mainly on the selected review horizon and the PM holding horizon: whether the decision was correct at the time and over the intended holding period. The latest trading-day outcome is supporting evidence for follow-through, driver analysis, and lessons; it must not change the primary verdict. If the latest outcome conflicts with the selected-horizon conclusion, state the date, price, and difference in `correctness_reasoning`. Stop-loss/take-profit triggers inside the PM holding period count toward the holding-period verdict; triggers after the holding period are follow-through evidence only and must not rewrite the primary verdict. "
        "Evaluate the PM's take-profit, stop-loss, and holding-period design against the actual price path. Compress any mismatch or missing exit discipline into `correctness_reasoning`; keep the fixed 5d/20d/60d review horizons, but never use them as a reason to ignore newer data. "
        "The system provides raw database rows only: no precomputed returns, drawdowns, relative returns, or holding-period results. Compute everything yourself from the raw rows in `review_window`, `holding_window`, `latest_window`, and `benchmark_window`, preferably with `execute_python_sandboxed` or `query_and_calculate`, not mental arithmetic. `data_range` and `time_range` describe only the coverage of the raw data; use them to judge completeness and freshness, not as returns or results. `review_window` is the raw daily bars from the decision day to the selected review day (the 5th/20th/60th trading day): 6 bars for 5D, 21 for 20D, and 61 for 60D. `holding_window` runs from the decision day to the first trading day after the PM holding deadline. `latest_window` is the latest 30 raw daily bars for follow-through only; do not mix it with `review_window`. Entry price rule: use `trades` fill prices when actual buys exist; otherwise use the next trading day's open. `orders`/`trades` are raw rows; compute average fill, fees, and realized PnL yourself. Benchmark relative returns use the CSI 300 raw rows in `benchmark_window` on the same trading dates; never fill missing index dates forward or backward. For a longer path, first check freshness with `get_current_time`, refresh with `sync_market_data` when needed, then query with `query_stock_data`. "
        "When explaining the move, check policy, industry, macro, earnings, valuation, flow, sector beta, sentiment, events, commodity costs, and rates/FX, separating true drivers, validated signals, falsified signals, and noise. "
        "Even when a latest market snapshot is available, research thoroughly: at minimum verify the latest trading-day quote and cross-check with news, policy, industry, flow, or financial tools when needed. Avoid mechanical searches, but never skip research when key evidence is missing. "
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
                    "task": "Review one existing debate conclusion using the selected horizon, PM holding horizon, and latest trading-day outcome. Focus on why the stock actually rose/fell after the PM decision, what reusable price-move experience can be extracted, and how the debate flow should improve.",
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
