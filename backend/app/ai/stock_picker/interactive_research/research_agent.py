from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.ai.agentic.tool_output_summarizer import should_summarize_tool_output, summarize_tool_output
from app.ai.json_utils import stable_json_dumps
from app.ai.llm_providers.factory import build_chat_model, get_llm_provider
from app.ai.stock_picker.interactive_research import constants as prompt_constants
from app.ai.stock_picker.interactive_research.constants import (
    PLAN_CONVERSATION_CONTEXT_HEADER_EN,
    PLAN_CONVERSATION_CONTEXT_HEADER_ZH,
    PLAN_CONVERSATION_PLAN_LINE_EN,
    PLAN_CONVERSATION_PLAN_LINE_ZH,
    PLAN_CONVERSATION_USER_LINE_EN,
    PLAN_CONVERSATION_USER_LINE_ZH,
    research_agent_system_prompt,
)
from app.ai.stock_picker.interactive_research.flow_control import (
    FLOW_CONTROL_TOOL_NAME,
    FlowControlDecision,
    flow_control_decision_from_tool_args,
)
from app.ai.stock_picker.interactive_research.persistence import (
    accumulate_llm_usage_record,
    append_assistant_text_record,
    append_queued_input_status_record,
    append_tool_result_and_progress_record,
    append_tool_start_record,
    pause_for_user_question_record,
    process_queued_user_inputs_record,
    start_research_run_record,
    synthesize_final_message_record,
)
from app.ai.stock_picker.interactive_research.tool_registry import InteractiveResearchToolRegistry, ToolLoaderFactory
from app.core.config import settings
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.crud.llm_usage_log import record_llm_usage


LLMFactory = Callable[[], Any]
ResearchAgentNotificationCallback = Callable[[Dict[str, Any]], Awaitable[None]]
DEFAULT_INTERACTIVE_RESEARCH_ITERATIONS = 60
MIN_INTERACTIVE_RESEARCH_ITERATIONS = 10
MAX_FLOW_CONTROL_RETRIES = 2
FLOW_CONTROL_RETRY_MARKER = "FLOW_CONTROL_RETRY"
logger = get_logger(__name__)


def flow_control_protocol_instruction() -> str:
    """返回当前语言下的流程控制工具提示词。

    Returns:
        流程控制工具提示词。
    """
    return prompt_constants.flow_control_protocol_instruction(FLOW_CONTROL_TOOL_NAME)


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究 Agent 翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


