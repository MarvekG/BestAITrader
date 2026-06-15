from __future__ import annotations

from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.ai.agentic.tool_output_summarizer import should_summarize_tool_output, summarize_tool_output
from app.ai.json_utils import stable_json_dumps
from app.ai.llm_providers.factory import build_chat_model, get_llm_provider
from app.ai.stock_picker.interactive_research import constants as prompt_constants
from app.ai.stock_picker.interactive_research.constants import phase_instructions, research_agent_system_prompt
from app.ai.stock_picker.interactive_research.flow_control import (
    FLOW_CONTROL_TOOL_NAME,
    FlowControlDecision,
    flow_control_decision_from_tool_args,
)
from app.ai.stock_picker.interactive_research.models import InteractiveResearchMessage, InteractiveResearchRun
from app.ai.stock_picker.interactive_research.persistence import accumulate_llm_usage, append_message, write_checkpoint
from app.ai.stock_picker.interactive_research.serializers import serialize_message, serialize_run_summary
from app.ai.stock_picker.interactive_research.tool_registry import InteractiveResearchToolRegistry, ToolLoaderFactory
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.i18n import i18n_service
from app.crud.llm_usage_log import record_llm_usage


LLMFactory = Callable[[], Any]
WorkflowNotificationCallback = Callable[[Dict[str, Any]], Awaitable[None]]
DEFAULT_INTERACTIVE_RESEARCH_ITERATIONS = 60
MIN_INTERACTIVE_RESEARCH_ITERATIONS = 10
MAX_FLOW_CONTROL_RETRIES = 2
FLOW_CONTROL_RETRY_MARKER = "FLOW_CONTROL_RETRY"


def flow_control_protocol_instruction() -> str:
    """返回当前语言下的流程控制工具提示词。

    Returns:
        流程控制工具提示词。
    """
    return prompt_constants.flow_control_protocol_instruction(FLOW_CONTROL_TOOL_NAME)


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究 workflow 翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


