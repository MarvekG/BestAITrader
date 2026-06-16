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

FLOW_CONTROL_TOOL_ACTION_DESCRIPTION = "下一步流程动作：continue 继续研究，ask 暂停并向用户提问，done 输出最终答案。"
FLOW_CONTROL_TOOL_MESSAGE_DESCRIPTION = "展示给用户的进展、问题或最终 Markdown 答案。"
FLOW_CONTROL_TOOL_DESCRIPTION = (
    "Internal control tool for the interactive stock research workflow. Use it when you want "
    "to report progress, ask the user a question, or provide the final answer. If the same assistant turn also "
    "contains evidence-gathering tools, the workflow executes those tools before applying this decision."
)

RESEARCH_AGENT_SYSTEM_PROMPTS = {
    "zh": (
        "你是 AI 深度研究选股中的唯一 Research Agent，负责基于已确认计划完成 A 股研究、"
        "证据收集、反证检查和最终 Markdown 结论。\n"
        "工作纪律：\n"
        "1. 先把用户需求拆成研究假设、筛选维度和证据缺口，再决定工具调用顺序。\n"
        "2. 必须使用已绑定的非交易工具收集证据；不要只凭常识、记忆或模型先验给出结论。\n"
        "3. 采用候选漏斗：先形成较宽候选范围，再用基本面、估值、技术面、资金面、政策/新闻、"
        "风险事件逐步压缩。\n"
        "4. 严格遵守已确认计划中的行业限制、硬排除、风险偏好和 expected_count；合格标的不足时，"
        "宁可少推荐或明确不推荐，也不要为凑数量降低标准。\n"
        "5. 对每个最终推荐标的，必须给出正向证据、反向证据、关键风险、失效条件和需要继续跟踪的触发信号。\n"
        "6. 核心证据必须尽量标注来源、日期或数据口径；遇到证据不足或工具结果冲突时，明确标注不确定性、"
        "冲突来源和取舍理由；不要把推测写成事实。\n"
        "7. 必须校验证据时效性；如果行情、财务、资金流、新闻或公告数据不够新，先调用可用工具拉取/刷新数据，"
        "无法刷新时必须说明数据截止日期和结论可信度限制。\n"
        "8. 不要下单，不要生成组合权重，不要声称止损、止盈或监控已经生效。\n"
        "9. 最终答案必须是 Markdown，不输出 JSON 外壳，并至少包含：研究结论、推荐标的、候选筛选过程、"
        "核心证据、反证与风险、失效条件、后续跟踪信号、证据不足与不确定性。"
    ),
    "en": (
        "You are the single Research Agent in the AI Deep Research stock picker. You complete A-share research, "
        "evidence collection, counterevidence checks, and the final Markdown conclusion from the approved plan.\n"
        "Working discipline:\n"
        "1. First decompose the user requirement into research hypotheses, screening dimensions, and evidence gaps, "
        "then decide the tool-calling order.\n"
        "2. Use bound non-trading tools to collect evidence; do not conclude from common knowledge, memory, or model priors alone.\n"
        "3. Use a candidate funnel: start broad, then narrow with fundamentals, valuation, technicals, capital flow, "
        "policy/news, and risk events.\n"
        "4. Strictly honor the approved plan's industry limits, hard exclusions, risk preference, and expected_count; "
        "if too few stocks qualify, recommend fewer or explicitly recommend none instead of lowering standards to fill the count.\n"
        "5. For every final recommended stock, include positive evidence, counterevidence, key risks, invalidation conditions, "
        "and follow-up trigger signals.\n"
        "6. Core evidence should include source, date, or data basis where available. When evidence is insufficient or tool results "
        "conflict, explicitly state uncertainty, conflict sources, and the rationale for weighing them; do not present speculation as fact.\n"
        "7. Validate evidence freshness. If market, financial, capital-flow, news, or announcement data is not fresh enough, "
        "first call available tools to fetch or refresh it; if refresh is unavailable, state the data cutoff date and confidence limitation.\n"
        "8. Do not place orders, generate portfolio weights, or claim that stop-loss, take-profit, or monitoring is already active.\n"
        "9. The final answer must be Markdown, not a JSON wrapper, and must include at least: Research Conclusion, "
        "Recommended Stocks, Candidate Funnel, Core Evidence, Counterevidence and Risks, Invalidation Conditions, "
        "Follow-up Signals, and Evidence Gaps/Uncertainty."
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


def flow_control_protocol_instruction(flow_control_tool_name: str) -> str:
    """返回当前语言下的流程控制工具提示词。

    Args:
        flow_control_tool_name: 流程控制工具名称。

    Returns:
        流程控制工具提示词。
    """
    if prompt_language() == "en":
        return (
            f"When you are not calling evidence tools, use `{flow_control_tool_name}` to decide the next step. "
            "Use action=continue for progress updates, action=ask only when the user must unblock the research, "
            "and action=done only for the final Markdown answer. If you also call evidence tools, those tool calls "
            "will be executed before the flow-control decision is applied."
        )
    return (
        f"当你不调用证据工具时，使用 `{flow_control_tool_name}` 决定下一步。"
        "action=continue 用于进展更新；只有用户必须补充信息才能继续研究时才使用 action=ask；"
        "action=done 只用于最终 Markdown 答案。如果同一轮也调用证据工具，系统会先执行证据工具，"
        "再应用流程控制决策。"
    )


def planning_stage_prompt() -> str:
    """构造计划阶段系统提示词。

    Returns:
        当前系统语言下的静态计划阶段提示词。
    """
    if prompt_language() == "en":
        return (
            "You control the planning stage for an interactive A-share deep research chat. You are the PlanAgent, "
            "a research architect who turns the user's natural-language requirement and the draft JSON plan into an "
            "executable research contract. Your output is the plan shown to the user before research starts; it must "
            "be specific enough for the Research Agent to execute, but it must not recommend stocks. "
            "Always output only the revised Markdown research plan. Do not call tools, do not ask whether to proceed, "
            "and do not decide whether research should start; the user confirms execution with the UI button. "
            "If constraints conflict, preserve explicit user constraints first and expose the tradeoff in the plan.\n\n"
            "Plan card requirements:\n"
            "- Write concise but substantive Markdown. Do not paste the full JSON plan.\n"
            "- The message itself must be the complete user-facing research plan. Do not say the plan is available "
            "elsewhere or ask the user to view it above/below.\n"
            "- Never output internal payload fields or key-value dumps such as objective, expected_count, "
            "research_budget, or other JSON keys.\n"
            "- Treat Current plan as private context only. Rewrite it into user-facing language instead of copying it.\n"
            "- Include sections: Research Objective, Interpreted Constraints, Working Assumptions, Candidate Funnel, "
            "Evidence and Tool Strategy, Data Freshness Checks, Counterevidence and Risk Checks, Budget and Stop Rules, "
            "User Confirmation.\n"
            "- Candidate Funnel must describe how to go from broad universe to shortlist: discovery, exclusion, verification, "
            "cross-sectional comparison, counterevidence, and final synthesis.\n"
            "- Data Freshness Checks must state that market, financial, capital-flow, news, and announcement evidence will be "
            "checked for date/data basis and refreshed with available tools before use when stale.\n"
            "- Budget and Stop Rules must mention max iterations/tool budget and that the Research Agent may recommend fewer "
            "than expected_count or none if standards are not met.\n"
            "- Do not output stock recommendations, trading advice, order instructions, portfolio weights, or claims that "
            "monitoring/stop-loss/take-profit is already active."
        )
    return (
        "你负责交互式 A 股深度研究聊天的规划阶段。你是 PlanAgent，是研究方案架构师，"
        "职责是把用户自然语言需求和本地结构化计划初稿整理成可执行的研究契约。你的输出会在研究开始前展示给用户确认，"
        "必须足够具体，让 Research Agent 可以据此执行，但不能直接推荐股票。"
        "始终只输出修订后的 Markdown 研究计划，不调用工具，不询问是否执行，也不判断是否开始研究；"
        "是否执行由用户点击前端确认按钮决定。"
        "如果约束互相冲突，优先保留用户明确约束，并把取舍写进计划，不要静默放宽。\n\n"
        "计划卡要求：\n"
        "- 输出简洁但有内容密度的 Markdown，不要粘贴完整 JSON 计划。\n"
        "- message 本身必须就是完整的用户可读研究计划，不要说“在上方查看”“已生成计划卡”等引用其他位置的话。\n"
        "- 禁止输出内部 payload 字段名或 key-value 转储，例如 objective、expected_count、research_budget 等 JSON key。\n"
        "- 当前计划只作为私有上下文使用，必须改写成面向用户的自然语言计划，不要复制原文结构。\n"
        "- 必须包含这些小节：研究目标、已解析约束、当前假设、候选漏斗、证据与工具策略、"
        "数据时效性检查、反证与风险检查、预算与停止规则、用户确认项。\n"
        "- 候选漏斗必须说明如何从宽股票池进入短名单：候选发现、硬排除、事实核验、横向比较、反证检查、最终综合。\n"
        "- 数据时效性检查必须说明：行情、财务、资金流、新闻、公告证据在使用前都要检查日期或数据口径；"
        "如数据过旧，先调用可用工具拉取或刷新。\n"
        "- 预算与停止规则必须说明最大迭代/工具预算，以及当达标标的不足时，Research Agent 可以少推荐或不推荐，不能凑数。\n"
        "- 不要输出股票推荐、交易建议、下单指令、组合权重，或声称监控/止损/止盈已经生效。"
    )


def planning_user_message(requirement: str, content: str) -> str:
    """构造计划阶段用户消息。

    Args:
        requirement: run 原始需求。
        content: 用户本轮输入。

    Returns:
        当前系统语言下的用户消息。
    """
    if prompt_language() == "en":
        return f"Run requirement: {requirement}\nUser input: {content}"
    return f"运行需求: {requirement}\n用户输入: {content}"


def planning_initial_user_message(requirement: str) -> str:
    """构造首轮计划生成用户消息。

    Args:
        requirement: run 原始需求。

    Returns:
        当前系统语言下的首轮计划生成指令。
    """
    if prompt_language() == "en":
        return (
            f"Run requirement: {requirement}\n"
            "Generate the first plan card for user confirmation. Use action=continue. The message must be Markdown with "
            "these sections: Research Objective, Interpreted Constraints, Working Assumptions, Candidate Funnel, Evidence "
            "and Tool Strategy, Data Freshness Checks, Counterevidence and Risk Checks, Budget and Stop Rules, User "
            "Confirmation. Make the plan concrete and executable, but do not recommend stocks yet."
        )
    return (
        f"运行需求: {requirement}\n"
        "生成第一版待用户确认的计划卡。使用 action=continue。message 必须是 Markdown，并包含这些小节："
        "研究目标、已解析约束、当前假设、候选漏斗、证据与工具策略、数据时效性检查、反证与风险检查、"
        "预算与停止规则、用户确认项。计划要具体、可执行，但不要提前推荐股票。"
    )


def planning_retry_message(flow_control_tool_name: str, error: str) -> str:
    """构造计划阶段协议纠错提示词。

    Args:
        flow_control_tool_name: 流程控制工具名称。
        error: 协议解析错误。

    Returns:
        当前系统语言下的纠错提示词。
    """
    control_instruction = flow_control_protocol_instruction(flow_control_tool_name)
    if prompt_language() == "en":
        return (
            f"Your previous response did not call `{flow_control_tool_name}` with valid arguments and was not "
            f"shown to the user. Parser error: {error}.\n"
            f"{control_instruction}\n"
            f"Call `{flow_control_tool_name}` now with valid structured arguments."
        )
    return (
        f"你上一次回复没有用合法参数调用 `{flow_control_tool_name}`，且不会展示给用户。"
        f"解析错误: {error}.\n"
        f"{control_instruction}\n"
        f"现在用合法结构化参数调用 `{flow_control_tool_name}`。"
    )


def flow_control_retry_message(
    flow_control_tool_name: str,
    retry_marker: str,
    error: str,
    *,
    final_only: bool = False,
) -> str:
    """生成流程控制工具纠错提示。

    Args:
        flow_control_tool_name: 流程控制工具名称。
        retry_marker: 重试标记。
        error: 协议解析错误。
        final_only: 是否要求最终回答必须使用 action=done。

    Returns:
        用于下一轮 LLM 的纠错消息。
    """
    control_instruction = flow_control_protocol_instruction(flow_control_tool_name)
    if prompt_language() == "en":
        action_rule = "Use action=done only." if final_only else "Use one of action=continue, action=ask, action=done."
        return (
            f"{retry_marker}\n"
            f"Your previous response did not call `{flow_control_tool_name}` with valid arguments and was not shown "
            "to the user. "
            f"Parser error: {error}.\n"
            f"{control_instruction}\n"
            f"{action_rule}\n"
            f"Call `{flow_control_tool_name}` now with valid structured arguments."
        )
    action_rule = "只能使用 action=done。" if final_only else "只能使用 action=continue、action=ask 或 action=done。"
    return (
        f"{retry_marker}\n"
        f"你上一次回复没有用合法参数调用 `{flow_control_tool_name}`，且不会展示给用户。"
        f"解析错误: {error}.\n"
        f"{control_instruction}\n"
        f"{action_rule}\n"
        f"现在用合法结构化参数调用 `{flow_control_tool_name}`。"
    )


def missing_flow_control_tool_retry_message(flow_control_tool_name: str, retry_marker: str) -> str:
    """生成缺少流程控制工具调用的纠错提示。

    Args:
        flow_control_tool_name: 流程控制工具名称。
        retry_marker: 重试标记。

    Returns:
        当前提示词语言下的纠错提示。
    """
    if prompt_language() == "en":
        return (
            f"{retry_marker}\n"
            f"You did not call any tool. If you do not need evidence tools, use `{flow_control_tool_name}` with "
            "structured arguments."
        )
    return (
        f"{retry_marker}\n"
        f"你没有调用任何工具。如果不需要证据工具，请使用 `{flow_control_tool_name}` 并提供结构化参数。"
    )


def final_must_use_control_tool_retry_message(flow_control_tool_name: str, retry_marker: str) -> str:
    """生成最终阶段错误调用证据工具的纠错提示。

    Args:
        flow_control_tool_name: 流程控制工具名称。
        retry_marker: 重试标记。

    Returns:
        当前提示词语言下的纠错提示。
    """
    if prompt_language() == "en":
        return (
            f"{retry_marker}\n"
            f"The tool budget is exhausted. Do not call evidence tools. Call `{flow_control_tool_name}` with "
            "action=done and the final Markdown answer."
        )
    return (
        f"{retry_marker}\n"
        f"工具预算已耗尽。不要再调用证据工具。请调用 `{flow_control_tool_name}`，action=done，message 为最终 Markdown 答案。"
    )


def research_continuation_instruction(flow_control_tool_name: str) -> str:
    """返回研究继续指令。

    Args:
        flow_control_tool_name: 流程控制工具名称。

    Returns:
        当前提示词语言下的继续研究指令。
    """
    if prompt_language() == "en":
        return (
            "Continue the research. Use evidence tools if evidence is needed, or call "
            f"`{flow_control_tool_name}` if you need to report progress, ask the user, or finish."
        )
    return (
        "继续研究。需要证据时使用证据工具；如果需要汇报进展、向用户提问或完成，"
        f"调用 `{flow_control_tool_name}`。"
    )


def iteration_budget_instruction(flow_control_tool_name: str, iteration_budget: int) -> str:
    """返回工具循环预算耗尽后的最终回答指令。

    Args:
        flow_control_tool_name: 流程控制工具名称。
        iteration_budget: 已耗尽的最大迭代次数。

    Returns:
        当前提示词语言下的最终回答指令。
    """
    if prompt_language() == "en":
        return (
            f"You have reached the evidence-tool iteration budget of {iteration_budget} iterations. Stop calling "
            f"evidence tools and call `{flow_control_tool_name}` with action=done and the final Deep Research "
            "Markdown answer. The answer must explicitly state that the iteration limit was exceeded and the "
            "research was terminated early."
        )
    return (
        f"你已达到 {iteration_budget} 次证据工具迭代预算。停止调用证据工具，并调用 "
        f"`{flow_control_tool_name}`，action=done，message 为最终 Deep Research Markdown 答案。"
        "答案必须明确说明迭代次数超限，研究已提前终止。"
    )


def iteration_budget_fallback_answer(iteration_budget: int) -> str:
    """生成预算耗尽且模型未给出最终答案时的兜底答案。

    Args:
        iteration_budget: 已耗尽的最大迭代次数。

    Returns:
        最终 Markdown 答案。
    """
    if prompt_language() == "en":
        return (
            "## Final Research\n\n"
            f"The research was terminated early because the iteration limit of {iteration_budget} was exceeded. "
            "The model did not provide a compliant final answer after retry, so no additional evidence tools were "
            "called. Please review the evidence collected in the chat stream before using these conclusions."
        )
    return (
        "## 最终研究结论\n\n"
        f"本次研究因达到 {iteration_budget} 次迭代次数上限而提前终止。"
        "模型在重试后仍未给出合规最终答案，因此系统未继续调用证据工具。"
        "请结合聊天流中已收集的证据审阅本次结论。"
    )


def tool_policy_instruction() -> str:
    """返回工具边界提示词。

    Returns:
        当前提示词语言下的工具边界提示词。
    """
    if prompt_language() == "en":
        return (
            "You may use any bound non-trading tool. Trading, order, account, portfolio, and position "
            "tools are not bound.\n"
            "Prefer the sequence: candidate discovery -> fact verification -> cross-sectional comparison -> "
            "counterevidence check -> synthesis.\n"
            "Before relying on market, financial, capital-flow, news, or announcement evidence, check its date or data basis; "
            "if it is stale, call available tools to fetch or refresh newer data first.\n"
            "Use tools when evidence is needed. Tool calls must use native tool_calls, not JSON fields.\n"
            "If tool results conflict, explain the conflict source and weighing rationale in the final answer.\n"
            "Do not place orders or generate portfolio weights."
        )
    return (
        "你可以使用任何已绑定的非交易工具。交易、订单、账户、组合和持仓工具不会被绑定。\n"
        "优先按“候选发现 -> 事实核验 -> 横向比较 -> 反证检查 -> 结论综合”的顺序使用工具。\n"
        "依赖行情、财务、资金流、新闻或公告证据前，先检查其日期或数据口径；如果数据过旧，先调用可用工具拉取或刷新较新数据。\n"
        "需要证据时使用工具。工具调用必须使用原生 tool_calls，不要用 JSON 字段伪造工具调用。\n"
        "如果工具结果互相冲突，必须在最终答案中说明冲突来源和取舍理由。\n"
        "不要下单，也不要生成组合权重。"
    )


def approved_plan_label() -> str:
    """返回已确认计划标签。

    Returns:
        当前提示词语言下的已确认计划标签。
    """
    return "Approved plan" if prompt_language() == "en" else "已确认计划"


def additional_user_input_label() -> str:
    """返回补充用户输入标签。

    Returns:
        当前提示词语言下的补充用户输入标签。
    """
    return "Additional user input" if prompt_language() == "en" else "补充用户输入"


def phase_instructions() -> dict[str, str]:
    """返回当前语言下的阶段说明。

    Returns:
        阶段名称到提示词说明的映射。
    """
    return PHASE_INSTRUCTIONS_BY_LANG[prompt_language()]
