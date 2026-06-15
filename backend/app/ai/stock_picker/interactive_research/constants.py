"""聊天式 Deep Research 选股常量。"""

from app.core.config import settings

RESEARCH_RUN_STATUSES = {
    "drafting_plan",
    "awaiting_plan_approval",
    "researching",
    "awaiting_user_input",
    "reflecting",
    "synthesizing",
    "completed",
    "cancelled",
    "failed",
}

TERMINAL_RESEARCH_STATUSES = {"completed", "cancelled", "failed"}
ACTIVE_RESEARCH_STATUSES = RESEARCH_RUN_STATUSES - TERMINAL_RESEARCH_STATUSES

RESEARCH_PHASES = {"planning", "research", "reflection", "synthesis"}

MESSAGE_ROLES = {"user", "assistant", "system", "tool"}
MESSAGE_TYPES = {
    "user_input",
    "assistant_text",
    "plan_card",
    "tool_start",
    "tool_result",
    "progress_update",
    "assistant_question",
    "final_result",
    "system_status",
}
MESSAGE_STATUSES = {"created", "streaming", "completed", "failed", "queued"}

ACTION_TYPES = {"approve", "cancel"}

DEFAULT_SCOPE = "core"
DEFAULT_STYLE = "balanced"
DEFAULT_RISK_LEVEL = "medium"
DEFAULT_RESEARCH_DEPTH = "standard"
CACHE_CONTEXT_VERSION = "research-agent-v1"

RESEARCH_AGENT_SYSTEM_PROMPTS = {
    "zh": (
        "你是 AI 深度研究选股中的唯一 Research Agent。"
        "只能输出研究计划、证据摘要、反证检查和推荐结论。"
        "不要下单，也不要生成组合权重。"
    ),
    "en": (
        "You are the single Research Agent in the AI Deep Research stock picker. "
        "Only produce research plans, evidence summaries, counterevidence checks, and recommendation conclusions. "
        "Do not place orders or generate portfolio weights."
    ),
}

PHASE_INSTRUCTIONS_BY_LANG = {
    "zh": {
        "planning": "解析用户需求，并生成适合聊天确认的研究计划、工具策略和成本估计。",
        "research": "使用聊天上下文和工具收集证据，不依赖固定的本地候选股流水线。",
        "reflection": "检查证据覆盖、反证、偏差和遗漏风险。",
        "synthesis": "综合生成最终 Markdown 研究答案，包含证据、风险和失效条件。",
    },
    "en": {
        "planning": "Parse the requirement and produce a chat-native research plan, tool policy, and cost estimate.",
        "research": "Use chat context and tools to gather evidence without a fixed local candidate pipeline.",
        "reflection": "Check evidence coverage, counterevidence, bias, and missing risks.",
        "synthesis": "Synthesize one final Markdown research answer with evidence, risks, and invalidation conditions.",
    },
}


def prompt_language() -> str:
    """读取当前提示词语言。

    Returns:
        zh 或 en；非英文配置默认使用中文。
    """
    return "en" if str(settings.SYSTEM_LANGUAGE).lower().startswith("en") else "zh"


def research_agent_system_prompt() -> str:
    """返回当前语言下的 Research Agent 系统提示词。

    Returns:
        Research Agent 系统提示词。
    """
    return RESEARCH_AGENT_SYSTEM_PROMPTS[prompt_language()]


def phase_instructions() -> dict[str, str]:
    """返回当前语言下的阶段说明。

    Returns:
        阶段名称到提示词说明的映射。
    """
    return PHASE_INSTRUCTIONS_BY_LANG[prompt_language()]