class InteractiveResearchAgent:
    """聊天式 Deep Research 单 Agent tool-calling loop。"""

    def __init__(
        self,
        tool_loader_factory: Optional[ToolLoaderFactory] = None,
        llm_factory: Optional[LLMFactory] = None,
        notification_callback: Optional[ResearchAgentNotificationCallback] = None,
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

    async def execute(self, run_id: UUID, approved_plan: str) -> None:
        """异步运行 LLM tool-calling 循环。

        Args:
            run_id: 当前研究 run ID。
            approved_plan: 用户确认的研究计划正文。
        """
        tool_trace: List[Dict[str, Any]] = []
        run_snapshot = await self._start_research_run(run_id)
        if run_snapshot is None:
            logger.warning("interactive research agent skipped missing run", extra={"run_id": str(run_id)})
            return
        logger.info(
            "interactive research agent started",
            extra={
                "run_id": str(run_id),
                "user_id": run_snapshot["user_id"],
                "queued_before_count": len(run_snapshot["queued_before"]),
                "plan_conversation_count": len(run_snapshot.get("plan_conversation", [])),
                "approved_plan_length": len(str(approved_plan or "")),
                "max_iterations": run_snapshot["max_iterations"],
            },
        )

        messages = self._build_agent_messages(
            run_snapshot["raw_requirement"],
            approved_plan,
            run_snapshot["queued_before"],
            run_snapshot.get("plan_conversation", []),
        )
        tools = await self._load_tools(run_id, run_snapshot["user_id"])
        tool_map = {
            str(getattr(tool, "name", "")): tool
            for tool in tools
            if getattr(tool, "name", "") and str(getattr(tool, "name", "")) != FLOW_CONTROL_TOOL_NAME
        }
        llm = self._build_llm()
        llm_with_tools = llm.bind_tools(tools)
        logger.info(
            "interactive research agent tools loaded",
            extra={
                "run_id": str(run_id),
                "tool_names": [str(getattr(tool, "name", "")) for tool in tools],
                "evidence_tool_count": len(tool_map),
            },
        )
        final_content = ""
        iteration_budget = self._iteration_budget(run_snapshot["max_iterations"])
        stopped_by_iteration_limit = False

        for iteration_index in range(1, iteration_budget + 1):
            logger.info(
                "interactive research agent llm iteration started",
                extra={
                    "run_id": str(run_id),
                    "iteration_index": iteration_index,
                    "iteration_budget": iteration_budget,
                    "message_count": len(messages),
                },
            )
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
            logger.info(
                "interactive research agent llm iteration completed",
                extra={
                    "run_id": str(run_id),
                    "iteration_index": iteration_index,
                    "evidence_tool_call_count": len(evidence_tool_calls),
                    "flow_control_call_count": len(flow_control_calls),
                    "invalid_tool_call_count": len(invalid_tool_calls),
                    "content_length": len(str(getattr(response, "content", "") or "")),
                },
            )
            if not evidence_tool_calls and not flow_control_calls and not invalid_tool_calls:
                logger.warning(
                    "interactive research agent missing flow control tool",
                    extra={"run_id": str(run_id), "iteration_index": iteration_index},
                )
                messages.append(HumanMessage(content=_missing_flow_control_tool_retry_message()))
                continue

            for tool_call in evidence_tool_calls:
                trace_item = await self._execute_tool_call(run_id, tool_map, messages, tool_call, iteration_index, llm)
                tool_trace.append(trace_item)

            queued_after_tool = self._process_queued_user_inputs(run_id)
            if queued_after_tool:
                logger.info(
                    "interactive research agent queued inputs merged",
                    extra={
                        "run_id": str(run_id),
                        "iteration_index": iteration_index,
                        "queued_input_count": len(queued_after_tool),
                    },
                )
                self._append_queued_inputs_to_messages(messages, queued_after_tool)
                await self._append_queued_input_status(run_id, queued_after_tool)

            if invalid_tool_calls:
                logger.warning(
                    "interactive research agent invalid tool calls",
                    extra={
                        "run_id": str(run_id),
                        "iteration_index": iteration_index,
                        "invalid_tool_call_count": len(invalid_tool_calls),
                    },
                )
                messages.append(
                    HumanMessage(content=self._llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls))
                )

            if flow_control_calls:
                decision = self._parse_flow_control_tool_or_retry(messages, flow_control_calls)
                if decision is None:
                    continue
                logger.info(
                    "interactive research agent flow control decision",
                    extra={
                        "run_id": str(run_id),
                        "iteration_index": iteration_index,
                        "decision": decision.status,
                        "message_length": len(decision.message),
                    },
                )
                if decision.status == "ask":
                    await self._pause_for_user_question(run_id, decision.message)
                    return
                if decision.status == "done":
                    final_content = decision.message
                    break
                await self._append_assistant_text(run_id, decision.message)
                messages.append(HumanMessage(content=_research_continuation_instruction()))

        if not final_content:
            stopped_by_iteration_limit = True
            logger.warning(
                "interactive research agent iteration budget exhausted",
                extra={"run_id": str(run_id), "iteration_budget": iteration_budget},
            )
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
                final_response, invalid_tool_calls = self._llm_provider.sanitize_tool_call_response_for_replay(
                    final_response
                )
                messages.append(final_response)
                tool_calls = list(getattr(final_response, "tool_calls", []) or [])
                flow_control_calls, evidence_tool_calls = _partition_tool_calls(tool_calls)
                logger.info(
                    "interactive research agent final retry completed",
                    extra={
                        "run_id": str(run_id),
                        "retry_index": retry_index,
                        "evidence_tool_call_count": len(evidence_tool_calls),
                        "flow_control_call_count": len(flow_control_calls),
                        "invalid_tool_call_count": len(invalid_tool_calls),
                    },
                )
                if evidence_tool_calls:
                    logger.warning(
                        "interactive research agent final retry used evidence tool",
                        extra={"run_id": str(run_id), "retry_index": retry_index},
                    )
                    messages.append(HumanMessage(content=_final_must_use_control_tool_retry_message()))
                    continue
                if invalid_tool_calls:
                    logger.warning(
                        "interactive research agent final retry invalid tool calls",
                        extra={
                            "run_id": str(run_id),
                            "retry_index": retry_index,
                            "invalid_tool_call_count": len(invalid_tool_calls),
                        },
                    )
                    messages.append(
                        HumanMessage(
                            content=self._llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls)
                        )
                    )
                    continue
                decision = self._parse_flow_control_tool_or_retry(messages, flow_control_calls, final_only=True)
                final_content = decision.message if decision is not None else ""
                if final_content:
                    break
            if not final_content:
                final_content = _iteration_budget_fallback_answer(iteration_budget)
                logger.warning(
                    "interactive research agent using iteration budget fallback answer",
                    extra={"run_id": str(run_id), "iteration_budget": iteration_budget},
                )

        await self._synthesize_final_message(
            run_id,
            tool_trace,
            final_content,
            stopped_by_iteration_limit=stopped_by_iteration_limit,
            iteration_budget=iteration_budget,
        )
        logger.info(
            "interactive research agent completed",
            extra={
                "run_id": str(run_id),
                "tool_trace_count": len(tool_trace),
                "final_content_length": len(final_content),
                "stopped_by_iteration_limit": stopped_by_iteration_limit,
            },
        )

    async def _start_research_run(self, run_id: UUID) -> Optional[Dict[str, Any]]:
        """把 run 切到研究阶段并记录输入上下文。

        Args:
            run_id: 当前研究 run ID。

        Returns:
            run 快照；run 不存在时返回 None。
        """
        result = start_research_run_record(run_id)
        if result is None:
            return None
        await self._notify_change(result["notification"])
        logger.info(
            "interactive research run entered research phase",
            extra={
                "run_id": str(run_id),
                "queued_before_count": len(result["snapshot"]["queued_before"]),
                "max_iterations": result["snapshot"]["max_iterations"],
            },
        )
        return result["snapshot"]

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
        logger.info(
            "interactive research agent tool call started",
            extra={
                "run_id": str(run_id),
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "iteration_index": iteration_index,
                "tool_arg_keys": sorted(tool_args.keys()),
            },
        )
        start_message_id = await self._append_tool_start(run_id, tool_name, tool_args, tool_call_id)
        trace_item: Dict[str, Any] = {"name": tool_name, "args": tool_args, "success": False}
        tool = tool_map.get(tool_name)
        if tool is None:
            result_text = _t("errors.tool_not_allowed", tool_name=tool_name)
            trace_item["error"] = result_text
            logger.warning(
                "interactive research agent tool not allowed",
                extra={
                    "run_id": str(run_id),
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "iteration_index": iteration_index,
                },
            )
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
                trace_item["success"] = _is_successful_tool_result(raw_result, result_text)
            except Exception as exc:
                result_text = f"Error: {exc}"
                trace_item["error"] = str(exc)
                logger.warning(
                    "interactive research agent tool call failed",
                    extra={
                        "run_id": str(run_id),
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "iteration_index": iteration_index,
                        "exception": str(exc),
                    },
                )

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
        logger.info(
            "interactive research agent tool call completed",
            extra={
                "run_id": str(run_id),
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "iteration_index": iteration_index,
                "success": trace_item["success"],
                "result_length": len(result_text),
            },
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
        result = append_tool_start_record(run_id, tool_name, tool_args, tool_call_id)
        await self._notify_change(result.get("notification"))
        return str(result.get("message_id") or "")

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
        payloads = append_tool_result_and_progress_record(
            run_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            start_message_id=start_message_id,
            success=success,
            result_text=result_text,
            result_content=_compact_tool_result(result_text),
        )
        for payload in payloads:
            await self._notify_change(payload)

    async def _synthesize_final_message(
        self,
        run_id: UUID,
        tool_trace: List[Dict[str, Any]],
        final_content: str,
        *,
        stopped_by_iteration_limit: bool = False,
        iteration_budget: int = 0,
    ) -> None:
        """写入最终消息，不使用本地固定评分或基础股票池。

        Args:
            run_id: 当前研究 run ID。
            tool_trace: 工具调用轨迹。
            final_content: LLM 最终回答。
            stopped_by_iteration_limit: 是否因迭代预算耗尽提前终止。
            iteration_budget: 本轮研究允许的最大迭代次数。
        """
        payloads = synthesize_final_message_record(
            run_id,
            tool_trace=tool_trace,
            final_content=final_content,
            stopped_by_iteration_limit=stopped_by_iteration_limit,
            iteration_budget=iteration_budget,
        )
        for payload in payloads:
            await self._notify_change(payload)

    async def _pause_for_user_question(
        self,
        run_id: UUID,
        question_content: str,
    ) -> None:
        """在 agent 输出 ask 时停止循环并写入问题。

        Args:
            run_id: 当前研究 run ID。
            question_content: LLM 生成的用户问题。
        """
        await self._notify_change(pause_for_user_question_record(run_id, question_content))
        logger.info(
            "interactive research agent paused for user question",
            extra={"run_id": str(run_id), "question_length": len(question_content)},
        )

    async def _append_assistant_text(self, run_id: UUID, content: str) -> None:
        """追加研究过程中的普通 assistant 文本。

        Args:
            run_id: 当前研究 run ID。
            content: LLM 输出的过程说明。
        """
        await self._notify_change(append_assistant_text_record(run_id, content))

    def _process_queued_user_inputs(self, run_id: UUID) -> List[Dict[str, str]]:
        """处理运行中排队的用户输入消息。

        Args:
            run_id: 当前研究 run ID。

        Returns:
            已处理的排队消息列表。
        """
        return process_queued_user_inputs_record(run_id)

    async def _append_queued_input_status(
        self, run_id: UUID, queued_messages: List[Dict[str, str]]
    ) -> None:
        """记录排队输入已并入下一轮 agent 消息。

        Args:
            run_id: 当前研究 run ID。
            queued_messages: 已处理的排队用户消息。
        """
        await self._notify_change(append_queued_input_status_record(run_id, queued_messages))

    def _build_agent_messages(
        self,
        raw_requirement: str,
        approved_plan: str,
        queued_messages: List[Dict[str, str]],
        plan_conversation: List[Dict[str, Any]],
    ) -> List[Any]:
        """构造 LLM tool-calling 消息上下文。

        Args:
            raw_requirement: 原始用户需求。
            approved_plan: 用户确认的研究计划正文。
            queued_messages: 本轮开始前并入上下文的排队用户输入。
            plan_conversation: 计划阶段用户输入和计划卡，按顺序传入研究上下文。

        Returns:
            LangChain 消息列表。
        """
        prompt = (
            f"{research_agent_system_prompt()}\n"
            f"{_tool_policy_instruction()}\n"
            f"{flow_control_protocol_instruction()}\n"
        )
        messages: List[Any] = [SystemMessage(content=prompt)]
        if approved_plan:
            messages.append(SystemMessage(content=approved_plan))
        if plan_conversation:
            messages.append(SystemMessage(content=_format_plan_conversation(plan_conversation)))
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
        accumulate_llm_usage_record(run_id, usage_record)

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
            for tool_call in flow_control_calls:
                messages.append(
                    ToolMessage(
                        tool_call_id=str(tool_call.get("id") or ""),
                        content=stable_json_dumps({"action": decision.status}),
                    )
                )
            return decision
        except ValueError as exc:
            if _flow_control_retry_count(messages) >= MAX_FLOW_CONTROL_RETRIES:
                raise ValueError(f"LLM flow-control tool call invalid after retry: {exc}") from exc
            for tool_call in flow_control_calls:
                messages.append(
                    ToolMessage(
                        tool_call_id=str(tool_call.get("id") or ""),
                        content=stable_json_dumps({"error": str(exc)}),
                    )
                )
            messages.append(HumanMessage(content=_build_flow_control_retry_message(exc, final_only=final_only)))
            return None

    def _iteration_budget(self, max_iterations: int) -> int:
        """读取并限制工具循环预算。

        Args:
            max_iterations: 前端创建 run 时传入的最大迭代次数。

        Returns:
            工具循环上限。
        """
        return max(MIN_INTERACTIVE_RESEARCH_ITERATIONS, int(max_iterations))

    async def _notify_change(
        self,
        payload: Optional[Dict[str, Any]],
    ) -> None:
        """推送已持久化变更的实时通知。

        Args:
            payload: 持久化层生成的通知 payload。
        """
        if self._notification_callback is not None and payload is not None:
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


def _is_successful_tool_result(raw_result: Any, result_text: str) -> bool:
    """根据工具返回内容判断业务层是否成功。

    Args:
        raw_result: 工具原始返回值。
        result_text: 已序列化后的工具结果文本。

    Returns:
        存在 success=false 或 error 字段时返回 False，否则返回 True。
    """
    payload = raw_result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    elif not isinstance(payload, (dict, list)):
        try:
            payload = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            payload = None

    if isinstance(payload, dict):
        if payload.get("success") is False:
            return False
        if payload.get("error"):
            return False
    return True


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


def _format_plan_conversation(plan_conversation: List[Dict[str, Any]]) -> str:
    """格式化计划阶段对话，供研究阶段识别用户修订语义。

    Args:
        plan_conversation: 计划阶段用户输入和计划卡列表。

    Returns:
        带轮次和类型标识的上下文文本。
    """
    if str(settings.SYSTEM_LANGUAGE).lower().startswith("zh"):
        lines = [PLAN_CONVERSATION_CONTEXT_HEADER_ZH]
        user_template = PLAN_CONVERSATION_USER_LINE_ZH
        plan_template = PLAN_CONVERSATION_PLAN_LINE_ZH
    else:
        lines = [PLAN_CONVERSATION_CONTEXT_HEADER_EN]
        user_template = PLAN_CONVERSATION_USER_LINE_EN
        plan_template = PLAN_CONVERSATION_PLAN_LINE_EN
    for item in plan_conversation:
        template = plan_template if item.get("kind") == "plan_card" else user_template
        lines.append(template.format(round=item.get("round"), content=item.get("content") or ""))
    return "\n".join(lines)


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


def _additional_user_input_label() -> str:
    """返回补充用户输入标签。

    Returns:
        当前提示词语言下的补充用户输入标签。
    """
    return prompt_constants.additional_user_input_label()
