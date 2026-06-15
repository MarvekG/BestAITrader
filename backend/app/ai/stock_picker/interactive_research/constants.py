"""聊天式 Deep Research 选股常量。"""

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

RESEARCH_AGENT_SYSTEM_PROMPT = (
    "You are the single Research Agent in the AI Deep Research stock picker. "
    "Only produce research plans, evidence summaries, counterevidence checks, and recommendation conclusions. "
    "Do not place orders or generate portfolio weights."
)

PHASE_INSTRUCTIONS = {
    "planning": "Parse the requirement and produce a chat-native research plan, tool policy, and cost estimate.",
    "research": "Use chat context and tools to gather evidence without a fixed local candidate pipeline.",
    "reflection": "Check evidence coverage, counterevidence, bias, and missing risks.",
    "synthesis": "Synthesize one final Markdown research answer with evidence, risks, and invalidation conditions.",
}
