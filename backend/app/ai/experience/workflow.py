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


class SignalReviewItem(BaseModel):
    """描述单个被验证或被证伪的复盘信号。"""

    signal: str
    evidence: str = ""
    impact: str = Field(default="medium", pattern="^(low|medium|high)$")
    lesson: str = ""


class NoiseSignalItem(BaseModel):
    """描述对实际涨跌贡献较低的噪音信号。"""

    signal: str
    reason: str = ""


class OriginalJudgmentReview(BaseModel):
    """描述原始 PM 判断相对市场结果的正确性。"""

    verdict: str = Field(pattern="^(correct|partially_correct|incorrect|inconclusive)$")
    score: float = Field(ge=0, le=100)
    pm_decision: str = ""
    outcome_basis: str = ""
    reasoning: str = ""


class SignalValidationReview(BaseModel):
    """按验证、证伪和噪音三类组织信号复盘结果。"""

    validated_signals: List[SignalReviewItem] = Field(default_factory=list)
    invalidated_signals: List[SignalReviewItem] = Field(default_factory=list)
    noise_signals: List[NoiseSignalItem] = Field(default_factory=list)


class DecisionProcessImprovementReview(BaseModel):
    """描述后续 debate、PM 决策和风控流程的改进项。"""

    debate_changes: List[str] = Field(default_factory=list)
    pm_changes: List[str] = Field(default_factory=list)
    risk_control_changes: List[str] = Field(default_factory=list)


class ExperienceReviewTriads(BaseModel):
    """承载经验复盘必须输出的三件套结构。"""

    original_judgment: OriginalJudgmentReview
    signal_validation: SignalValidationReview
    decision_process_improvement: DecisionProcessImprovementReview


class ExperienceTags(BaseModel):
    """承载经验复盘结果的展示和筛选标签。"""

    stock_tags: List[str] = Field(default_factory=list)
    industry_tags: List[str] = Field(default_factory=list)
    strategy_tags: List[str] = Field(default_factory=list)
    failure_lesson_tags: List[str] = Field(default_factory=list)
    position_discipline_tags: List[str] = Field(default_factory=list)
    signal_tags: List[str] = Field(default_factory=list)
    market_regime_tags: List[str] = Field(default_factory=list)


