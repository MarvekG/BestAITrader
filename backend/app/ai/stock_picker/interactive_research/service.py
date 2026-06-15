from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.ai.json_utils import stable_json_dumps
from app.ai.llm_providers.factory import build_chat_model
from app.ai.stock_picker.interactive_research.constants import ACTIVE_RESEARCH_STATUSES, TERMINAL_RESEARCH_STATUSES
from app.ai.stock_picker.interactive_research.flow_control import FlowControlDecision, parse_flow_control_decision
from app.ai.stock_picker.interactive_research.models import InteractiveResearchMessage, InteractiveResearchRun
from app.ai.stock_picker.interactive_research.persistence import append_message, write_checkpoint
from app.ai.stock_picker.interactive_research.planning import (
    build_plan_payload,
    build_plan_preview_payload,
    parse_requirement,
)
from app.ai.stock_picker.interactive_research.serializers import serialize_message, serialize_run_summary
from app.ai.stock_picker.interactive_research.tool_registry import ToolLoaderFactory
from app.ai.stock_picker.interactive_research.workflow import (
    FLOW_CONTROL_PROTOCOL_INSTRUCTION,
    MAX_FLOW_CONTROL_RETRIES,
    InteractiveResearchWorkflow,
    LLMFactory,
)
from app.core.config import settings
from app.core.database import SessionLocal

