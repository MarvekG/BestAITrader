from __future__ import annotations

from typing import Any, Dict

from app.ai.stock_picker.interactive_research.constants import (
    DEFAULT_RESEARCH_DEPTH,
    DEFAULT_RISK_LEVEL,
    DEFAULT_SCOPE,
    DEFAULT_STYLE,
)


def build_plan_payload(request_data: Dict[str, Any]) -> Dict[str, Any]:
    """从 API 请求直接生成最小研究计划 payload。

    Args:
        request_data: API 请求数据。

    Returns:
        研究计划 payload。
    """
    requirement = str(request_data.get("requirement") or "").strip()
    scope = request_data.get("scope") or DEFAULT_SCOPE
    research_depth = request_data.get("research_depth") or DEFAULT_RESEARCH_DEPTH
    risk_level = request_data.get("risk_level") or DEFAULT_RISK_LEVEL
    style = request_data.get("style") or infer_style(requirement)
    expected_count = int(request_data.get("expected_count") or 5)
    max_iterations = max(10, int(request_data.get("max_iterations") or 60))
    hard_exclusions = ["ST", "*ST", "delisting risk", "abnormal trading status", "non-A-share common stock"]
    if request_data.get("exclude_recent_ipos"):
        hard_exclusions.append("recent IPOs below the user-specified minimum listing days")

    return {
        "objective": requirement,
        "expected_count": expected_count,
        "scope": scope,
        "research_depth": research_depth,
        "risk_level": risk_level,
        "style": style,
        "allowed_industries": list(request_data.get("allowed_industries") or []),
        "excluded_industries": list(request_data.get("excluded_industries") or []),
        "exclude_recent_ipos": bool(request_data.get("exclude_recent_ipos") or False),
        "min_listing_days": request_data.get("min_listing_days"),
        "hard_exclusions": hard_exclusions,
        "research_budget": build_research_budget(research_depth, expected_count, max_iterations),
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


def build_research_budget(research_depth: str, expected_count: int, max_iterations: int) -> Dict[str, Any]:
    """按研究深度和推荐数量生成研究预算。

    Args:
        research_depth: 研究深度。
        expected_count: 期望推荐数量。
        max_iterations: 最大工具调用轮数。

    Returns:
        研究预算字典。
    """
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
    budget = dict(budget_by_depth[research_depth])
    budget["max_tool_calls"] = max_iterations
    budget["expected_count"] = expected_count
    return budget


def build_plan_preview_payload(plan_payload: Dict[str, Any]) -> Dict[str, Any]:
    """从计划草稿生成轻量预览。

    Args:
        plan_payload: 计划草稿。

    Returns:
        可直接展示在 plan_card 消息中的摘要 payload。
    """
    return {
        "status": "preview",
        "objective": plan_payload.get("objective"),
        "scope": plan_payload.get("scope"),
        "style": plan_payload.get("style"),
        "max_tool_calls": (plan_payload.get("research_budget") or {}).get("max_tool_calls"),
        "estimated_duration": (plan_payload.get("research_budget") or {}).get("estimated_duration"),
        "estimated_tokens": (plan_payload.get("research_budget") or {}).get("estimated_tokens"),
    }