class ExperienceReviewOutput(BaseModel):
    thesis_summary: str = Field(
        description="用 2-4 句总结原始 PM 结论是否正确、股票实际涨跌主因，以及最重要的复盘结论。 / Summarize in 2-4 sentences whether the original PM conclusion was correct, what mainly drove the stock move, and the key review takeaway."
    )
    recommended_action: str = Field(pattern="^(avoid|watch|buy|add|hold|reduce|sell)$")
    confidence_score: float = Field(ge=0, le=100)
    risk_flags: List[str] = Field(default_factory=list)
    memory_evidence_used: List[str] = Field(default_factory=list)
    similar_success_patterns: List[str] = Field(
        default_factory=list,
        description="过去类似上涨或成功案例中真正有效的模式。 / Patterns that truly worked in similar rising or successful cases."
    )
    similar_failure_patterns: List[str] = Field(
        default_factory=list,
        description="过去类似下跌或失败案例中经常导致判断失效的模式。 / Patterns that often led to failure in similar falling or failed cases."
    )
    lessons_applied: List[str] = Field(default_factory=list)
    current_case_vs_history: str = ""
    why_this_is_not_blind_guess: str = ""
    action_plan: str = ""
    entry_plan: str = ""
    exit_plan: str = ""
    position_management: str = ""
    profit_hypothesis: str = ""
    market_experience_summary: str = Field(
        default="",
        description="必须总结这次股票为什么涨或跌，区分被验证信号、被证伪信号，并提炼可复用的涨跌经验。 / Must explain why the stock rose or fell, separate validated vs falsified signals, and extract reusable price-move experience."
    )
    dominant_drivers: List[str] = Field(
        default_factory=list,
        description="本次涨跌最主要的 1-3 个驱动因素。 / The top 1-3 dominant drivers behind the price move."
    )
    rejected_drivers: List[str] = Field(
        default_factory=list,
        description="被讨论过但最终不构成主因的伪因或噪音。 / Candidate drivers that were considered but rejected as noise or non-dominant."
    )
    driver_dimension_review: List[str] = Field(
        default_factory=list,
        description="按维度逐项复盘，例如 政策/行业/国际局势/业绩/估值/资金/情绪/事件/商品价格/利率汇率，并说明证据与影响。 / Dimension-by-dimension review across policy, industry, geopolitics, earnings, valuation, flow, sentiment, events, commodities, rates/FX, with evidence and impact."
    )
    buy_sell_rules: List[str] = Field(
        default_factory=list,
        description="每条都用“触发条件 -> 动作 -> 原因”格式，写成未来可执行的买卖规则。 / Each rule must follow 'trigger -> action -> reason' so it is executable in future cases."
    )
    internet_evidence_used: List[str] = Field(default_factory=list)
    review_triads: ExperienceReviewTriads
    experience_tags: ExperienceTags = Field(default_factory=ExperienceTags)
    debate_correctness: str = Field(pattern="^(correct|partially_correct|incorrect|inconclusive)$")
    correctness_score: float = Field(ge=0, le=100)
    correctness_reasoning: str = Field(
        default="",
        description="基于决策后的价格路径、回撤和驱动因素，解释原始 PM 结论为什么对或错。 / Explain why the original PM conclusion was right or wrong based on post-decision price path, drawdown, and drivers."
    )
    debate_process_issues: List[str] = Field(default_factory=list)
    optimization_directions: List[str] = Field(default_factory=list)
    improved_debate_rules: List[str] = Field(default_factory=list)
    process_improvement_summary: str = ""
    revised_target_position: Optional[float] = Field(default=None, ge=0, le=1)
    revised_stop_loss: str = ""
    reviewed_pm_decision: str = ""
    original_pm_decision: str = ""
    original_target_position: Optional[float] = Field(default=None, ge=0, le=1)


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
        if not content:
            continue
        stock_code = str(result.get("stock_code") or "").strip() or None
        memo_session = str(result.get("memo_session") or "stock").strip() or "stock"
        importance = str(args.get("importance") or "medium").strip().lower()
        if importance not in {"low", "medium", "high"}:
            importance = "medium"
        item: dict[str, Any] = {
            "content": content,
            "importance": importance,
            "memo_session": memo_session,
            "stock_code": stock_code,
        }
        for key in ("status", "observation_id", "source_id", "error"):
            value = result.get(key)
            if value not in (None, ""):
                item[key] = value
        items.append(item)
    return items


