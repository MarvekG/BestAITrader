from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import app.core.database as database_module
from app.ai.stock_picker.interactive_research.constants import (
    ACTIVE_RESEARCH_STATUSES,
    TERMINAL_RESEARCH_STATUSES,
    phase_instructions,
)
from app.ai.stock_picker.interactive_research.models import (
    InteractiveResearchMessage,
    InteractiveResearchRun,
)
from app.ai.stock_picker.interactive_research.serializers import (
    serialize_message,
    serialize_run_summary,
)
from app.core.i18n import i18n_service
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究持久化层翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


PHASE_PLANNING = "planning"
PHASE_RESEARCH = "research"
CONTEXT_SUMMARY_PAYLOAD_KEY = "context_summary"


def _message_snapshot(message: InteractiveResearchMessage) -> dict[str, Any]:
    """把消息 ORM 对象转换为恢复快照。

    Args:
        message: 聊天消息 ORM 对象。

    Returns:
        包含恢复所需字段的消息快照。
    """
    return {
        "message_id": str(message.message_id),
        "sequence_no": int(message.sequence_no),
        "role": message.role,
        "message_type": message.message_type,
        "content": message.content or "",
        "payload": message.payload or {},
    }


async def create_run_record(
    user_id: int,
    request_data: dict[str, Any],
    *,
    title: str,
) -> dict[str, Any]:
    """创建研究 run 并写入首条用户消息。

    Args:
        user_id: 当前用户 ID。
        request_data: 已校验的用户研究请求。
        title: 调用方生成的聊天标题。

    Returns:
        新建 run 的基础快照。

    Raises:
        ValueError: 当前用户已有活跃 run 时抛出。
    """
    async with database_module.AsyncSessionLocal() as db:
        await _lock_user_active_runs(db, user_id)
        active_run = (await db.execute(
            select(InteractiveResearchRun)
            .where(
                InteractiveResearchRun.user_id == user_id,
                InteractiveResearchRun.status.in_(ACTIVE_RESEARCH_STATUSES),
            )
            .order_by(InteractiveResearchRun.created_at.desc())
            .with_for_update()
            .limit(1)
        )).scalar_one_or_none()
        if active_run:
            raise ValueError(_t("errors.active_run_exists", run_id=active_run.run_id))

        requirement = str(request_data["requirement"]).strip()
        run_config = {"max_iterations": int(request_data["max_iterations"])}
        run = InteractiveResearchRun(
            user_id=user_id,
            status="awaiting_plan_approval",
            current_stage="awaiting_plan_approval",
            current_phase="planning",
            title=title,
            raw_requirement=requirement,
            checkpoint_payload={
                "status": "awaiting_plan_approval",
                "current_phase": "planning",
                "run_config": run_config,
            },
        )
        db.add(run)
        await db.flush()
        await append_message(
            db,
            run,
            role="user",
            message_type="user_input",
            content=requirement,
            payload={"request": request_data},
        )
        await db.commit()
        await db.refresh(run)
        return {"run_id": run.run_id, "raw_requirement": run.raw_requirement}


async def list_run_records(user_id: int) -> list[InteractiveResearchRun]:
    """查询用户研究 run 列表。

    Args:
        user_id: 当前用户 ID。

    Returns:
        按创建时间倒序排列的 run 列表。
    """
    async with database_module.AsyncSessionLocal() as db:
        return (await db.execute(
            select(InteractiveResearchRun)
            .where(InteractiveResearchRun.user_id == user_id)
            .order_by(InteractiveResearchRun.created_at.desc())
        )).scalars().all()


