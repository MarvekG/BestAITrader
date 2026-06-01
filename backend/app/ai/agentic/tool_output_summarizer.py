from __future__ import annotations

from typing import Any, Dict, Optional

from app.ai.llm_providers import get_llm_provider
from app.ai.llm_routing import get_shared_usage_lane
from app.ai.json_utils import stable_json_dumps
from app.core.config import settings
from app.core.logger import get_logger
from app.crud.llm_usage_log import record_llm_usage

logger = get_logger(__name__)

SUMMARY_THRESHOLD = 12000
TOOLS_TO_SUMMARIZE = [
    "search_news",
]


def _resolve_llm_model_name(llm: Any) -> str:
    model_name = getattr(llm, "model_name", None) or getattr(llm, "model", None)
    return str(model_name or settings.LLM_MODEL)


def _summary_llm(llm: Any) -> Any:
    return get_llm_provider().build_chat_model(
        model=_resolve_llm_model_name(llm),
        temperature=0.1,
        api_key=None,
    )


def should_summarize_tool_output(tool_name: str, content: str) -> bool:
    return tool_name in TOOLS_TO_SUMMARIZE and len(content) > SUMMARY_THRESHOLD


async def summarize_tool_output(
    llm: Any,
    *,
    role_name: str,
    tool_name: str,
    content: str,
    tool_args: Optional[Dict[str, Any]] = None,
    workflow: str | None = None,
    stage: str | None = None,
    iteration_index: int | None = None,
) -> str:
    """Compress large search-like tool output while preserving concrete facts."""
    logger.info("[%s] Extracting core facts from: %s (Input length: %s)...", role_name, tool_name, len(content))

    tool_args_json = stable_json_dumps(tool_args or {})
    summary_input = _build_summary_user_input(
        role_name=role_name,
        tool_name=tool_name,
        tool_args_json=tool_args_json,
        content=content,
    )

    try:
        summary_llm = _summary_llm(llm)
        response = await summary_llm.ainvoke(
            [
                ("system", _summary_system_prompt()),
                ("user", summary_input),
            ]
        )
        cache_lane, api_key_alias = get_shared_usage_lane()
        record_llm_usage(
            response,
            _resolve_llm_model_name(summary_llm),
            f"{role_name}_tool_summary",
            workflow=workflow or "tool_summary",
            stage=stage or tool_name,
            call_kind="tool_summary",
            iteration_index=iteration_index,
            cache_lane=cache_lane,
            api_key_alias=api_key_alias,
        )
        summary = response.content
        logger.info(
            "[%s] Extraction complete: %s -> %s chars. Tool: %s",
            role_name,
            len(content),
            len(summary),
            tool_name,
        )
        return f"[Structured Summary of {tool_name}]:\n{summary}"
    except Exception as exc:
        logger.exception("[%s] Failed to summarize tool output: %s", role_name, exc)
        return content[:SUMMARY_THRESHOLD]


def _summary_system_prompt() -> str:
    if str(settings.SYSTEM_LANGUAGE).lower().startswith("zh"):
        return (
            "你正在压缩整理工具的原始输出，具体角色、工具名、工具参数和内容会由用户消息提供。"
            "在删除明显重复、模板化表述、HTML 噪音和无信息量废话的前提下，尽量保留原始信息密度和细节。\n\n"
            "关键规则:\n"
            "- 严禁调用任何工具，只能直接输出文本摘要。\n"
            "- 不要过度概括，优先保留具体事实。\n"
            "- 如果原文出现日期、时间、公司名、股票名、股票代码、机构、人名、事件时间线、价格、百分比、目标值、评级、"
            "因果关系，尽量保留。\n"
            "- 如果有多篇文章或多条记录，分别覆盖每个重要事项，不要合并成一句模糊结论。\n"
            "- 如果观点或情绪存在冲突，明确写出“混合”或“存在分歧”。\n"
            "- 不要编造原始数据没有支持的事实、解释或情绪。\n\n"
            "相关性约束:\n"
            "- 根据用户消息中的 tool_args_json 判断查询意图。\n"
            "- 以查询意图为准总结信息，忽略明显不相关、弱相关、跑题或只是在大类主题上沾边的内容。\n"
            "- 如果某条内容与查询目标的关联不明确，不要写入摘要。\n\n"
            "输出格式:\n"
            "1. 总览: 2-4 句话概括主要主题。\n"
            "2. 详细要点: 用 bullet 列出重要事项；每条尽量保留原始实体、日期和数字。\n"
            "3. 市场情绪: bullish / bearish / neutral / mixed，并附简短依据。\n"
            "4. 核心数据: 紧凑列出最重要的价格、涨跌幅、目标值、评级或其他数字信号。\n"
            "5. 其他信号: 保留次要但仍有价值的信息。\n\n"
            "目标:\n"
            "- 优先保留信息量，不追求文采。\n"
            "- 只要可能影响后续判断，就尽量多保留细节。\n"
            "- 控制在 2500 个中文字符以内。\n"
            "- 不要寒暄，不要废话。"
        )
    return (
        "You are condensing raw tool output. The user message provides the role, tool name, tool arguments, and content. "
        "Preserve as much original information density and detail as possible while removing only obvious duplication, "
        "boilerplate, HTML noise, and filler.\n\n"
        "CRITICAL RULES:\n"
        "- You MUST NOT call any tools. Only return a text summary.\n"
        "- Keep concrete facts instead of over-abstracting.\n"
        "- Preserve dates, times, company or stock names, stock codes, institutions, people, event chronology, prices, "
        "percentages, target values, ratings, and causal statements whenever present.\n"
        "- If there are multiple articles or records, cover each important item separately instead of merging them "
        "into one vague summary.\n"
        "- If viewpoints or sentiment conflict, explicitly say they are mixed or conflicting.\n"
        "- Do not invent facts, explanations, or sentiment that are not supported by the raw data.\n\n"
        "RELEVANCE FILTERS:\n"
        "- Use tool_args_json from the user message to infer query intent.\n"
        "- Summarize according to the query intent and ignore content that is clearly unrelated, only weakly related, "
        "off-topic, or merely adjacent by broad theme.\n"
        "- If an item's connection to the query target is unclear, leave it out of the summary.\n\n"
        "OUTPUT FORMAT:\n"
        "1. OVERVIEW: Summarize the main themes in 2-4 sentences.\n"
        "2. DETAILED POINTS: Use bullets for important items and keep original entities, dates, and numbers whenever "
        "possible.\n"
        "3. MARKET SENTIMENT: bullish / bearish / neutral / mixed, with a brief justification.\n"
        "4. CORE DATA: List the most important prices, percentage moves, targets, ratings, or other numeric signals "
        "compactly.\n"
        "5. REMAINING SIGNALS: Preserve secondary but still useful information.\n\n"
        "TARGET:\n"
        "- Prefer high information density over elegant wording.\n"
        "- Keep more detail from the source whenever it may affect downstream judgment.\n"
        "- Limit to 2500 Chinese characters.\n"
        "- No chatter."
    )


def _build_summary_user_input(
    *,
    role_name: str,
    tool_name: str,
    tool_args_json: str,
    content: str,
) -> str:
    return (
        f"role_name: {role_name}\n"
        f"tool_name: {tool_name}\n"
        f"tool_args_json: {tool_args_json}\n"
        f"content: {content[:120000]}\n"
    )
