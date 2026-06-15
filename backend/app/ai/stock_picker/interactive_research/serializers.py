from __future__ import annotations

from typing import Any, Dict

from fastapi.encoders import jsonable_encoder

from app.ai.json_utils import stable_json_dumps
from app.ai.stock_picker.interactive_research.models import (
    InteractiveResearchMessage,
    InteractiveResearchRun,
)
from app.core.i18n import i18n_service


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究后端翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


def serialize_run_summary(run: InteractiveResearchRun) -> Dict[str, Any]:
    """把 run ORM 对象转换为 API 响应字典。

    Args:
        run: 交互式研究 run ORM 对象。

    Returns:
        可被 Pydantic 响应模型校验且可直接 JSON 序列化的字典。
    """
    checkpoint_payload = run.checkpoint_payload or {}
    llm_usage_payload = checkpoint_payload.get("llm_usage") if isinstance(checkpoint_payload, dict) else {}
    llm_usage = llm_usage_payload if isinstance(llm_usage_payload, dict) else {}
    return jsonable_encoder({
        "run_id": run.run_id,
        "user_id": run.user_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "current_phase": run.current_phase,
        "title": run.title,
        "raw_requirement": run.raw_requirement,
        "pending_message_id": run.pending_message_id,
        "checkpoint_payload": checkpoint_payload,
        "cache_context_version": run.cache_context_version,
        "version": run.version,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "finished_at": run.finished_at,
        "llm_usage": llm_usage,
    })


def serialize_message(message: InteractiveResearchMessage) -> Dict[str, Any]:
    """把消息 ORM 对象转换为 API 响应字典。

    Args:
        message: 聊天消息 ORM 对象。

    Returns:
        可被 Pydantic 响应模型校验且可直接 JSON 序列化的字典。
    """
    return jsonable_encoder({
        "message_id": message.message_id,
        "run_id": message.run_id,
        "role": message.role,
        "message_type": message.message_type,
        "content": message.content,
        "display_type": message.role,
        "markdown": build_message_markdown(message),
        "execution_status": message.status,
        "payload": message.payload or {},
        "parent_message_id": message.parent_message_id,
        "sequence_no": message.sequence_no,
        "status": message.status,
        "visible_to_user": message.visible_to_user,
        "created_at": message.created_at,
    })


def build_message_markdown(message: InteractiveResearchMessage) -> str:
    """把内部消息和 payload 转换为前端展示用 Markdown。

    Args:
        message: 聊天消息 ORM 对象。

    Returns:
        只包含用户可见正文的 Markdown 字符串。
    """
    content = str(message.content or "").strip()
    payload = message.payload if isinstance(message.payload, dict) else {}
    if message.message_type == "plan_card":
        return _build_plan_card_markdown(content, payload)
    if message.message_type == "tool_start":
        return _build_tool_start_markdown(content, payload)
    if message.message_type == "tool_result":
        return _build_tool_result_markdown(content, payload)
    if message.message_type == "progress_update":
        return _build_progress_markdown(content, payload)
    if message.message_type == "system_status":
        return _build_titled_markdown(_t("markdown.titles.system_status"), content, payload)
    if message.message_type == "assistant_question":
        return _build_titled_markdown(_t("markdown.titles.assistant_question"), content, payload)
    return content or _t("markdown.empty")


def _build_plan_card_markdown(content: str, payload: Dict[str, Any]) -> str:
    """生成研究计划卡片的 Markdown 正文。

    Args:
        content: 原始计划说明。
        payload: 消息 payload，包含 preview 和 actions。

    Returns:
        计划卡片 Markdown。
    """
    lines = [f"### {_t('markdown.titles.plan')}"]
    if content:
        lines.extend(["", content])
    return "\n".join(lines).strip()


