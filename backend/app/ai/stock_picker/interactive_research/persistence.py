from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.stock_picker.interactive_research.constants import (
    ACTIVE_RESEARCH_STATUSES,
    TERMINAL_RESEARCH_STATUSES,
    phase_instructions,
)
from app.ai.stock_picker.interactive_research.models import (
    InteractiveResearchMessage,
    InteractiveResearchRun,
)
from app.ai.stock_picker.interactive_research.planning import build_plan_payload, parse_requirement
from app.ai.stock_picker.interactive_research.serializers import serialize_message, serialize_run_summary
from app.core.database import SessionLocal
from app.core.i18n import i18n_service


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究持久化层翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


def create_run_record(
    user_id: int,
    request_data: Dict[str, Any],
    *,
    parsed_requirement: Dict[str, Any],
    title: str,
) -> Dict[str, Any]:
    """创建研究 run 并写入首条用户消息。

    Args:
        user_id: 当前用户 ID。
        request_data: 已校验的用户研究请求。
        parsed_requirement: 已解析的用户需求。
        title: 调用方生成的聊天标题。

    Returns:
        新建 run 的基础快照。

    Raises:
        ValueError: 当前用户已有活跃 run 时抛出。
    """
    with SessionLocal() as db:
        active_run = (
            db.query(InteractiveResearchRun)
            .filter(
                InteractiveResearchRun.user_id == user_id,
                InteractiveResearchRun.status.in_(ACTIVE_RESEARCH_STATUSES),
            )
            .order_by(InteractiveResearchRun.created_at.desc())
            .first()
        )
        if active_run:
            raise ValueError(_t("errors.active_run_exists", run_id=active_run.run_id))

        plan_payload = build_plan_payload(parsed_requirement)
        run = InteractiveResearchRun(
            user_id=user_id,
            status="awaiting_plan_approval",
            current_stage="awaiting_plan_approval",
            current_phase="planning",
            title=title,
            raw_requirement=parsed_requirement["raw_requirement"],
            checkpoint_payload={
                "status": "awaiting_plan_approval",
                "current_phase": "planning",
                "parsed_requirement": parsed_requirement,
                "plan_payload": plan_payload,
            },
        )
        db.add(run)
        db.flush()
        append_message(
            db,
            run,
            role="user",
            message_type="user_input",
            content=parsed_requirement["raw_requirement"],
            payload={"request": request_data},
        )
        db.commit()
        db.refresh(run)
        return {"run_id": run.run_id, "raw_requirement": run.raw_requirement}


def list_run_records(user_id: int) -> List[InteractiveResearchRun]:
    """查询用户研究 run 列表。

    Args:
        user_id: 当前用户 ID。

    Returns:
        按创建时间倒序排列的 run 列表。
    """
    with SessionLocal() as db:
        return (
            db.query(InteractiveResearchRun)
            .filter(InteractiveResearchRun.user_id == user_id)
            .order_by(InteractiveResearchRun.created_at.desc())
            .all()
        )


def get_run_record(run_id: UUID, user_id: int) -> Optional[InteractiveResearchRun]:
    """查询用户拥有的单个研究 run。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。

    Returns:
        找到时返回 run，否则返回 None。
    """
    with SessionLocal() as db:
        return (
            db.query(InteractiveResearchRun)
            .filter(InteractiveResearchRun.run_id == run_id, InteractiveResearchRun.user_id == user_id)
            .first()
        )


def delete_run_record(run_id: UUID, user_id: int) -> bool:
    """删除用户拥有的研究 run。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。

    Returns:
        删除成功返回 True，否则返回 False。
    """
    with SessionLocal() as db:
        run = _get_user_run(db, run_id, user_id)
        if run is None:
            return False
        db.query(InteractiveResearchMessage).filter(InteractiveResearchMessage.run_id == run_id).delete(
            synchronize_session=False
        )
        db.delete(run)
        db.commit()
        return True