def _build_experience_analysis_payload(
    validated_output: ExperienceReviewOutput,
    tool_trace: List[Dict[str, Any]],
    internet_tools_used: set[str],
) -> Dict[str, Any]:
    payload = validated_output.model_dump(mode="python")
    payload["tool_invocation_summary"] = tool_trace
    payload["internet_tools_used"] = sorted(internet_tools_used)
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
    internet_tools_used: set[str],
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
        record_llm_usage(
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
                    internet_tools_used,
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
            "你是一名 A 股投研复盘分析师。你的任务不是重新做一遍普通选股，而是复盘现有 debate / PM 结论："
            "1. 评估这次 PM 结论在市场结果上是否正确；"
            "2. 找出这只股票在决策后阶段为什么上涨/下跌，哪些因素是真正主导驱动，哪些只是噪音；"
            "3. 找出 debate 流程中遗漏的信息、错误的推理、仓位或卖出设计问题；"
            "4. 给出可复用的涨跌经验、交易规则与流程优化规则，让后续 debate 更容易得出赚钱的结论。"
            "你的主输入只有：各个 agent 的辩论 timeline 结论、PM 的交易相关字段、执行结果、以及决策后的市场结果。"
            "你必须把 market_outcome_summary 中的收益、回撤、相对收益结果作为核心证据输入。"
            "凡是关于决策时点的历史事实，包括 timeline、PM 字段、执行结果、价格路径、收益和回撤，一律以输入里的 review_input 为准。"
            "工具调用拿到的是当前时点的实时或补充信息，可能与决策时点不同，只能用于补充解释和验证原因，不能覆盖、改写或否定输入中的历史事实。"
            "你的核心工作不是复述各个 agent 说了什么，而是判断：哪些论点真正解释了后验价格路径，哪些论点没有被市场验证。"
            "分析“股票为什么涨跌”时，优先从这些维度归因：政策、业绩、估值修复或杀估值、资金流与成交结构、板块 Beta 与指数环境、商品价格与成本、情绪催化、事件驱动、预期差修正。"
            "如果多个因素都相关，你必须指出 1-3 个主导因素，并说明它们如何对应到价格路径和回撤。"
            "如果当前上下文不足以解释涨跌原因，可以调用外部工具补证据；但如果已有证据足够，不要机械搜索。"
            "历史经验只能通过记忆工具读取和写入，不要假设有额外的经验表可用。"
            "是否调用 `recall_memory` 由你自己决定。只有当历史经验确实能降低当前不确定性时，才调用它；不要机械调用。"
            "是否调用互联网或其他工具也由你自己决定，但你必须把股票涨跌的主要驱动原因查清楚，并在正确性解释里说明清楚。"
            "你必须显式区分：A. 被市场验证的信号；B. 被市场证伪的信号；C. 虽然说得有道理，但对实际涨跌贡献不大的噪音信号。"
            "你必须做一次多维原因检查，至少逐项检查这些维度：国家政策/监管、行业景气度与板块强弱、国际局势与宏观环境、业绩与基本面、估值、资金流与成交结构、市场情绪、事件催化、商品价格或成本、利率/汇率。"
            "不能只盯一两个技术或量价指标；即使某个维度最终不重要，也要明确说明它为什么不是主因。"
            "`dominant_drivers` 只写 1-3 个真正主导涨跌的因素。"
            "`rejected_drivers` 写那些被讨论过但最终不构成主因的因素。"
            "`driver_dimension_review` 要按“维度 -> 证据 -> 影响 -> 结论”逐项写，尽量覆盖上面的多维检查。"
            "market_experience_summary 必须写成经验，不是摘要。至少包含：实际涨跌主因、被验证信号、被证伪信号、可复用经验。"
            "buy_sell_rules 必须写成未来可执行规则，而不是抽象观点。"
            "只有在总结出可复用的赚钱经验、失败教训、仓位纪律或 debate 流程改进规则后，才调用 `write_memory` 写入记忆；如果没有新增可复用经验，可以跳过全部记忆写入。"
            "在调用 `write_memory` 之前，先把本次复盘提炼成 1-3 条可独立复用的高信息密度经验教训；每条都应能单独成立，避免空泛套话。"
            "记忆工具已自动绑定到当前股票，只允许写入当前股票记忆，不支持通用记忆，也不要尝试传入 `stock_code`。"
            "\n记忆写入协议:\n"
            "1. 写入前提: 在调用 `write_memory` 前，先判断哪些主题有新增经验；没有新增经验的主题可以跳过。\n"
            "2. 内容要素: 写入记忆时，`content` 必须同时包含真实股票名和股票代码，并清楚覆盖本次对象、交易频率、交易策略、原始 PM 结论正确性、决策后实际涨跌结果、主导驱动、多维原因拆解、被验证信号、被证伪信号、可复用规则、未来 Debate / PM / 风控检查项、失效条件/边界；若交易频率或交易策略无法确认，必须在正文中说明缺失。\n"
            "3. 推荐写入顺序:\n"
            "3.1 [MEMORY_TOPIC: decision_outcome]: 如果原始 PM 结论有明确后验结果，记录原始决策、目标仓位、置信度、止损/加仓计划、后续收益/回撤/相对收益和结论正确性。\n"
            "3.2 [MEMORY_TOPIC: driver_validation]: 如果能区分被验证、被证伪和噪音信号，记录主导驱动、被验证信号、被证伪信号、噪音信号和被排除伪因。\n"
            "3.3 [MEMORY_TOPIC: risk_control]: 如果仓位、止损、`buy`/`sell`/`hold` 或回撤管理有教训，记录仓位大小、买入/卖出/持有条件、止损是否缺失/失效、流动性、板块 Beta、事件落地和失效条件。\n"
            "3.4 [MEMORY_TOPIC: strategy_fit]: 如果经验的适用频率、策略或市场环境存在明显边界，记录适用的交易频率、交易策略、市场环境、失效环境和经验是否过时及原因。\n"
            "3.5 [MEMORY_TOPIC: process_improvement]: 如果能提炼出未来 Debate / PM / Risk 的流程检查项，记录哪个 Agent 要补什么证据、哪类推理错误要避免、PM 如何调整仓位/置信度/卖出设计、Risk Control 要检查哪些否决条件。\n"
            "4. 拆分规则: 一条 Memory 只写一个主主题；不同主题必须分次调用 `write_memory`，不要把多个主题揉成一条 Memory。每个 `write_memory` 调用只承载一个主题，主题之间不要互相夹带。\n"
            "5. 推荐结构: 单条 Memory 正文建议包含 [MEMORY_TOPIC: ...]、对象:、交易频率:、交易策略:、场景:、经验:、触发条件:、未来动作:、失效边界:、证据:。对象必须同时包含真实股票名和股票代码。\n"
            "6. 写入质量: 复盘写入必须包含后验市场结果或信号验证证据；不要把整个复盘表格原样塞进记忆；应提炼成高信息密度、可召回、可执行的经验正文。\n"
            "7. 适用边界: 每条经验必须说明适用的交易频率和交易策略；每条改进都必须说明触发条件、未来 PM 或 Agent 要检查的证据，以及历史经验在什么边界下不再适用。\n"
            "如果调用 `write_memory`，至少一条写入记忆必须直接总结本次复盘得到的经验教训与可执行规则，而不是只重复结论标签。"
            "不要把普通背景信息和流水账写入记忆。"
            "最终结论必须明确区分：原始 PM 决策、你复盘后的改进动作、以及 debate 流程该如何优化。"
            "decision_process_improvement 必须写成给未来 Debate / PM 可直接执行的流程检查项，不能只是抽象建议。"
            "不要预设固定问题清单；必须从本次复盘证据和召回记忆中归纳真正反复出现、导致判断失效或执行偏差的问题。"
            "如果证据显示某类信号需要额外确认、某种行业或市场比较被忽视、仓位纪律不足、止损或反转条件缺失，才把它写成未来检查项。"
            "复盘提炼经验时必须说明它适用的交易频率和交易策略；不同频率或策略下可能不适用，不要把经验无条件推广。"
            "每条改进都必须说明触发条件、未来 PM 或 Agent 要检查的证据，以及历史经验在什么边界下不再适用。"
            "最终 JSON 必须包含 `review_triads`。其中 `original_judgment` 判断原始 PM 是否正确，"
            "`signal_validation` 明确列出被验证信号、被证伪信号和噪音信号，"
            "`decision_process_improvement` 明确列出下次 debate、PM 决策和风控要改什么。"
            "最终 JSON 还应包含 `experience_tags`，用于经验库展示筛选。"
            "不要输出 markdown，不要输出额外解释，只返回严格合法的 JSON 对象。"
            f"{skills_prompt_suffix}"
            f"最终 JSON Schema: {schema}"
        )
    return (
        "You are an A-share review analyst. Your task is not to rerun ordinary stock picking, but to review an existing debate / PM conclusion:"
        "1. Judge whether the PM conclusion was correct in terms of market outcome; "
        "2. explain why the stock rose or fell after the decision, separating real drivers from noise; "
        "3. identify missing information, flawed reasoning, position-sizing mistakes, or sell-design problems in the debate process; "
        "4. extract reusable market experience, trading rules, and process improvements that can help future debates make more profitable conclusions. "
        "Your main input only contains the agents' timeline conclusions, PM trading fields, execution outcome, and post-decision market outcome. "
        "You must treat the returns, drawdowns, and relative-performance fields in `market_outcome_summary` as core evidence. "
        "For any historical fact about the decision-time state, including the timeline, PM fields, execution result, price path, returns, and drawdowns, the `review_input` must be treated as the source of truth. "
        "Any tool output is current-time or supplementary information and may differ from the decision-time state, so it may only be used for explanation or corroboration and must never overwrite the historical facts in the input. "
        "Your job is not to restate each agent, but to determine which arguments truly explain the later price path and which were not validated by the market. "
        "When explaining why the stock moved, prioritize attribution across policy, earnings, valuation rerating or derating, flow and trading structure, sector beta and index environment, commodity costs, sentiment catalyst, event-driven moves, and expectation reset. "
        "If several factors matter, identify the top 1-3 dominant drivers and connect them to the price path and drawdown. "
        "If the current context is insufficient to explain the move, you may call external tools for evidence; otherwise do not search mechanically. "
        "Historical experience can only be read or written through memory tools; do not assume any extra experience tables exist. "
        "Whether to call `recall_memory` is your decision; only do so when prior experience can materially reduce uncertainty. "
        "Whether to call internet or other tools is also your decision, but you must clearly identify the main drivers of the stock move and explain them in correctness analysis. "
        "You must explicitly separate: A. validated signals; B. falsified signals; C. noisy signals that sounded plausible but contributed little to the actual move. "
        "You must perform a multi-dimensional driver check that covers at least: national policy/regulation, industry cycle and sector strength, international situation and macro backdrop, earnings/fundamentals, valuation, capital flow and trading structure, market sentiment, event catalysts, commodity prices/costs, and rates/FX. "
        "Do not fixate on one or two technical or price/volume indicators; even when a dimension is not important, explain why it was not a dominant driver. "
        "`dominant_drivers` should contain only the 1-3 true dominant causes of the move. "
        "`rejected_drivers` should capture factors that were considered but ultimately rejected as non-dominant. "
        "`driver_dimension_review` should be written in the format 'dimension -> evidence -> impact -> conclusion' and should reflect the multi-factor scan above. "
        "`market_experience_summary` must be written as reusable experience, not as a generic summary. It must include actual move drivers, validated signals, falsified signals, and reusable lessons. "
        "`buy_sell_rules` must be executable future rules, not abstract opinions. "
        "Only after extracting reusable profitable experience, failed lessons, position discipline, or debate-process improvement rules should you call `write_memory`; if there is no new reusable lesson, you may skip all memory writes. "
        "Before calling `write_memory`, distill the review into 1-3 self-contained, high-density lessons that can stand on their own and avoid vague wording. "
        "The memory tools are already bound to the current stock. Only stock-bound memory is supported here, general memory is not supported, and you must not try to pass `stock_code`. "
        "\nMemory write protocol:\n"
        "1. Write precondition: before calling `write_memory`, decide which topics contain new lessons; topics without new lessons may be skipped.\n"
        "2. Content elements: memory `content` must include both the real stock name and stock code, and clearly cover the object, trading frequency, trading strategy, original PM correctness, actual post-decision outcome, dominant drivers, multi-factor driver decomposition, validated signals, falsified signals, reusable rules, future Debate / PM / risk-control checklist items, and failure conditions or boundaries. If trading frequency or strategy cannot be confirmed, state the missing field in the content.\n"
        "3. Recommended write order:\n"
        "3.1 [MEMORY_TOPIC: decision_outcome]: if the original PM conclusion has clear later outcome evidence, record the original decision, target size, confidence, stop/add plan, later return/drawdown/relative return, and correctness.\n"
        "3.2 [MEMORY_TOPIC: driver_validation]: if validated, falsified, and noisy signals can be separated, record dominant drivers, validated signals, falsified signals, noisy signals, and rejected false causes.\n"
        "3.3 [MEMORY_TOPIC: risk_control]: if sizing, stop-loss, `buy`/`sell`/`hold`, or drawdown control produced a lesson, record sizing, buy/sell/hold conditions, missing/failed stops, liquidity, sector beta, event realization, and invalidation conditions.\n"
        "3.4 [MEMORY_TOPIC: strategy_fit]: if the lesson has clear frequency, strategy, or market-regime boundaries, record applicable trading frequency, strategy, market regime, invalidation regime, and whether the lesson is stale and why.\n"
        "3.5 [MEMORY_TOPIC: process_improvement]: if future Debate / PM / Risk checklist items can be extracted, record which Agent should verify what evidence, which reasoning error to avoid, how PM should adjust sizing/confidence/sell design, and which veto checks Risk Control must run.\n"
        "4. Split rule: one Memory must carry one primary topic only. Different topics must use separate `write_memory` calls; do not mix multiple topics into one Memory. Each `write_memory` call must carry only one topic, and topics must not be bundled together.\n"
        "5. Recommended structure: each Memory body should contain [MEMORY_TOPIC: ...], Object:, Trading frequency:, Trading strategy:, Scenario:, Lesson:, Trigger conditions:, Future action:, Invalidation boundary:, and Evidence:. Object must include both the real stock name and stock code.\n"
        "6. Write quality: review writes must include later market outcome or signal-validation evidence. Do not copy the full review into memory; distill it into high-density, retrievable, executable experience text.\n"
        "7. Applicability boundary: each lesson must state the trading frequency and strategy it applies to. Each improvement must state the trigger condition, the evidence future PM or agents must check, and the boundary where the historical lesson no longer applies.\n"
        "If you call `write_memory`, at least one memory write must directly capture the reusable lesson and executable rule from this review instead of merely repeating verdict labels. "
        "Do not write generic background information or diary-style notes into memory. "
        "Your final answer must clearly distinguish the original PM decision, your revised action after review, and how the debate flow should improve. "
        "`decision_process_improvement` must be written as concrete future Debate / PM checklist items, not abstract advice. "
        "Do not assume a fixed issue checklist; derive the recurring problems that truly caused judgment failures or execution drift from the current review evidence and recalled memory. "
        "Only when the evidence shows that a signal needs extra confirmation, an industry or market comparison was ignored, position discipline was weak, or stop-loss or reversal conditions were missing should it become a future checklist item. "
        "The final JSON must contain `review_triads`: `original_judgment` judges whether the original PM was correct, "
        "`signal_validation` lists validated, invalidated, and noisy signals, and "
        "`decision_process_improvement` lists concrete debate, PM, and risk-control changes for next time. "
        "The final JSON should also contain `experience_tags` for experience-library filtering. "
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

    internet_tools_used: set[str] = set()
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
            record_llm_usage(
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
                    if tool_name == "search_news":
                        internet_tools_used.add(tool_name)
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

                    tool_result = await tool_func.ainvoke(tool_call["args"])
                    tool_payload = stable_json_dumps(make_json_serializable(tool_result))
                    if tool_name == "write_memory":
                        if isinstance(tool_result, dict):
                            tool_trace_entry["result"] = {
                                "success": tool_result.get("success"),
                                "status": tool_result.get("status"),
                                "observation_id": tool_result.get("observation_id"),
                                "source_id": tool_result.get("source_id"),
                                "memo_session": tool_result.get("memo_session"),
                                "stock_code": tool_result.get("stock_code"),
                                "error": tool_result.get("error"),
                            }
                    if should_summarize_tool_output(tool_name, tool_payload):
                        tool_payload = await summarize_tool_output(
                            raw_llm,
                            role_name="experience_debate_review",
                            tool_name=tool_name,
                            content=tool_payload,
                            tool_args=tool_call["args"],
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
                        internet_tools_used,
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
            internet_tools_used=internet_tools_used,
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
