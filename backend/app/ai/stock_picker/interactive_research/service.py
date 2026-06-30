from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from app.ai.stock_picker.interactive_research.models import InteractiveResearchMessage, InteractiveResearchRun
from app.ai.stock_picker.interactive_research.persistence import (
    append_user_message_record,
    approve_plan_record,
    cancel_run_record,
    create_run_record,
    delete_run_record,
    fail_run_record,
    get_run_record,
    list_message_records,
    list_run_records,
)
from app.ai.stock_picker.interactive_research.plan_agent import PlanAgent
from app.ai.stock_picker.interactive_research.serializers import serialize_message, serialize_run_summary
from app.ai.stock_picker.interactive_research.tool_registry import ToolLoaderFactory
from app.ai.stock_picker.interactive_research.research_agent import (
    InteractiveResearchAgent,
    LLMFactory,
)
from app.core.i18n import i18n_service
from app.core.logger import get_logger


logger = get_logger(__name__)


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究服务翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


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
        self._plan_agent = PlanAgent(
            tool_loader_factory=tool_loader_factory,
            llm_factory=llm_factory,
            notification_callback=self._push_realtime_update,
        )
        self._research_agent = InteractiveResearchAgent(
            tool_loader_factory=tool_loader_factory,
            llm_factory=llm_factory,
            notification_callback=self._push_realtime_update,
        )

    async def create_run(
        self,
        user_id: int,
        request_data: Dict[str, Any],
        background_tasks: Any,
    ) -> InteractiveResearchRun:
        """创建聊天式研究 run，并异步生成首条计划消息。

        Args:
            user_id: 当前用户 ID。
            request_data: 已通过 API schema 校验的自然语言需求和约束。
            background_tasks: FastAPI BackgroundTasks 实例。

        Returns:
            已持久化的研究 run。

        Raises:
            ValueError: 当前用户已有未完成 Deep Research run 时抛出。
        """
        created = create_run_record(
            user_id,
            request_data,
            title=self._build_title(str(request_data["requirement"])),
        )
        run_id = created["run_id"]
        raw_requirement = created["raw_requirement"]
        logger.info(
            "interactive research run created",
            extra={
                "run_id": str(run_id),
                "user_id": user_id,
                "requirement_length": len(str(raw_requirement or "")),
                "scope": request_data.get("scope"),
                "research_depth": request_data.get("research_depth"),
                "max_iterations": request_data.get("max_iterations"),
            },
        )

        background_tasks.add_task(self.execute_plan_agent_background, run_id, raw_requirement, None, True)
        created_run = self.get_run(run_id, user_id)
        if created_run is None:
            raise LookupError(_t("errors.run_not_found"))
        return created_run

    def list_runs(self, user_id: int) -> List[InteractiveResearchRun]:
        """查询当前用户的研究 run 列表。

        Args:
            user_id: 当前用户 ID。

        Returns:
            按创建时间倒序排列的 run 列表。
        """
        return list_run_records(user_id)

    def get_run(self, run_id: UUID, user_id: int) -> Optional[InteractiveResearchRun]:
        """查询当前用户拥有的单个研究 run。

        Args:
            run_id: 研究 run ID。
            user_id: 当前用户 ID。

        Returns:
            找到时返回 run，否则返回 None。
        """
        return get_run_record(run_id, user_id)

    def delete_run(self, run_id: UUID, user_id: int) -> bool:
        """删除当前用户拥有的聊天式研究 run。

        Args:
            run_id: 研究 run ID。
            user_id: 当前用户 ID。

        Returns:
            删除成功返回 True；run 不存在或不属于当前用户时返回 False。
        """
        deleted = delete_run_record(run_id, user_id)
        return deleted

    async def append_user_message(
        self,
        run_id: UUID,
        user_id: int,
        content: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        background_tasks: Any,
    ) -> InteractiveResearchMessage:
        """向聊天流追加用户输入，并按当前状态处理动态输入。

        Args:
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            content: 用户输入文本。
            payload: 可选小型结构化 payload。
            background_tasks: FastAPI BackgroundTasks 实例。

        Returns:
            已创建的用户消息。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: 终态 run 不允许继续追加时抛出。
        """
        result = append_user_message_record(run_id, user_id, content, payload)
        message = result["message"]
        run_status = result["run_status"]
        logger.info(
            "interactive research user message appended",
            extra={
                "run_id": str(run_id),
                "user_id": user_id,
                "message_id": str(message.message_id),
                "run_status": run_status,
                "content_length": len(str(content or "")),
            },
        )
        if run_status == "awaiting_user_input":
            logger.info(
                "interactive research queued answer resumes workflow",
                extra={"run_id": str(run_id), "user_id": user_id, "message_id": str(message.message_id)},
            )
            background_tasks.add_task(
                self.execute_workflow_background,
                run_id,
                self._plan_agent.latest_plan_output(run_id),
            )
            return message
        if run_status not in {"awaiting_plan_approval", "awaiting_user_input"}:
            return message

        if run_status == "awaiting_plan_approval":
            logger.info(
                "interactive research schedules plan revision",
                extra={"run_id": str(run_id), "user_id": user_id, "message_id": str(message.message_id)},
            )
            background_tasks.add_task(self.execute_plan_agent_background, run_id, content, content, False)
        return message

    async def process_action(
        self,
        run_id: UUID,
        user_id: int,
        action: str,
        *,
        background_tasks: Any,
        content: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> InteractiveResearchRun:
        """执行 run 级动作。

        Args:
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            action: approve 或 cancel。
            content: 动作说明文本。
            payload: 动作结构化 payload。
            background_tasks: FastAPI BackgroundTasks 实例。

        Returns:
            更新后的 run。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: action 或状态不允许时抛出。
        """
        if action == "approve":
            return await self.approve_plan(run_id, user_id, background_tasks=background_tasks)
        if action == "cancel":
            reason = content or str((payload or {}).get("reason") or "")
            return self.cancel_run(run_id, user_id, reason=reason)
        raise ValueError(_t("errors.unsupported_action", action=action))

    async def approve_plan(
        self, run_id: UUID, user_id: int, background_tasks: Any
    ) -> InteractiveResearchRun:
        """确认计划并启动单 Agent loop。

        Args:
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            background_tasks: FastAPI BackgroundTasks 实例。

        Returns:
            更新后的 run。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: run 不处于等待计划确认状态时抛出。
        """
        result = approve_plan_record(run_id, user_id)
        run = result["run"]
        logger.info(
            "interactive research plan approved",
            extra={"run_id": str(run.run_id), "user_id": user_id, "status": run.status},
        )

        background_tasks.add_task(
            self.execute_workflow_background,
            run.run_id,
            self._plan_agent.latest_plan_output(run.run_id),
        )

        return run

    def cancel_run(
        self,
        run_id: UUID,
        user_id: int,
        reason: Optional[str] = None,
    ) -> InteractiveResearchRun:
        """取消当前用户的研究 run。

        Args:
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            reason: 可选取消原因。

        Returns:
            已取消的 run。

        Raises:
            LookupError: run 不存在或不属于当前用户时抛出。
            ValueError: 终态 run 不能重复取消时抛出。
        """
        run = cancel_run_record(run_id, user_id, reason)
        return run

    def get_messages(
        self,
        run_id: UUID,
        user_id: int,
        *,
        visible_only: bool = True,
    ) -> List[InteractiveResearchMessage]:
        """查询 run 的聊天消息流。

        Args:
            run_id: 研究 run ID。
            user_id: 当前用户 ID。
            visible_only: 是否只返回用户可见消息。

        Returns:
            按 sequence_no 升序排列的消息列表。
        """
        return list_message_records(run_id, user_id, visible_only=visible_only)

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
        user_id = run_payload.get("user_id")
        if user_id is None:
            return
        await ws_manager.send_interactive_stock_picker_update(
            run_id=str(run_payload.get("run_id") or ""),
            stage=str(run_payload.get("current_stage") or ""),
            status=str(run_payload.get("status") or ""),
            message=str(message_text),
            user_id=int(user_id),
            payload={
                "domain": "interactive_research",
                "event": payload.get("event"),
                "run": run_payload,
                "message": message_payload,
                "display_message": display_message,
            },
        )

    def _build_title(self, requirement: str) -> str:
        """根据原始需求生成聊天标题。

        Args:
            requirement: 原始用户需求。

        Returns:
            最多 60 字的标题。
        """
        normalized = " ".join(requirement.split())
        return normalized[:60] or _t("messages.default_title")

    async def execute_workflow_background(self, run_id: UUID, approved_plan: str) -> None:
        """后台执行 workflow（用于 FastAPI BackgroundTasks）。

        Args:
            run_id: 研究 run ID。
            approved_plan: 用户确认的计划卡正文。
        """
        try:
            logger.info(
                "interactive research workflow background started",
                extra={"run_id": str(run_id), "approved_plan_length": len(str(approved_plan or ""))},
            )
            await self._research_agent.execute(run_id, approved_plan)
            logger.info("interactive research workflow background finished", extra={"run_id": str(run_id)})
        except Exception as exc:
            import traceback

            logger.error(
                "interactive research workflow failed",
                extra={"run_id": str(run_id), "exception": str(exc), "traceback": traceback.format_exc()},
            )
            fail_run_record(run_id, _t("errors.workflow_failed", error=exc), type(exc).__name__, str(exc))

    async def execute_plan_agent_background(
        self,
        run_id: UUID,
        user_input: str,
        history_input: Optional[str] = None,
        initial: bool = False,
    ) -> None:
        """后台执行计划 Agent，生成或修订计划卡。

        Args:
            run_id: 研究 run ID。
            user_input: 本轮发送给计划 Agent 的输入。
            history_input: 写入计划历史的用户原文。
            initial: 是否为首轮计划生成。
        """
        try:
            logger.info(
                "interactive research plan agent background started",
                extra={
                    "run_id": str(run_id),
                    "initial": initial,
                    "user_input_length": len(str(user_input or "")),
                    "history_input_length": len(str(history_input or "")),
                },
            )
            await self._plan_agent.execute(
                run_id,
                user_input,
                history_input=history_input,
                initial=initial,
            )
            logger.info(
                "interactive research plan agent background finished",
                extra={"run_id": str(run_id), "initial": initial},
            )
        except Exception as exc:
            import traceback

            logger.error(
                "interactive research plan agent failed",
                extra={"run_id": str(run_id), "exception": str(exc), "traceback": traceback.format_exc()},
            )
            fail_run_record(run_id, _t("errors.workflow_failed", error=exc), type(exc).__name__, str(exc))


interactive_research_service = InteractiveResearchService()