class InteractiveResearchService:
    """聊天式 Deep Research 选股状态机服务。"""

    def __init__(
        self,
        tool_loader_factory: Optional[ToolLoaderFactory] = None,
        llm_factory: Optional[LLMFactory] = None,
    ) -> None:
        """初始化交互式研究服务。

        Args:
            tool_loader_factory: 可选工具注册表工厂；测试可注入 fake 工具。
            llm_factory: 可选 LLM 工厂；测试可注入 fake LLM。
        """
        self._llm_factory = llm_factory
        self._workflow = InteractiveResearchWorkflow(
            tool_loader_factory=tool_loader_factory,
            llm_factory=llm_factory,
            notification_callback=self._push_realtime_update,
        )

    def create_run(self, db: Session, user_id: int, request_data: Dict[str, Any]) -> InteractiveResearchRun:
        """创建聊天式研究 run，并写入首条用户消息和计划消息。

        Args:
            db: 数据库会话。
            user_id: 当前用户 ID。
            request_data: 已通过 API schema 校验的自然语言需求和约束。

        Returns:
            已持久化的研究 run。

        Raises:
            ValueError: 当前用户已有未完成 Deep Research run 时抛出。
        """
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
            raise ValueError(f"An unfinished Deep Research run already exists: {active_run.run_id}")

        parsed_requirement = parse_requirement(request_data)
        plan_payload = build_plan_payload(parsed_requirement)
        plan_preview_payload = build_plan_preview_payload(plan_payload)
        run = InteractiveResearchRun(
            user_id=user_id,
            status="awaiting_plan_approval",
            current_stage="awaiting_plan_approval",
            current_phase="planning",
            title=self._build_title(parsed_requirement["raw_requirement"]),
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
        append_message(
            db,
            run,
            role="assistant",
            message_type="plan_card",
            content="Research plan generated and awaiting approval.",
            payload={"preview": plan_preview_payload, "actions": ["approve", "cancel"]},
        )
        write_checkpoint(db, run, reason="plan_drafted", extra_payload={"plan_payload": plan_payload})
        db.commit()
        db.refresh(run)
        return run

    def list_runs(self, db: Session, user_id: int) -> List[InteractiveResearchRun]:
        """查询当前用户的研究 run 列表。

        Args:
            db: 数据库会话。
            user_id: 当前用户 ID。

        Returns:
            按创建时间倒序排列的 run 列表。
        """
        return (
            db.query(InteractiveResearchRun)
            .filter(InteractiveResearchRun.user_id == user_id)
            .order_by(InteractiveResearchRun.created_at.desc())
            .all()
        )

    def get_run(self, db: Session, run_id: UUID, user_id: int) -> Optional[InteractiveResearchRun]:
        """查询当前用户拥有的单个研究 run。

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

    async def append_user_message(
        self,
        db: Session,
        run_id: UUID,
        user_id: int,
        content: str,
        payload: Optional[Dict[str, Any]] = None,
        background_tasks: Optional[Any] = None,
    ) -> InteractiveResearchMessage:
        """向聊天流追加用户输入，并按当前状态处理动态输入。

        Args:
            db: 数据库会话。
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            content: 用户输入文本。
            payload: 可选小型结构化 payload。
            background_tasks: FastAPI BackgroundTasks 实例（可选）。

        Returns:
            已创建的用户消息。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: 终态 run 不允许继续追加时抛出。
        """
        run = self._require_run(db, run_id, user_id)
        if run.status in TERMINAL_RESEARCH_STATUSES:
            raise ValueError("Terminal runs cannot accept new messages")

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

        if run.status == "awaiting_plan_approval":
            await self._handle_plan_llm_decision(db, run, content.strip(), background_tasks=background_tasks)
        elif run.status == "awaiting_user_input":
            await self._handle_user_answer(db, run, message, background_tasks=background_tasks)
        else:
            write_checkpoint(
                db,
                run,
                reason="queued_user_input",
                extra_payload={"queued_message_id": str(message.message_id)},
            )

        db.commit()
        db.refresh(message)
        return message

    async def process_action(
        self,
        db: Session,
        run_id: UUID,
        user_id: int,
        action: str,
        *,
        content: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        background_tasks: Optional[Any] = None,
    ) -> InteractiveResearchRun:
        """执行 run 级动作。

        Args:
            db: 数据库会话。
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            action: approve 或 cancel。
            content: 动作说明文本。
            payload: 动作结构化 payload。
            background_tasks: FastAPI BackgroundTasks 实例（可选）。

        Returns:
            更新后的 run。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: action 或状态不允许时抛出。
        """
        if action == "approve":
            return await self.approve_plan(db, run_id, user_id, background_tasks=background_tasks)
        if action == "cancel":
            reason = content or str((payload or {}).get("reason") or "")
            return self.cancel_run(db, run_id, user_id, reason=reason)
        raise ValueError(f"Unsupported action: {action}")

    async def approve_plan(
        self, db: Session, run_id: UUID, user_id: int, background_tasks: Optional[Any] = None
    ) -> InteractiveResearchRun:
        """确认计划并启动单 Agent loop。

        Args:
            db: 数据库会话。
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            background_tasks: FastAPI BackgroundTasks 实例（可选）。

        Returns:
            更新后的 run。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: run 不处于等待计划确认状态时抛出。
        """
        run = self._require_run(db, run_id, user_id)
        if run.status != "awaiting_plan_approval":
            raise ValueError("Only awaiting_plan_approval runs can approve a plan")
        plan_payload = self._plan_payload_from_checkpoint(run)

        append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content="Research plan approved.",
            payload={},
        )
        run.status = "researching"
        run.current_stage = "researching"
        run.current_phase = "research"
        run.version += 1
        write_checkpoint(db, run, reason="plan_approved", extra_payload={"plan_payload": plan_payload})
        db.commit()

        if background_tasks is not None:
            background_tasks.add_task(self.execute_workflow_background, run.run_id, plan_payload)

        db.refresh(run)
        return run

    def cancel_run(
        self,
        db: Session,
        run_id: UUID,
        user_id: int,
        reason: Optional[str] = None,
    ) -> InteractiveResearchRun:
        """取消当前用户的研究 run。

        Args:
            db: 数据库会话。
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            reason: 可选取消原因。

        Returns:
            已取消的 run。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: 终态 run 不能重复取消时抛出。
        """
        run = self._require_run(db, run_id, user_id)
        if run.status in TERMINAL_RESEARCH_STATUSES:
            raise ValueError("Terminal runs cannot be cancelled again")

        run.status = "cancelled"
        run.current_stage = "cancelled"
        run.pending_message_id = None
        run.finished_at = datetime.now()
        run.version += 1
        append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content="Interactive research run cancelled.",
            payload={"reason": reason or ""},
        )
        write_checkpoint(db, run, reason="cancelled")
        db.commit()
        db.refresh(run)
        return run

    def get_messages(
        self,
        db: Session,
        run_id: UUID,
        user_id: int,
        *,
        visible_only: bool = True,
    ) -> List[InteractiveResearchMessage]:
        """查询 run 的聊天消息流。

        Args:
            db: 数据库会话。
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            visible_only: 是否只返回用户可见消息。

        Returns:
            按 sequence_no 升序排列的消息列表。
        """
        if not self.get_run(db, run_id, user_id):
            return []
        query = db.query(InteractiveResearchMessage).filter(InteractiveResearchMessage.run_id == run_id)
        if visible_only:
            query = query.filter(InteractiveResearchMessage.visible_to_user.is_(True))
        return query.order_by(InteractiveResearchMessage.sequence_no.asc()).all()

    def serialize_run_summary(self, run: InteractiveResearchRun) -> Dict[str, Any]:
        """序列化 run 摘要。

        Args:
            run: 研究 run。

        Returns:
            run 摘要字典。
        """
        return serialize_run_summary(run)

    def serialize_message(self, message: InteractiveResearchMessage) -> Dict[str, Any]:
        """序列化消息。

        Args:
            message: 聊天消息。

        Returns:
            消息响应字典。
        """
        return serialize_message(message)

    async def _push_realtime_update(self, payload: Dict[str, Any]) -> None:
        """通过 WebSocket 推送交互式研究更新。

        Args:
            payload: workflow 已序列化的 run、message 和事件数据。
        """
        from app.websocket.manager import ws_manager

        run_payload = payload.get("run") if isinstance(payload.get("run"), dict) else {}
        message_payload = payload.get("message") if isinstance(payload.get("message"), dict) else None
        display_message = None
        if message_payload:
            display_message = {
                "message_type": message_payload.get("display_type") or message_payload.get("role"),
                "markdown": message_payload.get("markdown") or message_payload.get("content") or "",
                "execution_status": message_payload.get("execution_status") or message_payload.get("status"),
            }
        message_text = (
            (display_message or {}).get("markdown")
            or payload.get("message_text")
            or payload.get("event")
            or ""
        )
        await ws_manager.send_stock_picker_update(
            run_id=str(run_payload.get("run_id") or ""),
            stage=str(run_payload.get("current_stage") or ""),
            status=str(run_payload.get("status") or ""),
            message=str(message_text),
            payload={
                "domain": "interactive_research",
                "event": payload.get("event"),
                "run": run_payload,
                "message": message_payload,
                "display_message": display_message,
            },
        )

    def _require_run(self, db: Session, run_id: UUID, user_id: int) -> InteractiveResearchRun:
        """获取当前用户拥有的 run，不存在时抛出错误。

        Args:
            db: 数据库会话。
            run_id: 研究 run ID。
            user_id: 当前用户 ID。

        Returns:
            当前用户拥有的 run。

        Raises:
            LookupError: run 不存在时抛出。
        """
        run = self.get_run(db, run_id, user_id)
        if run is None:
            raise LookupError("Deep Research run does not exist")
        return run

    async def _handle_plan_llm_decision(
        self,
        db: Session,
        run: InteractiveResearchRun,
        content: str,
        background_tasks: Optional[Any] = None,
    ) -> None:
        """用 LLM 决定计划阶段是继续迭代、提问还是开始研究。

        Args:
            db: 数据库会话。
            run: 当前研究 run。
            content: 用户本轮输入。
            background_tasks: FastAPI BackgroundTasks 实例（可选）。
        """
        plan_payload = self._plan_payload_from_checkpoint(run)
        messages = self._build_plan_turn_messages(run, content, plan_payload)
        decision = await self._invoke_plan_flow_control(messages)
        if decision.status == "ask":
            message = append_message(
                db,
                run,
                role="assistant",
                message_type="assistant_question",
                content=decision.message,
                payload={"phase": "planning"},
            )
            run.pending_message_id = message.message_id
            write_checkpoint(db, run, reason="plan_question", extra_payload={"plan_payload": plan_payload})
            return
        if decision.status == "done":
            plan_payload = self._update_plan_payload_from_message(plan_payload, content, decision.message)
            write_checkpoint(db, run, reason="plan_llm_done", extra_payload={"plan_payload": plan_payload})
            await self.approve_plan(db, run.run_id, run.user_id, background_tasks=background_tasks)
            return
        self._patch_plan_from_user_input(db, run, content, plan_message=decision.message)

    def _patch_plan_from_user_input(
        self,
        db: Session,
        run: InteractiveResearchRun,
        content: str,
        plan_message: Optional[str] = None,
    ) -> None:
        """把计划确认阶段的新输入追加到计划上下文。

        Args:
            db: 数据库会话。
            run: 当前研究 run。
            content: 用户补充要求。
            plan_message: LLM 生成的计划说明；为空时使用默认说明。
        """
        if run.status != "awaiting_plan_approval":
            raise ValueError("Only awaiting_plan_approval runs can update a plan")
        if not content.strip():
            raise ValueError("Plan update instruction cannot be empty")

        plan_payload = self._update_plan_payload_from_message(
            self._plan_payload_from_checkpoint(run),
            content,
            plan_message,
        )
        plan_preview_payload = build_plan_preview_payload(plan_payload)
        run.pending_message_id = None
        run.version += 1
        append_message(
            db,
            run,
            role="assistant",
            message_type="plan_card",
            content=plan_message or "Research plan updated from the additional input and awaiting approval.",
            payload={"preview": plan_preview_payload, "actions": ["approve", "cancel"]},
        )
        write_checkpoint(db, run, reason="plan_updated", extra_payload={"plan_payload": plan_payload})

    def _update_plan_payload_from_message(
        self,
        plan_payload: Dict[str, Any],
        user_content: str,
        plan_message: Optional[str],
    ) -> Dict[str, Any]:
        """根据用户输入和 LLM 计划说明更新计划 payload。

        Args:
            plan_payload: 当前计划 payload。
            user_content: 用户本轮输入。
            plan_message: LLM 生成的计划说明。

        Returns:
            更新后的计划 payload。
        """
        updated_plan = dict(plan_payload)
        user_inputs = list(updated_plan.get("user_inputs") or [])
        user_inputs.append(
            {"content": user_content.strip(), "created_at": datetime.now().isoformat(timespec="seconds")}
        )
        updated_plan["user_inputs"] = user_inputs
        updated_plan["objective_summary"] = plan_message or (
            f"{self._plan_objective(updated_plan)}\nAdditional input: {user_content.strip()}"
        )
        return updated_plan

    def _build_plan_turn_messages(
        self,
        run: InteractiveResearchRun,
        content: str,
        plan_payload: Dict[str, Any],
    ) -> List[Any]:
        """构造计划阶段 LLM 流程控制消息。

        Args:
            run: 当前研究 run。
            content: 用户本轮输入。
            plan_payload: 当前计划 payload。

        Returns:
            LangChain 消息列表。
        """
        prompt = (
            "You control the planning stage for an interactive stock research chat. "
            "Do not call tools. Decide whether to continue refining the plan, ask one user question, "
            "or mark the plan done. "
            f"{FLOW_CONTROL_PROTOCOL_INSTRUCTION} "
            "Use continue to update the plan, ask when a user answer is needed, done when the user "
            "clearly wants to start research.\n\n"
            f"Current plan:\n{stable_json_dumps(plan_payload)}"
        )
        return [
            SystemMessage(content=prompt),
            HumanMessage(content=f"Run requirement: {run.raw_requirement}\nUser input: {content}"),
        ]

    def _build_llm(self) -> Any:
        """构造计划阶段使用的 LLM。

        Returns:
            LangChain chat model。
        """
        if self._llm_factory:
            return self._llm_factory()
        return build_chat_model(model=settings.LLM_MODEL, temperature=0.2)

    async def _invoke_plan_flow_control(self, messages: List[Any]) -> FlowControlDecision:
        """调用计划阶段 LLM，并在协议错误时要求重输。

        Args:
            messages: 计划阶段 LLM 消息上下文。

        Returns:
            解析后的流程控制决策。

        Raises:
            ValueError: 多次重试后仍无法解析协议时抛出。
        """
        llm = self._build_llm()
        retry_count = 0
        while True:
            response = await llm.ainvoke(messages)
            messages.append(response)
            try:
                return parse_flow_control_decision(response.content)
            except ValueError as exc:
                if retry_count >= MAX_FLOW_CONTROL_RETRIES:
                    raise ValueError(f"Plan LLM flow-control protocol invalid after retry: {exc}") from exc
                retry_count += 1
                messages.append(
                    HumanMessage(
                        content=(
                            "Your previous response did not follow the required flow-control protocol and was not "
                            f"shown to the user. Parser error: {exc}.\n"
                            f"{FLOW_CONTROL_PROTOCOL_INSTRUCTION}\n"
                            "Re-output the planning decision now with the ACTION line first and the body starting "
                            "on the second line."
                        )
                    )
                )

    def _plan_objective(self, plan_payload: Dict[str, Any]) -> str:
        """读取计划目标摘要。

        Args:
            plan_payload: 当前计划 payload。

        Returns:
            计划目标摘要。
        """
        return str(plan_payload.get("objective_summary") or "").strip()

    async def _handle_user_answer(
        self,
        db: Session,
        run: InteractiveResearchRun,
        message: InteractiveResearchMessage,
        background_tasks: Optional[Any] = None,
    ) -> None:
        """处理 awaiting_user_input 状态下的用户回答。

        Args:
            db: 数据库会话。
            run: 当前研究 run。
            message: 用户回答消息。
            background_tasks: FastAPI BackgroundTasks 实例（可选）。
        """
        plan_payload = self._plan_payload_from_checkpoint(run)
        run.pending_message_id = None
        run.status = "researching"
        run.current_stage = "researching"
        run.current_phase = "research"
        run.version += 1
        append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content="Answer received. Research will continue from the checkpoint.",
            payload={"answer_message_id": str(message.message_id)},
        )
        write_checkpoint(
            db,
            run,
            reason="user_answer_received",
            extra_payload={"answer_message_id": str(message.message_id), "plan_payload": plan_payload},
        )
        db.commit()

        if background_tasks is not None:
            background_tasks.add_task(self.execute_workflow_background, run.run_id, plan_payload)

    def _plan_payload_from_checkpoint(self, run: InteractiveResearchRun) -> Dict[str, Any]:
        """从 run checkpoint 中恢复已确认计划。

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

    def _build_title(self, requirement: str) -> str:
        """根据原始需求生成聊天标题。

        Args:
            requirement: 原始用户需求。

        Returns:
            最多 60 字的标题。
        """
        normalized = " ".join(requirement.split())
        return normalized[:60] or "Deep Research Stock Picker"

    def _handle_workflow_exception(
        self,
        db: Session,
        run: InteractiveResearchRun,
        exc: Exception,
    ) -> None:
        """处理 workflow 执行异常，写入失败状态。

        Args:
            db: 数据库会话。
            run: 当前研究 run。
            exc: 捕获的异常。
        """
        import logging
        import traceback

        logger = logging.getLogger(__name__)
        logger.error(
            "interactive research workflow failed",
            extra={"run_id": str(run.run_id), "exception": str(exc), "traceback": traceback.format_exc()},
        )

        run.status = "failed"
        run.current_stage = "failed"
        run.error_message = f"Workflow failed: {exc}"
        run.version += 1
        append_message(
            db,
            run,
            role="system",
            message_type="system_status",
            content=run.error_message,
            payload={"exception_type": type(exc).__name__, "exception_message": str(exc)},
        )
        write_checkpoint(db, run, reason="workflow_exception")

    async def execute_workflow_background(
        self,
        run_id: UUID,
        plan_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """后台执行 workflow（用于 FastAPI BackgroundTasks）。

        Args:
            run_id: 研究 run ID。
            plan_payload: 已确认计划 payload。
        """
        effective_plan_payload = plan_payload
        if effective_plan_payload is None:
            with SessionLocal() as db:
                run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
                if run is None:
                    return
                effective_plan_payload = self._plan_payload_from_checkpoint(run)

        try:
            await self._workflow.execute(run_id, effective_plan_payload)
        except Exception as exc:
            with SessionLocal() as db:
                run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
                if run is None:
                    return
                self._handle_workflow_exception(db, run, exc)
                db.commit()


interactive_research_service = InteractiveResearchService()