def _build_tool_start_markdown(content: str, payload: Dict[str, Any]) -> str:
    """生成工具开始调用消息的 Markdown 正文。

    Args:
        content: 原始消息正文。
        payload: 工具调用 payload。

    Returns:
        工具开始调用 Markdown。
    """
    tool_name = str(payload.get("tool_name") or "").strip()
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    lines = [f"### {_t('markdown.titles.tool_start')}"]
    if tool_name:
        lines.append(f"- **{_t('markdown.labels.tool')}**: `{tool_name}`")
    if content:
        lines.append(f"- **{_t('markdown.labels.description')}**: {content}")
    if arguments:
        lines.extend(["", "```json", stable_json_dumps(arguments), "```"])
    return "\n".join(lines).strip()


def _build_tool_result_markdown(content: str, payload: Dict[str, Any]) -> str:
    """生成工具结果消息的 Markdown 正文。

    Args:
        content: 已压缩的工具结果摘要。
        payload: 工具结果 payload。

    Returns:
        工具结果 Markdown。
    """
    tool_name = str(payload.get("tool_name") or "").strip()
    success = payload.get("success")
    lines = [f"### {_t('markdown.titles.tool_result')}"]
    if tool_name:
        lines.append(f"- **{_t('markdown.labels.tool')}**: `{tool_name}`")
    if success is not None:
        result_label = _t("markdown.values.success") if bool(success) else _t("markdown.values.failed")
        lines.append(f"- **{_t('markdown.labels.result')}**: {result_label}")
    if content:
        lines.extend(["", content])
    return "\n".join(lines).strip()


def _build_progress_markdown(content: str, payload: Dict[str, Any]) -> str:
    """生成研究进展消息的 Markdown 正文。

    Args:
        content: 原始进展说明。
        payload: 进展 payload。

    Returns:
        研究进展 Markdown。
    """
    lines = [f"### {_t('markdown.titles.progress')}"]
    if content:
        lines.extend(["", content])
    visible_items = [
        (str(key), value)
        for key, value in payload.items()
        if value is not None and value != "" and key not in {"result_preview", "tool_call_id"}
    ]
    if visible_items:
        lines.append("")
        for key, value in visible_items:
            lines.append(f"- **{_humanize_key(key)}**: {_markdown_value(value)}")
    return "\n".join(lines).strip()


def _build_titled_markdown(title: str, content: str, payload: Dict[str, Any]) -> str:
    """生成带标题的通用 Markdown 消息。

    Args:
        title: Markdown 标题。
        content: 原始消息正文。
        payload: 消息 payload。

    Returns:
        通用 Markdown 正文。
    """
    lines = [f"### {title}"]
    if content:
        lines.extend(["", content])
    reason = payload.get("reason")
    if reason:
        lines.extend(["", f"- **{_t('markdown.labels.reason')}**: {_markdown_value(reason)}"])
    return "\n".join(lines).strip()


def _humanize_key(key: str) -> str:
    """把 payload 字段名转换为可读标签。

    Args:
        key: 原始字段名。

    Returns:
        面向用户展示的字段名。
    """
    labels = {
        "scope": _t("markdown.labels.scope"),
        "style": _t("markdown.labels.style"),
        "estimated_duration": _t("markdown.labels.estimated_duration"),
        "estimated_tokens": _t("markdown.labels.estimated_tokens"),
        "max_tool_calls": _t("markdown.labels.max_iterations"),
        "selection_mode": _t("markdown.labels.selection_mode"),
        "tool_scope": _t("markdown.labels.tool_scope"),
        "tool_name": _t("markdown.labels.tool"),
        "success": _t("markdown.labels.status"),
    }
    return labels.get(key, key.replace("_", " "))


def _markdown_value(value: Any) -> str:
    """把 payload 值转换为 Markdown 行内文本。

    Args:
        value: 任意 JSON 兼容值。

    Returns:
        适合放入 Markdown 列表项的字符串。
    """
    if isinstance(value, bool):
        return _t("markdown.values.yes") if value else _t("markdown.values.no")
    if isinstance(value, (dict, list)):
        return f"`{stable_json_dumps(value)}`"
    return str(value)