def append_user_message_record(
    run_id: UUID,
    user_id: int,
    content: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """追加用户消息并执行同步状态写入。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。
        content: 用户输入文本。
        payload: 可选结构化 payload。

    Returns:
        消息、原始状态和可选 workflow 计划 payload。

    Raises:
        LookupError: run 不存在或不属于用户时抛出。
        ValueError: 终态 run 不允许继续追加时抛出。
    """
    with SessionLocal() as db:
        run = _get_user_run(db, run_id, user_id)
        if run is None:
            raise LookupError(_t("errors.run_not_found"))
        if run.status in TERMINAL_RESEARCH_STATUSES:
            raise ValueError(_t("errors.terminal_cannot_accept_messages"))

        run_status = run.status
        message_status = "queued" if run.status in {"researching", "reflecting", "synthesizing"} else "completed"
        parent_message_id = (
            run.pending_message_id if run.status in {"awaiting_plan_approval", "awaiting_user_input"} else None
        )
        message_payload = dict(payload or {})
        if message_status == "queued":
            message_payload["queued_user_input"] = True
        message = append_message(
            db,
            run,
            role="user",
            message_type="user_input",
            content=content.strip(),
            payload=message_payload,
            parent_message_id=parent_message_id,
            status=message_status,
        )
        plan_payload = None
        if run_status == "awaiting_user_input":
            plan_payload = _plan_payload_from_checkpoint(run)
            transition_run(
                db,
                run,
                status="researching",
                current_phase="research",
                system_content=_t("messages.answer_received"),
                system_payload={"answer_message_id": str(message.message_id)},
                checkpoint_reason="user_answer_received",
                checkpoint_extra={"answer_message_id": str(message.message_id), "plan_payload": plan_payload},
                clear_pending=True,
            )
        elif run_status not in {"awaiting_plan_approval", "awaiting_user_input"}:
            write_checkpoint(
                db,
                run,
                reason="queued_user_input",
                extra_payload={"queued_message_id": str(message.message_id)},
            )
        db.commit()
        db.refresh(message)
        return {"message": message, "run_status": run_status, "plan_payload": plan_payload}


def approve_plan_record(run_id: UUID, user_id: int) -> Dict[str, Any]:
    """确认计划并把 run 切换到研究中。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。

    Returns:
        更新后的 run 和确认的计划 payload。

    Raises:
        LookupError: run 不存在或不属于用户时抛出。
        ValueError: run 状态不允许确认时抛出。
    """
    with SessionLocal() as db:
        run = _get_user_run(db, run_id, user_id)
        if run is None:
            raise LookupError(_t("errors.run_not_found"))
        if run.status != "awaiting_plan_approval":
            raise ValueError(_t("errors.only_awaiting_plan_approval_can_approve"))
        plan_payload = _plan_payload_from_checkpoint(run)
        transition_run(
            db,
            run,
            status="researching",
            current_phase="research",
            system_content=_t("messages.plan_approved"),
            checkpoint_reason="plan_approved",
            checkpoint_extra={"plan_payload": plan_payload},
        )
        db.commit()
        db.refresh(run)
        return {"run": run, "plan_payload": plan_payload}


def cancel_run_record(run_id: UUID, user_id: int, reason: Optional[str] = None) -> InteractiveResearchRun:
    """取消用户研究 run。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。
        reason: 可选取消原因。

    Returns:
        已取消的 run。

    Raises:
        LookupError: run 不存在或不属于用户时抛出。
        ValueError: 终态 run 不能重复取消时抛出。
    """
    with SessionLocal() as db:
        run = _get_user_run(db, run_id, user_id)
        if run is None:
            raise LookupError(_t("errors.run_not_found"))
        if run.status in TERMINAL_RESEARCH_STATUSES:
            raise ValueError(_t("errors.terminal_cannot_cancel"))
        transition_run(
            db,
            run,
            status="cancelled",
            system_content=_t("messages.cancelled"),
            system_payload={"reason": reason or ""},
            checkpoint_reason="cancelled",
            clear_pending=True,
            finished=True,
        )
        db.commit()
        db.refresh(run)
        return run


def list_message_records(run_id: UUID, user_id: int, *, visible_only: bool = True) -> List[InteractiveResearchMessage]:
    """查询 run 的消息流。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。
        visible_only: 是否只返回用户可见消息。

    Returns:
        按 sequence_no 升序排列的消息列表。
    """
    with SessionLocal() as db:
        run = _get_user_run(db, run_id, user_id)
        if run is None:
            return []
        query = db.query(InteractiveResearchMessage).filter(InteractiveResearchMessage.run_id == run_id)
        if visible_only:
            query = query.filter(InteractiveResearchMessage.visible_to_user.is_(True))
        return query.order_by(InteractiveResearchMessage.sequence_no.asc()).all()


def load_plan_payload_record(run_id: UUID) -> Optional[Dict[str, Any]]:
    """读取 run checkpoint 中的计划 payload。

    Args:
        run_id: 研究 run ID。

    Returns:
        找到 run 时返回计划 payload，否则返回 None。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return None
        return _plan_payload_from_checkpoint(run)


def fail_run_record(run_id: UUID, error_text: str, exception_type: str, exception_message: str) -> None:
    """把 run 标记为失败并写入系统消息。

    Args:
        run_id: 研究 run ID。
        error_text: 用户可见错误文案。
        exception_type: 异常类型名称。
        exception_message: 异常消息。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return
        transition_run(
            db,
            run,
            status="failed",
            system_content=error_text,
            system_payload={"exception_type": exception_type, "exception_message": exception_message},
            checkpoint_reason="workflow_exception",
            error_message=error_text,
        )
        db.commit()


def load_plan_turn_record(run_id: UUID) -> Optional[Dict[str, Any]]:
    """读取计划阶段本轮所需的 run 与历史消息快照。

    Args:
        run_id: 研究 run ID。

    Returns:
        run 存在时返回状态、计划 payload 和历史消息；否则返回 None。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return None
        persisted = _load_plan_messages(db, run)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "plan_payload": _plan_payload_from_checkpoint(run),
            "persisted_messages": persisted,
        }


def persist_plan_card_record(
    run_id: UUID,
    *,
    plan_message: str,
    plan_preview_payload: Dict[str, Any],
    plan_payload: Dict[str, Any],
    usage_record: Optional[Dict[str, Any]],
    reason: str,
    bump_version: bool,
) -> bool:
    """打开事务写入计划卡。

    Args:
        run_id: 研究 run ID。
        plan_message: 计划 Agent Markdown 输出。
        plan_preview_payload: 计划预览 payload。
        plan_payload: 完整计划 payload。
        usage_record: 本轮 LLM usage。
        reason: checkpoint 原因。
        bump_version: 是否递增 run 版本。

    Returns:
        写入成功返回 True；run 不存在或状态不允许时返回 False。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            raise LookupError(_t("errors.run_not_found"))
        if run.status != "awaiting_plan_approval":
            return False
        persist_plan_card(
            db,
            run,
            plan_message=plan_message,
            plan_preview_payload=plan_preview_payload,
            plan_payload=plan_payload,
            usage_record=usage_record,
            reason=reason,
            bump_version=bump_version,
        )
        db.commit()
        return True


def start_research_run_record(run_id: UUID, plan_payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """启动研究 loop 并返回运行快照和通知 payload。

    Args:
        run_id: 研究 run ID。
        plan_payload: 已确认计划 payload。

    Returns:
        run 存在时返回快照和通知 payload；否则返回 None。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return None
        queued_messages = _process_queued_user_inputs(db, run)
        run.status = "researching"
        run.current_stage = "researching"
        run.current_phase = "research"
        run.version += 1
        current_checkpoint = run.checkpoint_payload or {}
        write_checkpoint(
            db,
            run,
            reason="agent_loop_started",
            extra_payload={
                "plan_payload": plan_payload,
                "answer_message_id": current_checkpoint.get("answer_message_id"),
                "queued_message_ids": [message["message_id"] for message in queued_messages],
            },
        )
        message = append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content=_t("messages.research_started"),
            payload={"phase_instruction": phase_instructions()["research"]},
        )
        payload = _notification_payload(run, message, "research_started")
        snapshot = {"user_id": run.user_id, "raw_requirement": run.raw_requirement, "queued_before": queued_messages}
        db.commit()
        return {"snapshot": snapshot, "notification": payload}


def append_tool_start_record(run_id: UUID, tool_name: str, tool_args: Dict[str, Any], tool_call_id: str) -> Dict[str, Any]:
    """记录工具开始调用消息。

    Args:
        run_id: 研究 run ID。
        tool_name: 工具名称。
        tool_args: 工具参数。
        tool_call_id: LLM 工具调用 ID。

    Returns:
        新消息 ID 和通知 payload；run 不存在时消息 ID 为空。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return {"message_id": "", "notification": None}
        message = append_message(
            db,
            run,
            role="tool",
            message_type="tool_start",
            content=_t("messages.tool_start", tool_name=tool_name),
            payload={"tool_name": tool_name, "arguments": tool_args, "tool_call_id": tool_call_id},
        )
        payload = _notification_payload(run, message, "tool_start")
        message_id = str(message.message_id)
        db.commit()
        return {"message_id": message_id, "notification": payload}


def append_tool_result_and_progress_record(
    run_id: UUID,
    *,
    tool_name: str,
    tool_args: Dict[str, Any],
    tool_call_id: str,
    start_message_id: str,
    success: bool,
    result_text: str,
    result_content: str,
) -> List[Dict[str, Any]]:
    """记录工具结果、进度消息和 checkpoint。

    Args:
        run_id: 研究 run ID。
        tool_name: 工具名称。
        tool_args: 工具参数。
        tool_call_id: LLM 工具调用 ID。
        start_message_id: tool_start 消息 ID。
        success: 工具是否成功。
        result_text: 完整工具结果。
        result_content: 消息流展示摘要。

    Returns:
        需要推送的通知 payload 列表。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return []
        result_message = append_message(
            db,
            run,
            role="tool",
            message_type="tool_result",
            content=result_content,
            payload={
                "tool_name": tool_name,
                "arguments": tool_args,
                "tool_call_id": tool_call_id,
                "start_message_id": start_message_id,
                "success": success,
                "result_preview": result_text,
            },
        )
        progress_message = append_message(
            db,
            run,
            role="assistant",
            message_type="progress_update",
            content=_t("messages.tool_completed", tool_name=tool_name),
            payload={"tool_name": tool_name, "success": success},
        )
        current_checkpoint = run.checkpoint_payload or {}
        write_checkpoint(
            db,
            run,
            reason="tool_step_completed",
            extra_payload={
                "plan_payload": _plan_payload_from_checkpoint(run),
                "answer_message_id": current_checkpoint.get("answer_message_id"),
                "last_tool_name": tool_name,
                "last_tool_success": success,
            },
        )
        payloads = [
            _notification_payload(run, result_message, "tool_result"),
            _notification_payload(run, progress_message, "progress_update"),
        ]
        db.commit()
        return payloads


def synthesize_final_message_record(
    run_id: UUID,
    *,
    plan_payload: Dict[str, Any],
    tool_trace: List[Dict[str, Any]],
    final_content: str,
    stopped_by_iteration_limit: bool,
    iteration_budget: int,
) -> List[Dict[str, Any]]:
    """写入最终答案和完成状态。

    Args:
        run_id: 研究 run ID。
        plan_payload: 已确认计划 payload。
        tool_trace: 工具调用轨迹。
        final_content: LLM 最终回答。
        stopped_by_iteration_limit: 是否因预算耗尽停止。
        iteration_budget: 本轮最大迭代次数。

    Returns:
        需要推送的通知 payload 列表。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return []
        run.status = "synthesizing"
        run.current_stage = "synthesizing"
        run.current_phase = "synthesis"
        run.version += 1
        final_payload = {
            "phase_instruction": phase_instructions()["synthesis"],
            "requirement_summary": plan_payload.get("objective_summary") or run.raw_requirement,
            "selection_mode": "llm_driven",
            "answer_markdown": final_content,
            "stopped_by_iteration_limit": stopped_by_iteration_limit,
            "iteration_budget": iteration_budget,
            "evidence_summary": {
                "tool_call_count": len(tool_trace),
                "tool_names": [item.get("name") for item in tool_trace],
            },
            "tool_trace": tool_trace,
        }
        final_message = append_message(
            db,
            run,
            role="assistant",
            message_type="final_result",
            content=final_content or _t("messages.llm_loop_completed"),
            payload=final_payload,
        )
        final_notification = _notification_payload(run, final_message, "final_result")
        run.status = "completed"
        run.current_stage = "completed"
        run.finished_at = datetime.now()
        run.version += 1
        write_checkpoint(db, run, reason="final_message_created", extra_payload={"plan_payload": plan_payload})
        status_message = append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content=_t("messages.completed"),
            payload={"selection_mode": "llm_driven"},
        )
        completed_notification = _notification_payload(run, status_message, "completed")
        db.commit()
        return [final_notification, completed_notification]


def pause_for_user_question_record(run_id: UUID, plan_payload: Dict[str, Any], question_content: str) -> Optional[Dict[str, Any]]:
    """暂停研究并写入追问消息。

    Args:
        run_id: 研究 run ID。
        plan_payload: 已确认计划 payload。
        question_content: LLM 生成的追问。

    Returns:
        通知 payload；run 不存在时返回 None。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return None
        run.status = "awaiting_user_input"
        run.current_stage = "awaiting_user_input"
        run.version += 1
        question = append_message(
            db,
            run,
            role="assistant",
            message_type="assistant_question",
            content=question_content,
            payload={"reason": "agent_asked_user"},
        )
        run.pending_message_id = question.message_id
        write_checkpoint(db, run, reason="agent_asked_user", extra_payload={"plan_payload": plan_payload})
        payload = _notification_payload(run, question, "assistant_question")
        db.commit()
        return payload


def append_assistant_text_record(run_id: UUID, content: str) -> Optional[Dict[str, Any]]:
    """追加研究过程中的 assistant 文本。

    Args:
        run_id: 研究 run ID。
        content: 文本内容。

    Returns:
        通知 payload；run 不存在时返回 None。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return None
        message = append_message(
            db,
            run,
            role="assistant",
            message_type="assistant_text",
            content=content,
            payload={},
        )
        write_checkpoint(db, run, reason="assistant_text")
        payload = _notification_payload(run, message, "assistant_text")
        db.commit()
        return payload


def process_queued_user_inputs_record(run_id: UUID) -> List[Dict[str, str]]:
    """处理运行中排队的用户输入。

    Args:
        run_id: 研究 run ID。

    Returns:
        已处理的排队消息快照。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return []
        queued_messages = _process_queued_user_inputs(db, run)
        db.commit()
        return queued_messages


def append_queued_input_status_record(run_id: UUID, queued_messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """记录排队输入已并入上下文。

    Args:
        run_id: 研究 run ID。
        queued_messages: 已处理的排队消息快照。

    Returns:
        通知 payload；run 不存在时返回 None。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return None
        message = append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content=_t("messages.queued_input_appended"),
            payload={"queued_message_ids": [message["message_id"] for message in queued_messages]},
        )
        payload = _notification_payload(run, message, "queued_input_appended")
        db.commit()
        return payload


