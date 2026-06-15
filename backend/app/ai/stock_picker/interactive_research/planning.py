from __future__ import annotations

from typing import Any, Dict, List

from app.ai.stock_picker.interactive_research.constants import (
    DEFAULT_RESEARCH_DEPTH,
    DEFAULT_RISK_LEVEL,
    DEFAULT_SCOPE,
    DEFAULT_STYLE,
)


def parse_requirement(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """把 API 请求整理为计划阶段可审计的结构化需求。

    Args:
        request_data: API 请求数据。

    Returns:
        结构化需求字典。
    """
    requirement = str(request_data.get("requirement") or "").strip()
    scope = request_data.get("scope") or DEFAULT_SCOPE
    research_depth = request_data.get("research_depth") or DEFAULT_RESEARCH_DEPTH
    risk_level = request_data.get("risk_level") or DEFAULT_RISK_LEVEL
    style = request_data.get("style") or infer_style(requirement)
    hard_exclusions = ["ST", "*ST", "delisting risk", "abnormal trading status", "non-A-share common stock"]
    if request_data.get("exclude_recent_ipos"):
        hard_exclusions.append("recent IPOs below the user-specified minimum listing days")

    return {
        "raw_requirement": requirement,
        "scope": scope,
        "research_depth": research_depth,
        "expected_count": int(request_data.get("expected_count") or 5),
        "risk_level": risk_level,
        "style": style,
        "allowed_industries": list(request_data.get("allowed_industries") or []),
        "excluded_industries": list(request_data.get("excluded_industries") or []),
        "exclude_recent_ipos": bool(request_data.get("exclude_recent_ipos") or False),
        "min_listing_days": request_data.get("min_listing_days"),
        "max_iterations": max(10, int(request_data.get("max_iterations") or 60)),
        "recent_ipo_risk_factor": not bool(request_data.get("exclude_recent_ipos") or False),
        "hard_exclusions": hard_exclusions,
        "open_questions": build_open_questions(request_data, scope, risk_level),
    }


def infer_style(requirement: str) -> str:
    """从自然语言需求中提取第一阶段的风格提示。

    Args:
        requirement: 用户原始自然语言需求。

    Returns:
        风格枚举值；无法判断时返回 balanced。
    """
    style_markers = {
        "growth": ["growth", "policy catalyst", "sector theme", "industry cycle"],
        "momentum": ["momentum", "trend", "breakout", "relative strength"],
        "value": ["value", "undervalued", "dividend", "valuation"],
        "defensive": ["defensive", "low drawdown", "stable", "low volatility"],
    }
    normalized = requirement.lower()
    for style, markers in style_markers.items():
        if any(marker in normalized for marker in markers):
            return style
    return DEFAULT_STYLE


def build_open_questions(request_data: Dict[str, Any], scope: str, risk_level: str) -> List[str]:
    """根据请求形成计划确认阶段可展示的缺口问题。

    Args:
        request_data: API 请求数据。
        scope: 股票池范围。
        risk_level: 风险偏好。

    Returns:
        需要用户在确认计划时留意的开放问题列表。
    """
    questions: List[str] = []
    if scope == "all" and not request_data.get("allowed_industries"):
        questions.append("The full-market scope is broad; AI factors will compress the candidate pool after approval.")
    if risk_level == "low" and request_data.get("style") == "growth":
        questions.append(
            "Low risk preference may conflict with growth upside; drawdown and overheat checks will be prioritized."
        )
    if not request_data.get("style"):
        questions.append(
            "No explicit style was provided; the initial style is inferred and can be adjusted in chat before approval."
        )
    return questions


def build_research_budget(parsed_requirement: Dict[str, Any]) -> Dict[str, Any]:
    """按研究深度和推荐数量生成研究预算。

    Args:
        parsed_requirement: 结构化需求。

    Returns:
        研究预算字典。
    """
    depth = parsed_requirement["research_depth"]
    expected_count = int(parsed_requirement.get("expected_count") or 5)
    max_iterations = max(10, int(parsed_requirement.get("max_iterations") or 60))
    budget_by_depth = {
        "light": {
            "max_human_rounds": 2,
            "estimated_tokens": "50k-150k",
            "estimated_duration": "5-15 min",
        },
        "standard": {
            "max_human_rounds": 5,
            "estimated_tokens": "200k-500k",
            "estimated_duration": "15-30 min",
        },
        "deep": {
            "max_human_rounds": 6,
            "estimated_tokens": "500k-1M+",
            "estimated_duration": "30-60 min",
        },
    }
    budget = dict(budget_by_depth[depth])
    budget["max_tool_calls"] = max_iterations
    budget["expected_count"] = expected_count
    return budget


def build_plan_payload(parsed_requirement: Dict[str, Any]) -> Dict[str, Any]:
    """根据结构化需求生成聊天式 LLM 研究计划。

    Args:
        parsed_requirement: 结构化需求。

    Returns:
        面向聊天消息展示的计划 payload。
    """
    research_budget = build_research_budget(parsed_requirement)
    style = parsed_requirement["style"]
    assumptions = [
        "The LLM agent decides which stocks to inspect from the chat context and tool evidence.",
        f"Risk preference is {parsed_requirement['risk_level']} and style hint is {style}.",
        "No fixed local candidate pool, factor score, or candidate compression pipeline is precomputed.",
    ]
    if parsed_requirement.get("open_questions"):
        assumptions.append("Open constraints can be refined in the same chat before or during research.")

    return {
        "objective_summary": parsed_requirement["raw_requirement"],
        "expected_count": parsed_requirement["expected_count"],
        "selection_mode": "llm_driven",
        "assumptions": assumptions,
        "chat_context": {
            "raw_requirement": parsed_requirement["raw_requirement"],
            "scope": parsed_requirement["scope"],
            "style": style,
            "risk_level": parsed_requirement["risk_level"],
            "allowed_industries": parsed_requirement.get("allowed_industries") or [],
            "excluded_industries": parsed_requirement.get("excluded_industries") or [],
            "hard_exclusions": parsed_requirement.get("hard_exclusions") or [],
            "exclude_recent_ipos": parsed_requirement.get("exclude_recent_ipos", False),
            "min_listing_days": parsed_requirement.get("min_listing_days"),
        },
        "llm_selection_policy": {
            "principle": "Use the conversation, tools, and evidence to choose stocks dynamically.",
            "must_not_use": ["fixed_local_candidate_pool", "server_side_pre_ranking", "prebuilt_candidate_list"],
            "output_target": "one final Markdown research answer with evidence, risks, and invalidation conditions",
        },
        "tool_policy": {
            "allowed_tools": "all_non_trading_agentic_tools",
            "blocked_tools": ["trading", "order", "portfolio", "position", "account"],
            "query_basis": "latest chat context, requirement, risk preference, and evidence gaps",
        },
        "phase_plan": [
            {"phase": "planning", "goal": "Confirm the research objective, tool policy, and budget."},
            {"phase": "research", "goal": "Let the LLM choose tool queries and gather evidence from chat context."},
            {"phase": "reflection", "goal": "Check coverage, counterevidence, bias, and missing risks."},
            {"phase": "synthesis", "goal": "Synthesize one final Markdown research answer and risk summary."},
        ],
        "evidence_sources": [
            "market_data",
            "financial",
            "technical",
            "capital_flow",
            "search",
            "news",
            "announcement",
            "human_input",
        ],
        "research_depth": parsed_requirement["research_depth"],
        "research_budget": research_budget,
        "human_checkpoints": [
            "Confirm whether to narrow sectors when the industry scope is too broad.",
            "Confirm priority when high upside conflicts with low drawdown.",
            "Confirm whether to narrow scope or accept partial conclusions when key search evidence is insufficient.",
        ],
        "risk_controls": [
            "Exclude untradeable or clearly invalid names.",
            "Keep counterevidence search and negative information summaries.",
            "Check evidence coverage, industry concentration, and missing risks before synthesis.",
        ],
        "expected_outputs": [
            "final_markdown_answer",
            "evidence_summary_in_markdown",
            "risk_summary_in_markdown",
            "invalidation_conditions_in_markdown",
        ],
        "open_questions": parsed_requirement.get("open_questions") or [],
    }


def build_plan_preview_payload(plan_payload: Dict[str, Any]) -> Dict[str, Any]:
    """从计划草稿生成轻量预览。

    Args:
        plan_payload: 计划草稿。

    Returns:
        可直接展示在 plan_card 消息中的摘要 payload。
    """
    return {
        "status": "preview",
        "objective_summary": plan_payload.get("objective_summary"),
        "scope": (plan_payload.get("chat_context") or {}).get("scope"),
        "style": (plan_payload.get("chat_context") or {}).get("style"),
        "selection_mode": plan_payload.get("selection_mode"),
        "tool_scope": (plan_payload.get("tool_policy") or {}).get("allowed_tools"),
        "max_tool_calls": (plan_payload.get("research_budget") or {}).get("max_tool_calls"),
        "estimated_duration": (plan_payload.get("research_budget") or {}).get("estimated_duration"),
        "estimated_tokens": (plan_payload.get("research_budget") or {}).get("estimated_tokens"),
        "open_questions": plan_payload.get("open_questions") or [],
    }