class InteractiveResearchWorkflow:
    """聊天式 Deep Research 单 Agent tool-calling loop。"""

    def __init__(
        self,
        tool_loader_factory: Optional[ToolLoaderFactory] = None,
        llm_factory: Optional[LLMFactory] = None,
        notification_callback: Optional[WorkflowNotificationCallback] = None,
    ) -> None:
        """初始化研究工作流。

        Args:
            tool_loader_factory: 可选工具注册表工厂；测试可注入 fake 工具。
            llm_factory: 可选 LLM 工厂；测试可注入 fake LLM。
            notification_callback: 后台消息写入后的实时通知回调。
        """
        self._tool_loader_factory = tool_loader_factory
        self._llm_factory = llm_factory
        self._notification_callback = notification_callback
        self._llm_provider = get_llm_provider()

    async def execute(self, run_id: UUID, plan_payload: Dict[str, Any]) -> None:
        """异步运行 LLM tool-calling 循环。

        Args:
            run_id: 当前研究 run ID。
            plan_payload: 已确认计划 payload。
        """
        tool_trace: List[Dict[str, Any]] = []
        run_snapshot = await self._start_research_run(run_id, plan_payload)
        if run_snapshot is None:
            return

        messages = self._build_agent_messages(
            run_id,
            run_snapshot["raw_requirement"],
            plan_payload,
            run_snapshot["queued_before"],
        )
        tools = await self._load_tools(run_id, run_snapshot["user_id"])
        tool_map = {
            str(getattr(tool, "name", "")): tool
            for tool in tools
            if getattr(tool, "name", "") and str(getattr(tool, "name", "")) != FLOW_CONTROL_TOOL_NAME
        }
        llm = self._build_llm()
        llm_with_tools = llm.bind_tools(tools)
        final_content = ""
        iteration_budget = self._iteration_budget(plan_payload)
        stopped_by_iteration_limit = False

        for iteration_index in range(1, iteration_budget + 1):
            response = await llm_with_tools.ainvoke(messages)
            self._record_and_accumulate_llm_usage(
                run_id,
                response,
                stage="agent_loop",
                call_kind="agent",
                iteration_index=iteration_index,
            )
            response, invalid_tool_calls = self._llm_provider.sanitize_tool_call_response_for_replay(response)
            messages.append(response)
            tool_calls = list(getattr(response, "tool_calls", []) or [])
            flow_control_calls, evidence_tool_calls = _partition_tool_calls(tool_calls)
            if not evidence_tool_calls and not flow_control_calls and not invalid_tool_calls:
                messages.append(HumanMessage(content=_missing_flow_control_tool_retry_message()))
                continue

            for tool_call in evidence_tool_calls:
                trace_item = await self._execute_tool_call(run_id, tool_map, messages, tool_call, iteration_index, llm)
                tool_trace.append(trace_item)

            queued_after_tool = self._process_queued_user_inputs(run_id)
            if queued_after_tool:
                self._append_queued_inputs_to_messages(messages, queued_after_tool)
                await self._append_queued_input_status(run_id, queued_after_tool)

            if invalid_tool_calls:
                messages.append(
                    HumanMessage(content=self._llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls))
                )

            if flow_control_calls:
                decision = self._parse_flow_control_tool_or_retry(messages, flow_control_calls)
                if decision is None:
                    continue
                if decision.status == "ask":
                    await self._pause_for_user_question(run_id, plan_payload, decision.message)
                    return
                if decision.status == "done":
                    final_content = decision.message
                    break
                await self._append_assistant_text(run_id, decision.message)
                messages.append(HumanMessage(content=_research_continuation_instruction()))

        if not final_content:
            stopped_by_iteration_limit = True
            messages.append(
                HumanMessage(
                    content=_iteration_budget_instruction(iteration_budget)
                )
            )
            for retry_index in range(MAX_FLOW_CONTROL_RETRIES + 1):
                final_response = await llm_with_tools.ainvoke(messages)
                self._record_and_accumulate_llm_usage(
                    run_id,
                    final_response,
                    stage="agent_loop",
                    call_kind="final_no_tools",
                    iteration_index=iteration_budget + 1 + retry_index,
                )
                final_response, invalid_tool_calls = self._llm_provider.sanitize_tool_call_response_for_replay(final_response)
                messages.append(final_response)
                tool_calls = list(getattr(final_response, "tool_calls", []) or [])
                flow_control_calls, evidence_tool_calls = _partition_tool_calls(tool_calls)
                if evidence_tool_calls:
                    messages.append(HumanMessage(content=_final_must_use_control_tool_retry_message()))
                    continue
                if invalid_tool_calls:
                    messages.append(
                        HumanMessage(content=self._llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls))
                    )
                    continue
                decision = self._parse_flow_control_tool_or_retry(messages, flow_control_calls, final_only=True)
                final_content = decision.message if decision is not None else ""
                if final_content:
                    break
            if not final_content:
                final_content = _iteration_budget_fallback_answer(iteration_budget)

        await self._synthesize_final_message(
            run_id,
            plan_payload,
            tool_trace,
            final_content,
            stopped_by_iteration_limit=stopped_by_iteration_limit,
            iteration_budget=iteration_budget,
        )

    async def _start_research_run(
        self,
        run_id: UUID,
        plan_payload: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """把 run 切到研究阶段并记录输入上下文。

        Args:
            run_id: 当前研究 run ID。
            plan_payload: 已确认计划 payload。

        Returns:
            run 快照；run 不存在时返回 None。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return None
            queued_messages = self._process_queued_user_inputs_in_db(db, run)
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
            snapshot = {
                "user_id": run.user_id,
                "raw_requirement": run.raw_requirement,
                "queued_before": queued_messages,
            }
            await self._notify_change(db, run, message, "research_started")
            return snapshot

    async def _execute_tool_call(
        self,
        run_id: UUID,
        tool_map: Dict[str, Any],
        messages: List[Any],
        tool_call: Dict[str, Any],
        iteration_index: int,
        llm: Any,
    ) -> Dict[str, Any]:
        """执行 LLM 返回的单个工具调用，并同步写消息流。

        Args:
            run_id: 当前研究 run ID。
            tool_map: 工具名到 LangChain 工具对象的映射。
            messages: LLM 消息上下文。
            tool_call: LangChain tool call 载荷。
            iteration_index: 当前迭代序号。
            llm: 当前研究主 LLM，用于复用工具结果压缩链路。

        Returns:
            工具调用轨迹。
        """
        tool_name = str(tool_call.get("name") or "")
        tool_args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        tool_call_id = str(tool_call.get("id") or f"interactive-{iteration_index}-{len(messages)}")
        start_message_id = await self._append_tool_start(run_id, tool_name, tool_args, tool_call_id)
        trace_item: Dict[str, Any] = {"name": tool_name, "args": tool_args, "success": False}
        tool = tool_map.get(tool_name)
        if tool is None:
            result_text = _t("errors.tool_not_allowed", tool_name=tool_name)
            trace_item["error"] = result_text
        else:
            try:
                raw_result = await tool.ainvoke(tool_args)
                result_text = _json_tool_result(raw_result)
                if should_summarize_tool_output(tool_name, result_text):
                    result_text = await summarize_tool_output(
                        llm,
                        role_name="interactive_stock_research",
                        tool_name=tool_name,
                        content=result_text,
                        tool_args=tool_args,
                        workflow="interactive_stock_research",
                        stage="tool_summary",
                        iteration_index=iteration_index,
                    )
                trace_item["success"] = True
            except Exception as exc:
                result_text = f"Error: {exc}"
                trace_item["error"] = str(exc)

        messages.append(ToolMessage(tool_call_id=tool_call_id, content=result_text))
        await self._append_tool_result_and_progress(
            run_id,
            tool_name,
            tool_args,
            tool_call_id,
            start_message_id,
            trace_item["success"],
            result_text,
        )
        return trace_item

    async def _append_tool_start(
        self, run_id: UUID, tool_name: str, tool_args: Dict[str, Any], tool_call_id: str
    ) -> str:
        """记录工具开始调用消息。

        Args:
            run_id: 当前研究 run ID。
            tool_name: 工具名称。
            tool_args: 工具参数。
            tool_call_id: LLM 工具调用 ID。

        Returns:
            新建 tool_start 消息 ID。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return ""
            start_message = append_message(
                db,
                run,
                role="tool",
                message_type="tool_start",
                content=_t("messages.tool_start", tool_name=tool_name),
                payload={"tool_name": tool_name, "arguments": tool_args, "tool_call_id": tool_call_id},
            )
            await self._notify_change(db, run, start_message, "tool_start")
            return str(start_message.message_id)

    async def _append_tool_result_and_progress(
        self,
        run_id: UUID,
        tool_name: str,
        tool_args: Dict[str, Any],
        tool_call_id: str,
        start_message_id: str,
        success: bool,
        result_text: str,
    ) -> None:
        """记录工具结果、进度消息和 checkpoint。

        Args:
            run_id: 当前研究 run ID。
            tool_name: 工具名称。
            tool_args: 工具参数。
            tool_call_id: LLM 工具调用 ID。
            start_message_id: tool_start 消息 ID。
            success: 工具是否调用成功。
            result_text: 工具结果文本。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return
            result_message = append_message(
                db,
                run,
                role="tool",
                message_type="tool_result",
                content=_compact_tool_result(result_text),
                payload={
                    "tool_name": tool_name,
                    "arguments": tool_args,
                    "tool_call_id": tool_call_id,
                    "start_message_id": start_message_id,
                    "success": success,
                    "result_preview": result_text,
                },
            )
            await self._notify_change(db, run, result_message, "tool_result")
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
                    "plan_payload": self._plan_payload_from_checkpoint(run),
                    "answer_message_id": current_checkpoint.get("answer_message_id"),
                    "last_tool_name": tool_name,
                    "last_tool_success": success,
                },
            )
            await self._notify_change(db, run, progress_message, "progress_update")

    async def _synthesize_final_message(
        self,
        run_id: UUID,
        plan_payload: Dict[str, Any],
        tool_trace: List[Dict[str, Any]],
        final_content: str,
        *,
        stopped_by_iteration_limit: bool = False,
        iteration_budget: int = 0,
    ) -> None:
        """写入最终消息，不使用本地固定评分或基础股票池。

        Args:
            run_id: 当前研究 run ID。
            plan_payload: 已确认计划 payload。
            tool_trace: 工具调用轨迹。
            final_content: LLM 最终回答。
            stopped_by_iteration_limit: 是否因迭代预算耗尽提前终止。
            iteration_budget: 本轮研究允许的最大迭代次数。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return
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
            await self._notify_change(db, run, final_message, "final_result")
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
            await self._notify_change(db, run, status_message, "completed")

    async def _pause_for_user_question(
        self,
        run_id: UUID,
        plan_payload: Dict[str, Any],
        question_content: str,
    ) -> None:
        """在 agent 输出 ask 时停止循环并写入问题。

        Args:
            run_id: 当前研究 run ID。
            plan_payload: 已确认计划 payload。
            question_content: LLM 生成的用户问题。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return
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
            await self._notify_change(db, run, question, "assistant_question")

    async def _append_assistant_text(self, run_id: UUID, content: str) -> None:
        """追加研究过程中的普通 assistant 文本。

        Args:
            run_id: 当前研究 run ID。
            content: LLM 输出的过程说明。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return
            message = append_message(
                db,
                run,
                role="assistant",
                message_type="assistant_text",
                content=content,
                payload={},
            )
            write_checkpoint(db, run, reason="assistant_text")
            await self._notify_change(db, run, message, "assistant_text")

    def _process_queued_user_inputs(self, run_id: UUID) -> List[Dict[str, str]]:
        """处理运行中排队的用户输入消息。

        Args:
            run_id: 当前研究 run ID。

        Returns:
            已处理的排队消息列表。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return []
            queued_messages = self._process_queued_user_inputs_in_db(db, run)
            db.commit()
            return queued_messages

    def _process_queued_user_inputs_in_db(self, db: Any, run: InteractiveResearchRun) -> List[Dict[str, str]]:
        """在当前数据库作用域内处理排队用户输入。

        Args:
            db: 当前数据库会话。
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

    async def _append_queued_input_status(
        self, run_id: UUID, queued_messages: List[Dict[str, str]]
    ) -> None:
        """记录排队输入已并入下一轮 agent 消息。

        Args:
            run_id: 当前研究 run ID。
            queued_messages: 已处理的排队用户消息。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return
            message = append_message(
                db,
                run,
                role="system",
                message_type="system_status",
                content=_t("messages.queued_input_appended"),
                payload={"queued_message_ids": [message["message_id"] for message in queued_messages]},
            )
            await self._notify_change(db, run, message, "queued_input_appended")

    def _build_agent_messages(
        self,
        run_id: UUID,
        raw_requirement: str,
        plan_payload: Dict[str, Any],
        queued_messages: List[Dict[str, str]],
    ) -> List[Any]:
        """构造 LLM tool-calling 消息上下文。

        Args:
            run_id: 当前研究 run ID。
            raw_requirement: 原始用户需求。
            plan_payload: 已确认计划 payload。
            queued_messages: 本轮开始前并入上下文的排队用户输入。

        Returns:
            LangChain 消息列表。
        """
        history = self._build_recent_chat_messages(run_id)
        prompt = (
            f"{research_agent_system_prompt()}\n"
            f"{_tool_policy_instruction()}\n"
            f"{flow_control_protocol_instruction()}\n"
            f"{_approved_plan_label()}:\n{stable_json_dumps(plan_payload)}"
        )
        messages: List[Any] = [SystemMessage(content=prompt)]
        for item in history:
            role = item.get("role")
            content = str(item.get("content") or "")
            if role == "user":
                messages.append(HumanMessage(content=content))
        if queued_messages:
            self._append_queued_inputs_to_messages(messages, queued_messages)
        if len(messages) == 1:
            messages.append(HumanMessage(content=raw_requirement))
        return messages

    def _append_queued_inputs_to_messages(
        self,
        messages: List[Any],
        queued_messages: List[Dict[str, str]],
    ) -> None:
        """把排队用户输入追加到 LLM 上下文。

        Args:
            messages: LLM 消息上下文。
            queued_messages: 已处理的排队用户消息。
        """
        for message in queued_messages:
            messages.append(HumanMessage(content=f"{_additional_user_input_label()}: {message['content']}"))

    def _build_recent_chat_messages(self, run_id: UUID) -> List[Dict[str, Any]]:
        """构造给 agent 使用的最近消息上下文。

        Args:
            run_id: 当前研究 run ID。

        Returns:
            最近消息的轻量结构。
        """
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is None:
                return []
            return self._build_recent_chat_messages_in_db(db, run)

    def _build_recent_chat_messages_in_db(self, db: Any, run: InteractiveResearchRun) -> List[Dict[str, Any]]:
        """在当前数据库作用域内构造最近消息上下文。

        Args:
            db: 当前数据库会话。
            run: 当前研究 run。

        Returns:
            最近消息的轻量结构。
        """
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

    async def _load_tools(self, run_id: UUID, user_id: int) -> List[Any]:
        """加载绑定给 LLM 的非交易工具。

        Args:
            run_id: 当前研究 run ID。
            user_id: 当前用户 ID。

        Returns:
            LangChain 工具列表。
        """
        state = {"user_id": user_id, "run_id": str(run_id), "agent_state": "interactive_stock_research"}
        registry = (
            self._tool_loader_factory(state)
            if self._tool_loader_factory
            else InteractiveResearchToolRegistry(state=state)
        )
        return await registry.aload_tools()

    def _build_llm(self) -> Any:
        """构造 interactive research 使用的 LLM。

        Returns:
            LangChain chat model。
        """
        if self._llm_factory:
            return self._llm_factory()
        return build_chat_model(model=settings.LLM_MODEL, temperature=0.2)

    def _record_and_accumulate_llm_usage(
        self,
        run_id: UUID,
        response: Any,
        *,
        stage: str,
        call_kind: str,
        iteration_index: int,
    ) -> None:
        """记录单次 LLM usage，并同步累加到 run checkpoint。

        Args:
            run_id: 当前研究 run ID。
            response: LLM 返回对象。
            stage: workflow 阶段。
            call_kind: LLM 调用类型。
            iteration_index: 调用迭代序号。
        """
        usage_record = record_llm_usage(
            response,
            settings.LLM_MODEL,
            "interactive_stock_research",
            session_id=run_id,
            workflow="interactive_stock_research",
            stage=stage,
            call_kind=call_kind,
            iteration_index=iteration_index,
        )
        with SessionLocal() as db:
            run = db.query(InteractiveResearchRun).filter(InteractiveResearchRun.run_id == run_id).first()
            if run is not None:
                accumulate_llm_usage(db, run, usage_record)
                db.commit()

    def _parse_flow_control_tool_or_retry(
        self,
        messages: List[Any],
        flow_control_calls: List[Dict[str, Any]],
        *,
        final_only: bool = False,
    ) -> Optional[FlowControlDecision]:
        """解析流程控制工具调用，失败时把纠错指令加入下一轮上下文。

        Args:
            messages: 当前 LLM 消息上下文。
            flow_control_calls: 本轮流程控制工具调用列表。
            final_only: 是否只允许最终完成动作。

        Returns:
            解析成功的流程控制决策；需要重试时返回 None。
        """
        try:
            if not flow_control_calls:
                raise ValueError(f"Expected at least one {FLOW_CONTROL_TOOL_NAME} call")
            decision = flow_control_decision_from_tool_args(flow_control_calls[-1].get("args"))
            if final_only and decision.status != "done":
                raise ValueError(f"Final response must use action=done, got {decision.status}")
            return decision
        except ValueError as exc:
            if _flow_control_retry_count(messages) >= MAX_FLOW_CONTROL_RETRIES:
                raise ValueError(f"LLM flow-control tool call invalid after retry: {exc}") from exc
            messages.append(HumanMessage(content=_build_flow_control_retry_message(exc, final_only=final_only)))
            return None

    def _iteration_budget(self, plan_payload: Dict[str, Any]) -> int:
        """读取并限制工具循环预算。

        Args:
            plan_payload: 已确认计划 payload。

        Returns:
            工具循环上限。
        """
        budget = plan_payload.get("research_budget") if isinstance(plan_payload.get("research_budget"), dict) else {}
        max_tool_calls = int(budget.get("max_tool_calls") or DEFAULT_INTERACTIVE_RESEARCH_ITERATIONS)
        return max(MIN_INTERACTIVE_RESEARCH_ITERATIONS, max_tool_calls)

    def _plan_payload_from_checkpoint(self, run: InteractiveResearchRun) -> Dict[str, Any]:
        """从 run checkpoint 中读取计划。

        Args:
            run: 当前研究 run。

        Returns:
            计划 payload。
        """
        checkpoint = run.checkpoint_payload or {}
        plan_payload = checkpoint.get("plan_payload")
        return plan_payload if isinstance(plan_payload, dict) else {}

    async def _notify_change(
        self,
        db: Any,
        run: InteractiveResearchRun,
        message: Optional[InteractiveResearchMessage],
        event: str,
    ) -> None:
        """提交当前后台变更并推送实时通知。

        Args:
            db: 数据库会话。
            run: 当前研究 run。
            message: 本次新增消息。
            event: 通知事件名。
        """
        db.flush()
        payload = {
            "event": event,
            "run": serialize_run_summary(run),
            "message": serialize_message(message) if message is not None else None,
            "message_text": message.content if message is not None else event,
        }
        db.commit()
        if self._notification_callback is not None:
            await self._notification_callback(payload)


def _json_tool_result(value: Any) -> str:
    """把工具结果转换为 ToolMessage 文本。

    Args:
        value: 工具返回值。

    Returns:
        字符串结果。
    """
    if isinstance(value, (dict, list)):
        return stable_json_dumps(value)
    return str(value)


def _build_flow_control_retry_message(exc: ValueError, *, final_only: bool = False) -> str:
    """生成流程控制工具纠错提示。

    Args:
        exc: 解析失败的错误。
        final_only: 是否要求最终回答必须使用 action=done。

    Returns:
        用于下一轮 LLM 的纠错消息。
    """
    return prompt_constants.flow_control_retry_message(
        FLOW_CONTROL_TOOL_NAME,
        FLOW_CONTROL_RETRY_MARKER,
        str(exc),
        final_only=final_only,
    )


def _flow_control_retry_count(messages: List[Any]) -> int:
    """统计当前上下文中流程控制工具纠错次数。

    Args:
        messages: 当前 LLM 消息上下文。

    Returns:
        已追加的纠错提示数量。
    """
    return sum(
        1
        for message in messages
        if FLOW_CONTROL_RETRY_MARKER in str(getattr(message, "content", ""))
    )


def _partition_tool_calls(tool_calls: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """拆分流程控制工具调用和证据工具调用。

    Args:
        tool_calls: LLM 返回的工具调用列表。

    Returns:
        流程控制工具调用列表、证据工具调用列表。
    """
    flow_control_calls = [call for call in tool_calls if str(call.get("name") or "") == FLOW_CONTROL_TOOL_NAME]
    evidence_tool_calls = [call for call in tool_calls if str(call.get("name") or "") != FLOW_CONTROL_TOOL_NAME]
    return flow_control_calls, evidence_tool_calls


def _missing_flow_control_tool_retry_message() -> str:
    """生成缺少流程控制工具调用的纠错提示。

    Returns:
        当前提示词语言下的纠错提示。
    """
    return prompt_constants.missing_flow_control_tool_retry_message(FLOW_CONTROL_TOOL_NAME, FLOW_CONTROL_RETRY_MARKER)


def _final_must_use_control_tool_retry_message() -> str:
    """生成最终阶段错误调用证据工具的纠错提示。

    Returns:
        当前提示词语言下的纠错提示。
    """
    return prompt_constants.final_must_use_control_tool_retry_message(FLOW_CONTROL_TOOL_NAME, FLOW_CONTROL_RETRY_MARKER)


def _compact_tool_result(result_text: str) -> str:
    """生成消息流里展示的工具结果摘要。

    Args:
        result_text: 完整工具结果文本。

    Returns:
        面向聊天流的短摘要。
    """
    normalized = " ".join(str(result_text or "").split())
    return normalized or _t("messages.tool_empty_result")


def _research_continuation_instruction() -> str:
    """返回研究继续指令。

    Returns:
        当前提示词语言下的继续研究指令。
    """
    return prompt_constants.research_continuation_instruction(FLOW_CONTROL_TOOL_NAME)


def _iteration_budget_instruction(iteration_budget: int) -> str:
    """返回工具循环预算耗尽后的最终回答指令。

    Args:
        iteration_budget: 已耗尽的最大迭代次数。

    Returns:
        当前提示词语言下的最终回答指令。
    """
    return prompt_constants.iteration_budget_instruction(FLOW_CONTROL_TOOL_NAME, iteration_budget)


def _iteration_budget_fallback_answer(iteration_budget: int) -> str:
    """生成预算耗尽且模型未给出最终答案时的兜底答案。

    Args:
        iteration_budget: 已耗尽的最大迭代次数。

    Returns:
        最终 Markdown 答案。
    """
    return prompt_constants.iteration_budget_fallback_answer(iteration_budget)


def _tool_policy_instruction() -> str:
    """返回工具边界提示词。

    Returns:
        当前提示词语言下的工具边界提示词。
    """
    return prompt_constants.tool_policy_instruction()


def _approved_plan_label() -> str:
    """返回已确认计划标签。

    Returns:
        当前提示词语言下的已确认计划标签。
    """
    return prompt_constants.approved_plan_label()


def _additional_user_input_label() -> str:
    """返回补充用户输入标签。

    Returns:
        当前提示词语言下的补充用户输入标签。
    """
    return prompt_constants.additional_user_input_label()