def build_recent_chat_messages_record(run_id: UUID) -> List[Dict[str, Any]]:
    """构造给 agent 使用的最近聊天消息。

    Args:
        run_id: 研究 run ID。

    Returns:
        最近消息的轻量结构。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is None:
            return []
        messages = (
            db.query(InteractiveResearchMessage)
            .filter(InteractiveResearchMessage.run_id == run.run_id)
            .order_by(InteractiveResearchMessage.sequence_no.desc())
            .limit(20)
            .all()
        )
        return [
            {"role": message.role, "message_type": message.message_type, "content": message.content}
            for message in reversed(messages)
        ]


def accumulate_llm_usage_record(run_id: UUID, usage_record: Optional[Dict[str, Any]]) -> None:
    """累加 run 级 LLM usage。

    Args:
        run_id: 研究 run ID。
        usage_record: 单次 LLM usage 记录。
    """
    with SessionLocal() as db:
        run = _get_run(db, run_id)
        if run is not None:
            accumulate_llm_usage(db, run, usage_record)
            db.commit()


def _get_run(db: Session, run_id: UUID) -> Optional[InteractiveResearchRun]:
    """在当前会话中按 ID 查询 run。

    Args:
        db: 数据库会话。
        run_id: 研究 run ID。

    Returns:
        找到时返回 run，否则返回 None。
    """
    return db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()


def _get_user_run(db: Session, run_id: UUID, user_id: int) -> Optional[InteractiveResearchRun]:
    """在当前会话中查询用户拥有的 run。

    Args:
        db: 数据库会话。
        run_id: 研究 run ID。
        user_id: 当前用户 ID。

    Returns:
        找到时返回 run，否则返回 None。
    """
    return (
        db.query(InteractiveResearchRun)
        .filter(InteractiveResearchRun.run_id == run_id, InteractiveResearchRun.user_id == user_id)
        .first()
    )


def _plan_payload_from_checkpoint(run: InteractiveResearchRun) -> Dict[str, Any]:
    """从 run checkpoint 中恢复当前计划。

    Args:
        run: 当前研究 run。

    Returns:
        计划 payload；缺失时返回最小计划。
    """
    checkpoint = run.checkpoint_payload or {}
    plan_payload = checkpoint.get("plan_payload")
    if isinstance(plan_payload, dict):
        return plan_payload
    parsed_requirement = parse_requirement({"requirement": run.raw_requirement})
    return build_plan_payload(parsed_requirement)


def _load_plan_messages(db: Session, run: InteractiveResearchRun) -> List[Dict[str, str]]:
    """读取计划阶段可见消息快照。

    Args:
        db: 数据库会话。
        run: 当前研究 run。

    Returns:
        用于重建计划对话缓存的消息列表。
    """
    persisted = (
        db.query(InteractiveResearchMessage)
        .filter(
            InteractiveResearchMessage.run_id == run.run_id,
            InteractiveResearchMessage.visible_to_user.is_(True),
            InteractiveResearchMessage.message_type.in_(["user_input", "plan_card"]),
        )
        .order_by(InteractiveResearchMessage.sequence_no.asc())
        .all()
    )
    return [
        {"role": item.role, "message_type": item.message_type, "content": item.content or ""}
        for item in persisted
    ]


def _process_queued_user_inputs(db: Session, run: InteractiveResearchRun) -> List[Dict[str, str]]:
    """在当前会话中处理排队用户输入。

    Args:
        db: 数据库会话。
        run: 当前研究 run。

    Returns:
        已处理的排队消息列表。
    """
    queued_messages = (
        db.query(InteractiveResearchMessage)
        .filter(
            InteractiveResearchMessage.run_id == run.run_id,
            InteractiveResearchMessage.role == "user",
            InteractiveResearchMessage.status == "queued",
        )
        .order_by(InteractiveResearchMessage.sequence_no.asc())
        .all()
    )
    message_snapshots = []
    for message in queued_messages:
        message_snapshots.append({"message_id": str(message.message_id), "content": message.content or ""})
        message.status = "completed"
    return message_snapshots


def _notification_payload(
    run: InteractiveResearchRun,
    message: Optional[InteractiveResearchMessage],
    event: str,
) -> Dict[str, Any]:
    """构造事务提交后可推送的通知 payload。

    Args:
        run: 当前研究 run。
        message: 本次新增消息。
        event: 通知事件名。

    Returns:
        已序列化的通知 payload。
    """
    return {
        "event": event,
        "run": serialize_run_summary(run),
        "message": serialize_message(message) if message is not None else None,
        "message_text": message.content if message is not None else event,
    }


def append_message(
    db: Session,
    run: InteractiveResearchRun,
    *,
    role: str,
    message_type: str,
    content: str,
    payload: Optional[Dict[str, Any]] = None,
    parent_message_id: Optional[UUID] = None,
    status: str = "completed",
    visible_to_user: bool = True,
) -> InteractiveResearchMessage:
    """向 run 的聊天流追加一条消息。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        role: 消息角色。
        message_type: 消息类型。
        content: 展示文本。
        payload: 小型结构化 payload。
        parent_message_id: 父消息 ID。
        status: 消息状态。
        visible_to_user: 是否展示给用户。

    Returns:
        已写入数据库的消息对象。
    """
    message = InteractiveResearchMessage(
        run_id=run.run_id,
        role=role,
        message_type=message_type,
        content=content,
        payload=payload or {},
        parent_message_id=parent_message_id,
        sequence_no=next_message_sequence(db, run.run_id),
        status=status,
        visible_to_user=visible_to_user,
    )
    db.add(message)
    db.flush()
    return message


def write_checkpoint(
    db: Session,
    run: InteractiveResearchRun,
    *,
    reason: str,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """更新 run 的最小恢复 checkpoint。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        reason: checkpoint 生成原因。
        extra_payload: 额外恢复上下文。

    Returns:
        已写入 run 的 checkpoint payload。
    """
    existing_payload = run.checkpoint_payload if isinstance(run.checkpoint_payload, dict) else {}
    checkpoint_payload = {
        "status": run.status,
        "current_stage": run.current_stage,
        "current_phase": run.current_phase,
        "pending_message_id": str(run.pending_message_id) if run.pending_message_id else None,
        "version": run.version,
        "reason": reason,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "llm_usage": existing_payload.get("llm_usage") or {},
        **(extra_payload or {}),
    }
    run.checkpoint_payload = checkpoint_payload
    db.flush()
    return checkpoint_payload


def accumulate_llm_usage(
    db: Session,
    run: InteractiveResearchRun,
    usage_record: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """把一次 LLM 调用的 usage 累加到 run checkpoint。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        usage_record: record_llm_usage 返回的单次调用记录。

    Returns:
        累加后的 run 级 usage 汇总。
    """
    checkpoint_payload = dict(run.checkpoint_payload or {})
    current_usage = dict(checkpoint_payload.get("llm_usage") or {})
    if not usage_record:
        return current_usage

    current_usage["calls"] = int(current_usage.get("calls") or 0) + 1
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_miss_tokens",
        "reasoning_tokens",
    ):
        current_usage[key] = int(current_usage.get(key) or 0) + int(usage_record.get(key) or 0)

    input_tokens = int(current_usage.get("input_tokens") or 0)
    cached_tokens = int(current_usage.get("cached_tokens") or 0)
    current_usage["cache_hit_rate"] = cached_tokens / input_tokens if input_tokens > 0 else 0.0
    checkpoint_payload["llm_usage"] = current_usage
    run.checkpoint_payload = checkpoint_payload
    db.flush()
    return current_usage


def persist_plan_card(
    db: Session,
    run: InteractiveResearchRun,
    *,
    plan_message: str,
    plan_preview_payload: Dict[str, Any],
    plan_payload: Dict[str, Any],
    usage_record: Optional[Dict[str, Any]],
    reason: str,
    bump_version: bool,
) -> InteractiveResearchMessage:
    """写入一轮计划卡，并同步更新 run checkpoint 和 LLM usage。

    把计划阶段每轮固定的数据库写入（重置 pending、递增版本、追加 plan_card 消息、写
    checkpoint、累加 usage）收敛到一处，调用方只需负责打开会话和提交事务。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        plan_message: 计划 Agent 本轮 Markdown 输出。
        plan_preview_payload: 计划卡轻量预览 payload。
        plan_payload: 当前完整计划 payload，写入 checkpoint。
        usage_record: 本轮计划 LLM 调用的 usage 记录。
        reason: checkpoint 生成原因。
        bump_version: 是否递增 run 版本号。

    Returns:
        已写入的 plan_card 消息。
    """
    run.pending_message_id = None
    if bump_version:
        run.version += 1
    message = append_message(
        db,
        run,
        role="assistant",
        message_type="plan_card",
        content=plan_message,
        payload={"preview": plan_preview_payload, "actions": ["approve", "cancel"]},
    )
    write_checkpoint(db, run, reason=reason, extra_payload={"plan_payload": plan_payload})
    accumulate_llm_usage(db, run, usage_record)
    return message


def transition_run(
    db: Session,
    run: InteractiveResearchRun,
    *,
    status: str,
    current_phase: Optional[str] = None,
    system_content: str,
    system_payload: Optional[Dict[str, Any]] = None,
    checkpoint_reason: str,
    checkpoint_extra: Optional[Dict[str, Any]] = None,
    clear_pending: bool = False,
    finished: bool = False,
    error_message: Optional[str] = None,
) -> InteractiveResearchMessage:
    """切换 run 状态，并写入一条系统状态消息和 checkpoint。

    收敛计划批准、追问回答、取消、失败等共用的状态切换写库：递增版本号，更新
    status/current_stage（两者始终一致），按需更新 phase、清空 pending、标记完成时间或
    错误信息，追加一条 system_status 消息并写 checkpoint。提交事务由调用方负责。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        status: 目标状态，同时写入 current_stage。
        current_phase: 目标阶段；为空时保持不变。
        system_content: 系统状态消息正文。
        system_payload: 系统状态消息 payload。
        checkpoint_reason: checkpoint 生成原因。
        checkpoint_extra: checkpoint 额外恢复上下文。
        clear_pending: 是否清空 pending_message_id。
        finished: 是否写入终态完成时间。
        error_message: 失败时写入的错误信息；为空时保持不变。

    Returns:
        已写入的 system_status 消息。
    """
    run.status = status
    run.current_stage = status
    if current_phase is not None:
        run.current_phase = current_phase
    if clear_pending:
        run.pending_message_id = None
    if finished:
        run.finished_at = datetime.now()
    if error_message is not None:
        run.error_message = error_message
    run.version += 1
    message = append_message(
        db,
        run,
        role="system",
        message_type="system_status",
        content=system_content,
        payload=system_payload or {},
    )
    write_checkpoint(db, run, reason=checkpoint_reason, extra_payload=checkpoint_extra)
    return message


def next_message_sequence(db: Session, run_id: UUID) -> int:
    """计算 run 内下一条消息序号。

    Args:
        db: 数据库会话。
        run_id: 研究 run ID。

    Returns:
        下一条从 1 开始递增的序号。
    """
    current = (
        db.query(func.max(InteractiveResearchMessage.sequence_no))
        .filter(InteractiveResearchMessage.run_id == run_id)
        .scalar()
    )
    return int(current or 0) + 1