async def get_run_record(run_id: UUID, user_id: int) -> InteractiveResearchRun | None:
    """查询用户拥有的单个研究 run。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。

    Returns:
        找到时返回 run，否则返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        return (await db.execute(
            select(InteractiveResearchRun).where(
                InteractiveResearchRun.run_id == run_id,
                InteractiveResearchRun.user_id == user_id,
            )
        )).scalar_one_or_none()


async def delete_run_record(run_id: UUID, user_id: int) -> bool:
    """删除用户拥有的研究 run。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。

    Returns:
        删除成功返回 True，否则返回 False。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_user_run(db, run_id, user_id, for_update=True)
        if run is None:
            return False
        await db.execute(delete(InteractiveResearchMessage).where(InteractiveResearchMessage.run_id == run_id))
        await db.delete(run)
        await db.commit()
        return True


async def append_user_message_record(
    run_id: UUID,
    user_id: int,
    content: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_user_run(db, run_id, user_id)
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
        message = await append_message(
            db,
            run,
            role="user",
            message_type="user_input",
            content=content.strip(),
            payload=message_payload,
            parent_message_id=parent_message_id,
            status=message_status,
        )
        if run_status == "awaiting_user_input":
            await transition_run(
                db,
                run,
                status="researching",
                current_phase="research",
                system_content=_t("messages.answer_received"),
                system_payload={"answer_message_id": str(message.message_id)},
                checkpoint_reason="user_answer_received",
                checkpoint_extra={"answer_message_id": str(message.message_id)},
                clear_pending=True,
            )
        elif run_status not in {"awaiting_plan_approval", "awaiting_user_input"}:
            await write_checkpoint(
                db,
                run,
                reason="queued_user_input",
                extra_payload={"queued_message_id": str(message.message_id)},
            )
        await db.commit()
        await db.refresh(message)
        return {"message": message, "run_status": run_status}


async def approve_plan_record(run_id: UUID, user_id: int) -> dict[str, Any]:
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
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_user_run(db, run_id, user_id, for_update=True)
        if run is None:
            raise LookupError(_t("errors.run_not_found"))
        if run.status != "awaiting_plan_approval":
            raise ValueError(_t("errors.only_awaiting_plan_approval_can_approve"))
        await transition_run(
            db,
            run,
            status="researching",
            current_phase="research",
            system_content=_t("messages.plan_approved"),
            checkpoint_reason="plan_approved",
        )
        await db.commit()
        await db.refresh(run)
        return {"run": run}


async def cancel_run_record(run_id: UUID, user_id: int, reason: str | None = None) -> InteractiveResearchRun:
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
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_user_run(db, run_id, user_id, for_update=True)
        if run is None:
            raise LookupError(_t("errors.run_not_found"))
        if run.status in TERMINAL_RESEARCH_STATUSES:
            raise ValueError(_t("errors.terminal_cannot_cancel"))
        await transition_run(
            db,
            run,
            status="cancelled",
            system_content=_t("messages.cancelled"),
            system_payload={"reason": reason or ""},
            checkpoint_reason="cancelled",
            clear_pending=True,
            finished=True,
        )
        await db.commit()
        await db.refresh(run)
        return run


async def list_message_records(run_id: UUID, user_id: int, *, visible_only: bool = True) -> list[InteractiveResearchMessage]:
    """查询 run 的消息流。

    Args:
        run_id: 研究 run ID。
        user_id: 当前用户 ID。
        visible_only: 是否只返回用户可见消息。

    Returns:
        按 sequence_no 升序排列的消息列表。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_user_run(db, run_id, user_id)
        if run is None:
            return []
        query = select(InteractiveResearchMessage).where(InteractiveResearchMessage.run_id == run_id)
        if visible_only:
            query = query.where(InteractiveResearchMessage.visible_to_user.is_(True))
        return (await db.execute(query.order_by(InteractiveResearchMessage.sequence_no.asc()))).scalars().all()


async def fail_run_record(run_id: UUID, error_text: str, exception_type: str, exception_message: str) -> None:
    """把 run 标记为失败并写入系统消息。

    Args:
        run_id: 研究 run ID。
        error_text: 用户可见错误文案。
        exception_type: 异常类型名称。
        exception_message: 异常消息。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return
        await transition_run(
            db,
            run,
            status="failed",
            system_content=error_text,
            system_payload={"exception_type": exception_type, "exception_message": exception_message},
            checkpoint_reason="workflow_exception",
            error_message=error_text,
        )
        await db.commit()


async def load_plan_turn_record(run_id: UUID) -> dict[str, Any] | None:
    """读取计划阶段本轮所需的 run 与历史消息快照。

    Args:
        run_id: 研究 run ID。

    Returns:
        run 存在时返回状态、运行配置和恢复消息；否则返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id)
        if run is None:
            return None
        persisted = await _load_plan_messages(db, run)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "max_iterations": _max_iterations_from_checkpoint(run),
            "persisted_messages": persisted,
        }


async def persist_plan_card_record(
    run_id: UUID,
    *,
    plan_message: str,
    reason: str,
    bump_version: bool,
) -> dict[str, Any]:
    """打开事务写入计划卡。

    Args:
        run_id: 研究 run ID。
        plan_message: 计划 Agent Markdown 输出。
        reason: checkpoint 原因。
        bump_version: 是否递增 run 版本。

    Returns:
        写入结果和可推送通知 payload。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            raise LookupError(_t("errors.run_not_found"))
        if run.status != "awaiting_plan_approval":
            return {"persisted": False, "notification": None}
        message = await persist_plan_card(
            db,
            run,
            plan_message=plan_message,
            reason=reason,
            bump_version=bump_version,
        )
        payload = _notification_payload(run, message, "plan_card")
        await db.commit()
        return {"persisted": True, "notification": payload}


async def persist_context_summary_record(
    run_id: UUID,
    *,
    summary: str,
    phase: str,
) -> dict[str, Any] | None:
    """把压缩摘要作为普通可见 assistant 消息保存。

    Args:
        run_id: 研究 run ID。
        summary: 五栏摘要正文。
        phase: 摘要对应的 `planning` 或 `research` 阶段。

    Returns:
        已提交消息的通知 payload；run 不存在或已结束时返回 None。
    """
    if phase not in {PHASE_PLANNING, PHASE_RESEARCH}:
        raise ValueError(f"unsupported context summary phase: {phase}")

    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None or run.status in TERMINAL_RESEARCH_STATUSES:
            return None
        if phase == PHASE_PLANNING and run.status != "awaiting_plan_approval":
            return None
        if phase == PHASE_RESEARCH and run.current_phase != PHASE_RESEARCH:
            return None
        message = await append_message(
            db,
            run,
            role="assistant",
            message_type="assistant_text",
            content=summary,
            payload={CONTEXT_SUMMARY_PAYLOAD_KEY: phase},
            visible_to_user=True,
        )
        notification = _notification_payload(run, message, "assistant_text")
        await db.commit()
        return notification


async def start_research_run_record(run_id: UUID) -> dict[str, Any] | None:
    """启动研究 loop 并返回运行快照和通知 payload。

    Returns:
        run 存在时返回快照和通知 payload；否则返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return None
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return None
        queued_messages = await _process_queued_user_inputs(db, run)
        summary_card = await _load_latest_context_summary(db, run, PHASE_RESEARCH)
        research_messages: list[dict[str, Any]] = []
        if summary_card:
            queued_ids = {message["message_id"] for message in queued_messages}
            research_messages = [
                summary_card,
                *[
                    item
                    for item in await _load_research_user_inputs(
                        db,
                        run,
                        summary_card["sequence_no"],
                    )
                    if item["message_id"] not in queued_ids
                ],
            ]
        run.status = "researching"
        run.current_stage = "researching"
        run.current_phase = "research"
        run.version += 1
        current_checkpoint = run.checkpoint_payload or {}
        await write_checkpoint(
            db,
            run,
            reason="agent_loop_started",
            extra_payload={
                "answer_message_id": current_checkpoint.get("answer_message_id"),
                "queued_message_ids": [message["message_id"] for message in queued_messages],
            },
        )
        message = await append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content=_t("messages.research_started"),
            payload={"phase_instruction": phase_instructions()["research"]},
        )
        payload = _notification_payload(run, message, "research_started")
        plan_conversation = _build_plan_conversation_snapshot(await _load_plan_messages(db, run))
        snapshot = {
            "user_id": run.user_id,
            "raw_requirement": run.raw_requirement,
            "max_iterations": _max_iterations_from_checkpoint(run),
            "queued_before": queued_messages,
            "plan_conversation": plan_conversation,
            "research_messages": research_messages,
        }
        await db.commit()
        return {"snapshot": snapshot, "notification": payload}


async def append_tool_start_record(
    run_id: UUID,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_call_id: str,
) -> dict[str, Any]:
    """记录工具开始调用消息。

    Args:
        run_id: 研究 run ID。
        tool_name: 工具名称。
        tool_args: 工具参数。
        tool_call_id: LLM 工具调用 ID。

    Returns:
        新消息 ID 和通知 payload；run 不存在时消息 ID 为空。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return {"message_id": "", "notification": None}
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return {"message_id": "", "notification": None}
        message = await append_message(
            db,
            run,
            role="tool",
            message_type="tool_start",
            content=_t("messages.tool_start", tool_name=tool_name),
            payload={"tool_name": tool_name, "arguments": tool_args, "tool_call_id": tool_call_id},
        )
        payload = _notification_payload(run, message, "tool_start")
        message_id = str(message.message_id)
        await db.commit()
        return {"message_id": message_id, "notification": payload}


async def append_tool_result_and_progress_record(
    run_id: UUID,
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    tool_call_id: str,
    start_message_id: str,
    success: bool,
    result_text: str,
    result_content: str,
) -> list[dict[str, Any]]:
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
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return []
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return []
        result_message = await append_message(
            db,
            run,
            role="tool",
            message_type="tool_result",
            content=result_content,
            status="completed" if success else "failed",
            payload={
                "tool_name": tool_name,
                "arguments": tool_args,
                "tool_call_id": tool_call_id,
                "start_message_id": start_message_id,
                "success": success,
                "result_preview": result_text,
            },
        )
        progress_message = await append_message(
            db,
            run,
            role="assistant",
            message_type="progress_update",
            content=_t("messages.tool_completed" if success else "messages.tool_failed", tool_name=tool_name),
            payload={
                "tool_name": tool_name,
                "status": _t("messages.tool_call_success" if success else "messages.tool_call_failed"),
            },
        )
        current_checkpoint = run.checkpoint_payload or {}
        await write_checkpoint(
            db,
            run,
            reason="tool_step_completed",
            extra_payload={
                "answer_message_id": current_checkpoint.get("answer_message_id"),
                "last_tool_name": tool_name,
                "last_tool_success": success,
            },
        )
        payloads = [
            _notification_payload(run, result_message, "tool_result"),
            _notification_payload(run, progress_message, "progress_update"),
        ]
        await db.commit()
        return payloads


async def synthesize_final_message_record(
    run_id: UUID,
    *,
    tool_trace: list[dict[str, Any]],
    final_content: str,
    stopped_by_iteration_limit: bool,
    iteration_budget: int,
) -> list[dict[str, Any]]:
    """写入最终答案和完成状态。

    Args:
        run_id: 研究 run ID。
        tool_trace: 工具调用轨迹。
        final_content: LLM 最终回答。
        stopped_by_iteration_limit: 是否因预算耗尽停止。
        iteration_budget: 本轮最大迭代次数。

    Returns:
        需要推送的通知 payload 列表。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return []
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return []
        run.status = "synthesizing"
        run.current_stage = "synthesizing"
        run.current_phase = "synthesis"
        run.version += 1
        final_payload = {
            "phase_instruction": phase_instructions()["synthesis"],
            "requirement_summary": run.raw_requirement,
            "answer_markdown": final_content,
            "stopped_by_iteration_limit": stopped_by_iteration_limit,
            "iteration_budget": iteration_budget,
            "evidence_summary": {
                "tool_call_count": len(tool_trace),
                "tool_names": [item.get("name") for item in tool_trace],
            },
            "tool_trace": tool_trace,
        }
        final_message = await append_message(
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
        await write_checkpoint(db, run, reason="final_message_created")
        status_message = await append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content=_t("messages.completed"),
            payload={},
        )
        completed_notification = _notification_payload(run, status_message, "completed")
        await db.commit()
        return [final_notification, completed_notification]


async def pause_for_user_question_record(
    run_id: UUID,
    question_content: str,
    *,
    compaction_summary: str | None = None,
) -> list[dict[str, Any]] | None:
    """暂停研究并在同一事务写入追问消息和摘要 card。

    Args:
        run_id: 研究 run ID。
        question_content: LLM 生成的追问。
        compaction_summary: 暂停前生成的完整研究摘要。

    Returns:
        新增消息的通知 payload 列表；run 不存在时返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return None
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return None
        run.status = "awaiting_user_input"
        run.current_stage = "awaiting_user_input"
        run.version += 1
        notifications = []
        if compaction_summary:
            summary_message = await append_message(
                db,
                run,
                role="assistant",
                message_type="assistant_text",
                content=compaction_summary,
                payload={CONTEXT_SUMMARY_PAYLOAD_KEY: PHASE_RESEARCH},
                visible_to_user=True,
            )
            notifications.append(_notification_payload(run, summary_message, "assistant_text"))
        question = await append_message(
            db,
            run,
            role="assistant",
            message_type="assistant_question",
            content=question_content,
            payload={"reason": "agent_asked_user"},
        )
        run.pending_message_id = question.message_id
        notifications.append(_notification_payload(run, question, "assistant_question"))
        await write_checkpoint(db, run, reason="agent_asked_user")
        await db.commit()
        return notifications


async def append_assistant_text_record(run_id: UUID, content: str) -> dict[str, Any] | None:
    """追加研究过程中的 assistant 文本。

    Args:
        run_id: 研究 run ID。
        content: 文本内容。

    Returns:
        通知 payload；run 不存在时返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return None
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return None
        message = await append_message(
            db,
            run,
            role="assistant",
            message_type="assistant_text",
            content=content,
            payload={},
        )
        await write_checkpoint(db, run, reason="assistant_text")
        payload = _notification_payload(run, message, "assistant_text")
        await db.commit()
        return payload


async def process_queued_user_inputs_record(run_id: UUID) -> list[dict[str, str]]:
    """处理运行中排队的用户输入。

    Args:
        run_id: 研究 run ID。

    Returns:
        已处理的排队消息快照。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return []
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return []
        queued_messages = await _process_queued_user_inputs(db, run)
        await db.commit()
        return queued_messages


async def append_queued_input_status_record(run_id: UUID, queued_messages: list[dict[str, str]]) -> dict[str, Any] | None:
    """记录排队输入已并入上下文。

    Args:
        run_id: 研究 run ID。
        queued_messages: 已处理的排队消息快照。

    Returns:
        通知 payload；run 不存在时返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id, for_update=True)
        if run is None:
            return None
        if run.status in TERMINAL_RESEARCH_STATUSES:
            return None
        message = await append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content=_t("messages.queued_input_appended"),
            payload={"queued_message_ids": [message["message_id"] for message in queued_messages]},
        )
        payload = _notification_payload(run, message, "queued_input_appended")
        await db.commit()
        return payload


async def build_recent_chat_messages_record(run_id: UUID) -> list[dict[str, Any]]:
    """构造给 agent 使用的最近聊天消息。

    Args:
        run_id: 研究 run ID。

    Returns:
        最近消息的轻量结构。
    """
    async with database_module.AsyncSessionLocal() as db:
        run = await _get_run(db, run_id)
        if run is None:
            return []
        messages = (await db.execute(
            select(InteractiveResearchMessage)
            .where(InteractiveResearchMessage.run_id == run.run_id)
            .order_by(InteractiveResearchMessage.sequence_no.desc())
            .limit(20)
        )).scalars().all()
        return [
            {"role": message.role, "message_type": message.message_type, "content": message.content}
            for message in reversed(messages)
        ]


async def _get_run(
    db: AsyncSession,
    run_id: UUID,
    *,
    for_update: bool = False,
) -> InteractiveResearchRun | None:
    """在当前会话中按 ID 查询 run。

    Args:
        db: 数据库会话。
        run_id: 研究 run ID。
        for_update: 是否锁定 run 行用于后续写入。

    Returns:
        找到时返回 run，否则返回 None。
    """
    query = select(InteractiveResearchRun).where(InteractiveResearchRun.run_id == run_id)
    if for_update:
        query = query.with_for_update()
    return (await db.execute(
        query
    )).scalar_one_or_none()


async def _get_user_run(
    db: AsyncSession,
    run_id: UUID,
    user_id: int,
    *,
    for_update: bool = False,
) -> InteractiveResearchRun | None:
    """在当前会话中查询用户拥有的 run。

    Args:
        db: 数据库会话。
        run_id: 研究 run ID。
        user_id: 当前用户 ID。
        for_update: 是否锁定 run 行用于后续写入。

    Returns:
        找到时返回 run，否则返回 None。
    """
    query = select(InteractiveResearchRun).where(
        InteractiveResearchRun.run_id == run_id,
        InteractiveResearchRun.user_id == user_id,
    )
    if for_update:
        query = query.with_for_update()
    return (await db.execute(
        query
    )).scalar_one_or_none()


async def _lock_user_active_runs(db: AsyncSession, user_id: int) -> None:
    """在 PostgreSQL 上序列化同一用户创建活跃研究 run 的检查。"""
    bind = db.get_bind()
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect_name != "postgresql":
        return
    await db.execute(select(func.pg_advisory_xact_lock(user_id)))


async def _load_plan_messages(db: AsyncSession, run: InteractiveResearchRun) -> list[dict[str, Any]]:
    """读取最新计划摘要 card 及其后的计划消息。

    Args:
        db: 数据库会话。
        run: 当前研究 run。

    Returns:
        用于重建计划对话缓存的最新消息后缀。
    """
    persisted = (await db.execute(
        select(InteractiveResearchMessage)
        .where(
            InteractiveResearchMessage.run_id == run.run_id,
            InteractiveResearchMessage.visible_to_user.is_(True),
            InteractiveResearchMessage.message_type.in_(
                ["user_input", "plan_card", "assistant_text"]
            ),
        )
        .order_by(InteractiveResearchMessage.sequence_no.asc())
    )).scalars().all()
    planning_messages = [
        item
        for item in persisted
        if (
            item.message_type == "user_input"
            and item.role == "user"
            and item.parent_message_id is None
            and not _is_queued_user_input(item)
        )
        or (item.message_type == "plan_card" and item.role == "assistant")
        or _message_summary_phase(item) == PHASE_PLANNING
    ]
    latest_summary_sequence = max(
        (int(item.sequence_no) for item in planning_messages if _is_context_summary(item)),
        default=None,
    )
    if latest_summary_sequence is not None:
        planning_messages = [
            item for item in planning_messages if int(item.sequence_no) >= latest_summary_sequence
        ]
    return [_message_snapshot(item) for item in planning_messages]


async def _load_latest_context_summary(
    db: AsyncSession,
    run: InteractiveResearchRun,
    phase: str,
) -> dict[str, Any] | None:
    """读取指定阶段最新的可见摘要消息。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        phase: `planning` 或 `research`。

    Returns:
        最新摘要消息快照；不存在时返回 None。
    """
    persisted = (await db.execute(
        select(InteractiveResearchMessage)
        .where(
            InteractiveResearchMessage.run_id == run.run_id,
            InteractiveResearchMessage.message_type == "assistant_text",
            InteractiveResearchMessage.visible_to_user.is_(True),
        )
        .order_by(InteractiveResearchMessage.sequence_no.desc())
    )).scalars().all()
    for message in persisted:
        if _message_summary_phase(message) == phase:
            return _message_snapshot(message)
    return None


def _is_context_summary(message: InteractiveResearchMessage) -> bool:
    """判断消息是否为持久化的摘要 card。"""
    payload = message.payload if isinstance(message.payload, dict) else {}
    return (
        message.message_type == "assistant_text"
        and payload.get(CONTEXT_SUMMARY_PAYLOAD_KEY) in {PHASE_PLANNING, PHASE_RESEARCH}
    )


def _is_queued_user_input(message: InteractiveResearchMessage) -> bool:
    """判断用户输入是否属于研究阶段的排队消息。"""
    payload = message.payload if isinstance(message.payload, dict) else {}
    return bool(payload.get("queued_user_input"))


def _message_summary_phase(message: InteractiveResearchMessage) -> str | None:
    """读取摘要 card 的阶段标记。"""
    if not _is_context_summary(message):
        return None
    payload = message.payload if isinstance(message.payload, dict) else {}
    phase = payload.get(CONTEXT_SUMMARY_PAYLOAD_KEY)
    return str(phase) if phase in {PHASE_PLANNING, PHASE_RESEARCH} else None


async def _load_research_user_inputs(
    db: AsyncSession,
    run: InteractiveResearchRun,
    after_sequence_no: int | None,
) -> list[dict[str, Any]]:
    """读取最近研究摘要之后的用户输入。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        after_sequence_no: 最近研究摘要 card 的序号。

    Returns:
        按序号排列、属于研究阶段且尚未进入摘要的用户输入。
    """
    query = select(InteractiveResearchMessage).where(
        InteractiveResearchMessage.run_id == run.run_id,
        InteractiveResearchMessage.role == "user",
        InteractiveResearchMessage.message_type == "user_input",
        InteractiveResearchMessage.visible_to_user.is_(True),
        InteractiveResearchMessage.status.in_(["queued", "completed"]),
    )
    if after_sequence_no is not None:
        query = query.where(InteractiveResearchMessage.sequence_no > int(after_sequence_no))
    persisted = (await db.execute(query.order_by(InteractiveResearchMessage.sequence_no.asc()))).scalars().all()
    return [
        _message_snapshot(item)
        for item in persisted
    ]


def _build_plan_conversation_snapshot(plan_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """构造计划阶段用户输入和计划卡的顺序快照。

    Args:
        plan_messages: 计划阶段可见消息快照。

    Returns:
        按原消息顺序排列的用户输入和计划卡记录。
    """
    plan_conversation = []
    user_round = 0
    plan_round = 0
    for item in plan_messages:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        if item.get("role") == "user" and item.get("content"):
            user_round += 1
            plan_conversation.append(
                {
                    "kind": "user_input",
                    "round": user_round,
                    "content": item.get("content") or "",
                }
            )
        elif item.get("role") == "assistant" and item.get("message_type") == "plan_card" and item.get("content"):
            plan_round += 1
            plan_conversation.append(
                {
                    "kind": "plan_card",
                    "round": plan_round,
                    "content": item.get("content") or "",
                }
            )
        elif payload.get(CONTEXT_SUMMARY_PAYLOAD_KEY) == PHASE_PLANNING and item.get("content"):
            plan_conversation.append(
                {
                    "kind": "context_summary",
                    "content": item.get("content") or "",
                }
            )
    return plan_conversation


async def _process_queued_user_inputs(db: AsyncSession, run: InteractiveResearchRun) -> list[dict[str, str]]:
    """在当前会话中处理排队用户输入。

    Args:
        db: 数据库会话。
        run: 当前研究 run。

    Returns:
        已处理的排队消息列表。
    """
    queued_messages = (await db.execute(
        select(InteractiveResearchMessage)
        .where(
            InteractiveResearchMessage.run_id == run.run_id,
            InteractiveResearchMessage.role == "user",
            InteractiveResearchMessage.status == "queued",
        )
        .order_by(InteractiveResearchMessage.sequence_no.asc())
    )).scalars().all()
    message_snapshots = []
    for message in queued_messages:
        message_snapshots.append(
            {
                "message_id": str(message.message_id),
                "sequence_no": int(message.sequence_no),
                "content": message.content or "",
            }
        )
        message.status = "completed"
    return message_snapshots


def _notification_payload(
    run: InteractiveResearchRun,
    message: InteractiveResearchMessage | None,
    event: str,
) -> dict[str, Any]:
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


async def append_message(
    db: AsyncSession,
    run: InteractiveResearchRun,
    *,
    role: str,
    message_type: str,
    content: str,
    payload: dict[str, Any] | None = None,
    parent_message_id: UUID | None = None,
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
        sequence_no=await next_message_sequence(db, run.run_id),
        status=status,
        visible_to_user=visible_to_user,
    )
    db.add(message)
    await db.flush()
    return message


async def write_checkpoint(
    db: AsyncSession,
    run: InteractiveResearchRun,
    *,
    reason: str,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        "run_config": existing_payload.get("run_config") or {},
        **(extra_payload or {}),
    }
    run.checkpoint_payload = checkpoint_payload
    await db.flush()
    return checkpoint_payload


def _max_iterations_from_checkpoint(run: InteractiveResearchRun) -> int:
    """从 run checkpoint 读取前端传入的研究迭代上限。

    Args:
        run: 当前研究 run。

    Returns:
        前端创建 run 时传入的最大迭代次数。
    """
    checkpoint_payload = run.checkpoint_payload if isinstance(run.checkpoint_payload, dict) else {}
    run_config = checkpoint_payload["run_config"]
    return int(run_config["max_iterations"])


async def persist_plan_card(
    db: AsyncSession,
    run: InteractiveResearchRun,
    *,
    plan_message: str,
    reason: str,
    bump_version: bool,
) -> InteractiveResearchMessage:
    """写入一轮计划卡，并同步更新 run checkpoint。

    把计划阶段每轮固定的数据库写入（重置 pending、递增版本、追加 plan_card 消息、写
    checkpoint）收敛到一处，调用方只需负责打开会话和提交事务。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        plan_message: 计划 Agent 本轮 Markdown 输出。
        reason: checkpoint 生成原因。
        bump_version: 是否递增 run 版本号。

    Returns:
        已写入的 plan_card 消息。
    """
    run.pending_message_id = None
    if bump_version:
        run.version += 1
    message = await append_message(
        db,
        run,
        role="assistant",
        message_type="plan_card",
        content=plan_message,
        payload={"actions": ["approve", "cancel"]},
    )
    await write_checkpoint(db, run, reason=reason)
    return message


async def transition_run(
    db: AsyncSession,
    run: InteractiveResearchRun,
    *,
    status: str,
    current_phase: str | None = None,
    system_content: str,
    system_payload: dict[str, Any] | None = None,
    checkpoint_reason: str,
    checkpoint_extra: dict[str, Any] | None = None,
    clear_pending: bool = False,
    finished: bool = False,
    error_message: str | None = None,
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
    message = await append_message(
        db,
        run,
        role="system",
        message_type="system_status",
        content=system_content,
        payload=system_payload or {},
    )
    await write_checkpoint(db, run, reason=checkpoint_reason, extra_payload=checkpoint_extra)
    return message


async def next_message_sequence(db: AsyncSession, run_id: UUID) -> int:
    """计算 run 内下一条消息序号。

    Args:
        db: 数据库会话。
        run_id: 研究 run ID。

    Returns:
        下一条从 1 开始递增的序号。
    """
    current = (await db.execute(
        select(func.max(InteractiveResearchMessage.sequence_no)).where(InteractiveResearchMessage.run_id == run_id)
    )).scalar_one_or_none()
    return int(current or 0) + 1
