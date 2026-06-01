from __future__ import annotations

from app.core.config import settings


CACHE_LANE_RESEARCH = "research"
CACHE_LANE_SHARED = "shared"
API_KEY_ALIAS_RESEARCH = "research_llm_api_key"
API_KEY_ALIAS_SHARED = "shared_llm_api_key"


def should_run_debate_agents_in_parallel() -> bool:
    """判断辩论 Agent 是否并行执行。

    Returns:
        `DEBATE_AGENT_PARALLEL_ENABLED` 的布尔值；默认并行。
    """

    return bool(getattr(settings, "DEBATE_AGENT_PARALLEL_ENABLED", True))


def get_research_usage_lane() -> tuple[str, str]:
    """返回投研任务 LLM usage 观测标签。

    Returns:
        cache lane 和 API Key alias 标签。
    """

    return CACHE_LANE_RESEARCH, API_KEY_ALIAS_RESEARCH


def get_shared_usage_lane() -> tuple[str, str]:
    """返回普通共享 LLM usage 观测标签。

    Returns:
        cache lane 和 API Key alias 标签。
    """

    return CACHE_LANE_SHARED, API_KEY_ALIAS_SHARED
