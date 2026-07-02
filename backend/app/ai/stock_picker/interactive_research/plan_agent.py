from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.ai.agentic.tool_output_summarizer import should_summarize_tool_output, summarize_tool_output
from app.ai.agentic.tools import make_json_serializable
from app.ai.json_utils import stable_json_dumps
from app.ai.llm_providers.factory import build_chat_model, get_llm_provider
from app.ai.stock_picker.interactive_research.constants import (
    PLAN_ITERATION_BUDGET_INSTRUCTION_EN,
    PLAN_ITERATION_BUDGET_INSTRUCTION_ZH,
    planning_initial_user_message,
    planning_stage_prompt,
)
from app.ai.stock_picker.interactive_research.flow_control import FLOW_CONTROL_TOOL_NAME
from app.ai.stock_picker.interactive_research.persistence import (
    append_tool_result_and_progress_record,
    append_tool_start_record,
    load_plan_turn_record,
    persist_plan_card_record,
)
from app.ai.stock_picker.interactive_research.tool_registry import InteractiveResearchToolRegistry, ToolLoaderFactory
from app.core.config import settings
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.crud.llm_usage_log import record_llm_usage


LLMFactory = Callable[[], Any]
PlanAgentNotificationCallback = Callable[[Dict[str, Any]], Awaitable[None]]
PLAN_AGENT_TOOL_NAMES = {
    "get_current_time",
    "browse_web_page_html",
    "parse_pdf_to_markdown",
    "search_news",
}
logger = get_logger(__name__)


def _t(key: str, **kwargs: Any) -> str:
    """读取交互式研究计划阶段翻译文案。

    Args:
        key: backend 命名空间下的翻译 key。
        **kwargs: 翻译模板变量。

    Returns:
        当前系统语言下的文案。
    """
    return i18n_service.t(f"ai_stock_picker.interactive.backend.{key}", **kwargs)


class PlanAgent:
    """聊天式 Deep Research 计划阶段 Agent。

    与研究阶段的 ``InteractiveResearchAgent`` 结构对称：自管数据库会话、自管消息写入和
    checkpoint。唯一区别是计划循环每跑完一轮就停下来等用户输入或确认，而不是像研究阶段那样
    自动多轮跑到结束。计划阶段只产出 Markdown 计划卡，不绑定工具，也不推荐股票。
    """

    def __init__(
        self,
        tool_loader_factory: Optional[ToolLoaderFactory] = None,
        llm_factory: Optional[LLMFactory] = None,
        notification_callback: Optional[PlanAgentNotificationCallback] = None,
    ) -> None:
        """初始化计划阶段 Agent。

        Args:
            tool_loader_factory: 可选工具注册表工厂；测试可注入 fake 工具。
            llm_factory: 可选 LLM 工厂；测试可注入 fake LLM。
            notification_callback: 计划卡写入后的实时通知回调。
        """
        self._tool_loader_factory = tool_loader_factory
        self._llm_factory = llm_factory
        self._notification_callback = notification_callback
        self._plan_messages: Dict[UUID, List[Any]] = {}
        self._latest_plan_outputs: Dict[UUID, str] = {}
        self._llm_provider = get_llm_provider()

    async def execute(
        self,
        run_id: UUID,
        user_input: str,
        *,
        history_input: Optional[str] = None,
        initial: bool = False,
    ) -> None:
        """执行一轮计划 Agent，完成后停下来等待用户确认或补充。

        计划阶段每次后台任务只执行一轮：生成一张 plan_card 后退出，等待用户补充要求或确认计划。

        Args:
            run_id: 当前研究 run ID。
            user_input: 本轮发送给计划 Agent 的用户输入。
            history_input: 写入计划历史的用户原文；为空时使用 user_input。
            initial: 是否为首轮计划生成。

        Raises:
            LookupError: run 不存在时抛出。
            ValueError: 用户输入为空时抛出。
        """
        normalized_input = user_input.strip()
        if not normalized_input:
            raise ValueError(_t("errors.plan_update_empty"))

        effective_history_input = (history_input or normalized_input).strip()
        llm_input = planning_initial_user_message(normalized_input) if initial else normalized_input
        turn_record = await load_plan_turn_record(run_id)
        if turn_record is None:
            raise LookupError(_t("errors.run_not_found"))
        if turn_record["status"] != "awaiting_plan_approval":
            logger.info(
                "interactive research plan agent skipped by run status",
                extra={"run_id": str(run_id), "status": turn_record["status"], "initial": initial},
            )
            return
        logger.info(
            "interactive research plan agent started",
            extra={
                "run_id": str(run_id),
                "initial": initial,
                "persisted_message_count": len(turn_record["persisted_messages"]),
                "input_length": len(normalized_input),
            },
        )

        messages = self._build_plan_messages(
            run_id,
            llm_input,
            persisted_messages=turn_record["persisted_messages"],
        )
        tools = await self._load_tools(run_id)
        logger.info(
            "interactive research plan agent tools loaded",
            extra={"run_id": str(run_id), "tool_names": [str(getattr(tool, "name", "")) for tool in tools]},
        )
        plan_message = await self._invoke_plan_markdown(run_id, messages, tools)

        latest_turn_record = await load_plan_turn_record(run_id)
        if latest_turn_record is None:
            raise LookupError(_t("errors.run_not_found"))
        if latest_turn_record["status"] != "awaiting_plan_approval":
            logger.info(
                "interactive research plan card skipped by latest status",
                extra={"run_id": str(run_id), "status": latest_turn_record["status"], "initial": initial},
            )
            return

        result = await persist_plan_card_record(
            run_id,
            plan_message=plan_message,
            reason="plan_drafted" if initial else "plan_updated",
            bump_version=not initial,
        )
        persisted = result["persisted"]
        if not persisted:
            logger.info(
                "interactive research plan card not persisted",
                extra={"run_id": str(run_id), "initial": initial, "plan_length": len(plan_message)},
            )
            return
        await self._notify_change(result["notification"])

        self._remember_plan_turn(run_id, effective_history_input, plan_message)
        self._latest_plan_outputs[run_id] = plan_message
        logger.info(
            "interactive research plan card persisted",
            extra={"run_id": str(run_id), "initial": initial, "plan_length": len(plan_message)},
        )

    async def latest_plan_output(self, run_id: UUID) -> str:
        """读取当前进程内指定 run 的最新计划输出。

        Args:
            run_id: 当前研究 run ID。

        Returns:
            最新计划 Markdown；未生成时返回空字符串。
        """
        latest_plan = self._latest_plan_outputs.get(run_id, "")
        if latest_plan:
            return latest_plan

        turn_record = await load_plan_turn_record(run_id)
        if turn_record is None:
            return ""
        for item in reversed(turn_record["persisted_messages"]):
            if item.get("role") == "assistant" and item.get("message_type") == "plan_card":
                latest_plan = str(item.get("content") or "")
                self._latest_plan_outputs[run_id] = latest_plan
                return latest_plan
        return ""

    def _build_plan_messages(
        self,
        run_id: UUID,
        content: str,
        *,
        persisted_messages: List[Dict[str, str]],
    ) -> List[Any]:
        """构造本轮计划 Agent 消息。

        进程内缓存只保存已完成的 user/assistant 历史；本轮 system 和 human 临时拼装，不写入缓存。
        只有缓存缺失时才从数据库恢复历史，通常发生在服务重启后。

        Args:
            run_id: 当前研究 run ID。
            content: 用户本轮输入。
            persisted_messages: 已持久化的计划阶段消息快照。

        Returns:
            本轮调用 LLM 的消息列表。
        """
        system_message = SystemMessage(content=planning_stage_prompt())
        history = self._plan_messages.get(run_id)
        if history is None:
            history = self._restore_plan_messages(persisted_messages)
            self._plan_messages[run_id] = history
        return [system_message, *history, HumanMessage(content=content)]

    def _restore_plan_messages(
        self,
        persisted_messages: List[Dict[str, str]],
    ) -> List[Any]:
        """进程缓存缺失时，从已持久化消息重建计划阶段对话缓存。

        Args:
            persisted_messages: 已持久化的计划阶段消息快照。

        Returns:
            截止到上一条计划回复的消息缓存（丢弃尾部尚未回复的用户输入）。
        """
        conversation: List[Any] = []
        for item in persisted_messages:
            if item.get("role") == "user":
                conversation.append(HumanMessage(content=item.get("content") or ""))
            elif item.get("role") == "assistant" and item.get("message_type") == "plan_card":
                conversation.append(AIMessage(content=item.get("content") or ""))
        while conversation and isinstance(conversation[-1], HumanMessage):
            conversation.pop()
        return conversation

    def _remember_plan_turn(
        self,
        run_id: UUID,
        user_content: str,
        assistant_content: str,
    ) -> None:
        """把本轮成功的用户输入和计划回复追加进消息缓存。

        Args:
            run_id: 当前研究 run ID。
            user_content: 用户本轮输入。
            assistant_content: 计划 Agent 本轮 Markdown 输出。
        """
        conversation = self._plan_messages.get(run_id)
        if conversation is not None:
            conversation.append(HumanMessage(content=user_content))
            conversation.append(AIMessage(content=assistant_content))

    def _build_llm(self) -> Any:
        """构造计划阶段使用的 LLM。

        Returns:
            LangChain chat model。
        """
        if self._llm_factory:
            return self._llm_factory()
        return build_chat_model(model=settings.LLM_MODEL, temperature=0.2)

    async def _invoke_plan_markdown(
        self, run_id: UUID, messages: List[Any], tools: List[Any]
    ) -> str:
        """调用计划阶段 LLM 生成 Markdown 研究计划。

        Args:
            run_id: 当前研究 run ID。
            messages: 计划阶段 LLM 消息上下文。
            tools: 可供计划阶段使用的联网工具列表。

        Returns:
            计划 Agent 输出的 Markdown 正文。
        """
        llm = self._build_llm()
        llm_with_tools = llm.bind_tools(tools) if tools else llm
        tool_map = {str(getattr(tool, "name", "")): tool for tool in tools if getattr(tool, "name", "")}

        iteration_budget = max(1, int(settings.INTERACTIVE_RESEARCH_PLAN_MAX_ITERATIONS))
        for iteration_index in range(1, iteration_budget + 1):
            logger.info(
                "interactive research plan agent llm iteration started",
                extra={
                    "run_id": str(run_id),
                    "iteration_index": iteration_index,
                    "iteration_budget": iteration_budget,
                    "message_count": len(messages),
                },
            )
            response = await llm_with_tools.ainvoke(messages)
            await self._record_llm_usage(run_id, response, iteration_index, call_kind="plan_markdown")
            response, invalid_tool_calls = self._llm_provider.sanitize_tool_call_response_for_replay(response)
            messages.append(response)

            tool_calls = list(getattr(response, "tool_calls", []) or [])
            logger.info(
                "interactive research plan agent llm iteration completed",
                extra={
                    "run_id": str(run_id),
                    "iteration_index": iteration_index,
                    "tool_call_count": len(tool_calls),
                    "invalid_tool_call_count": len(invalid_tool_calls),
                    "content_length": len(str(getattr(response, "content", "") or "")),
                },
            )
            if not tool_calls and not invalid_tool_calls:
                return str(getattr(response, "content", "") or "").strip()

            for tool_call in tool_calls:
                await self._execute_tool_call(run_id, tool_map, messages, tool_call, iteration_index, llm)
            if invalid_tool_calls:
                logger.warning(
                    "interactive research plan agent invalid tool calls",
                    extra={
                        "run_id": str(run_id),
                        "iteration_index": iteration_index,
                        "invalid_tool_call_count": len(invalid_tool_calls),
                    },
                )
                messages.append(
                    HumanMessage(content=self._llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls))
                )

        logger.warning(
            "interactive research plan agent iteration budget exhausted",
            extra={"run_id": str(run_id), "iteration_budget": iteration_budget},
        )
        messages.append(HumanMessage(content=_plan_iteration_budget_instruction(iteration_budget)))
        final_response = await llm.ainvoke(messages)
        await self._record_llm_usage(
            run_id,
            final_response,
            iteration_budget + 1,
            call_kind="plan_final_no_tools",
        )
        final_response, _ = self._llm_provider.sanitize_tool_call_response_for_replay(final_response)
        logger.info(
            "interactive research plan agent final no-tools response completed",
            extra={"run_id": str(run_id), "content_length": len(str(getattr(final_response, "content", "") or ""))},
        )
        return str(getattr(final_response, "content", "") or "").strip()

    async def _load_tools(self, run_id: UUID) -> List[Any]:
        """加载计划阶段允许使用的联网工具。

        Args:
            run_id: 当前研究 run ID。

        Returns:
            过滤后的 LangChain 工具列表。
        """
        state = {"run_id": str(run_id), "agent_state": "interactive_stock_research_plan"}
        registry = (
            self._tool_loader_factory(state)
            if self._tool_loader_factory
            else InteractiveResearchToolRegistry(state=state)
        )
        tools = await registry.aload_tools()
        return [
            tool
            for tool in tools
            if str(getattr(tool, "name", "") or "") in PLAN_AGENT_TOOL_NAMES
            and str(getattr(tool, "name", "") or "") != FLOW_CONTROL_TOOL_NAME
        ]

    async def _execute_tool_call(
        self,
        run_id: UUID,
        tool_map: Dict[str, Any],
        messages: List[Any],
        tool_call: Dict[str, Any],
        iteration_index: int,
        llm: Any,
    ) -> None:
        """执行计划阶段联网工具调用并把结果放回 LLM 上下文。

        Args:
            run_id: 当前研究 run ID。
            tool_map: 工具名到 LangChain 工具对象的映射。
            messages: LLM 消息上下文。
            tool_call: LangChain tool call 载荷。
            iteration_index: 当前迭代序号。
            llm: 当前计划主 LLM，用于复用工具结果压缩链路。
        """
        tool_name = str(tool_call.get("name") or "")
        tool_args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}
        tool_call_id = str(tool_call.get("id") or f"plan-{iteration_index}-{len(messages)}")
        logger.info(
            "interactive research plan agent tool call started",
            extra={
                "run_id": str(run_id),
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "iteration_index": iteration_index,
                "tool_arg_keys": sorted(tool_args.keys()),
            },
        )
        start_message_id = await self._append_tool_start(run_id, tool_name, tool_args, tool_call_id)
        success = False
        tool = tool_map.get(tool_name)
        if tool is None:
            result_text = _t("errors.tool_not_allowed", tool_name=tool_name)
        else:
            try:
                raw_result = await tool.ainvoke(tool_args)
                result_text = stable_json_dumps(make_json_serializable(raw_result))
                if should_summarize_tool_output(tool_name, result_text):
                    result_text = await summarize_tool_output(
                        llm,
                        role_name="interactive_stock_research_plan",
                        tool_name=tool_name,
                        content=result_text,
                        tool_args=tool_args,
                        workflow="interactive_stock_research",
                        stage="planning_tool_summary",
                        iteration_index=iteration_index,
                    )
                success = True
            except Exception as exc:
                result_text = f"Error: {exc}"
                logger.warning(
                    "interactive research plan agent tool call failed",
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
            success,
            result_text,
        )
        logger.info(
            "interactive research plan agent tool call completed",
            extra={
                "run_id": str(run_id),
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "iteration_index": iteration_index,
                "success": success,
                "result_length": len(result_text),
            },
        )

    async def _append_tool_start(
        self, run_id: UUID, tool_name: str, tool_args: Dict[str, Any], tool_call_id: str
    ) -> str:
        """记录计划阶段工具开始调用消息并推送。

        Args:
            run_id: 当前研究 run ID。
            tool_name: 工具名称。
            tool_args: 工具参数。
            tool_call_id: LLM 工具调用 ID。

        Returns:
            新建 tool_start 消息 ID。
        """
        result = await append_tool_start_record(run_id, tool_name, tool_args, tool_call_id)
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
        """记录计划阶段工具结果和进度消息并推送。

        Args:
            run_id: 当前研究 run ID。
            tool_name: 工具名称。
            tool_args: 工具参数。
            tool_call_id: LLM 工具调用 ID。
            start_message_id: tool_start 消息 ID。
            success: 工具是否调用成功。
            result_text: 工具返回文本。
        """
        payloads = await append_tool_result_and_progress_record(
            run_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_call_id=tool_call_id,
            start_message_id=start_message_id,
            success=success,
            result_text=result_text,
            result_content=_compact_plan_tool_result(result_text),
        )
        for payload in payloads:
            await self._notify_change(payload)

    async def _record_llm_usage(
        self,
        run_id: UUID,
        response: Any,
        iteration_index: int,
        *,
        call_kind: str,
    ) -> None:
        """记录计划阶段单次 LLM usage。

        Args:
            run_id: 当前研究 run ID。
            response: LLM 返回对象。
            iteration_index: 调用迭代序号。
            call_kind: LLM 调用类型。

        """
        await record_llm_usage(
            response,
            settings.LLM_MODEL,
            "interactive_stock_research",
            session_id=run_id,
            workflow="interactive_stock_research",
            stage="planning",
            call_kind=call_kind,
            iteration_index=iteration_index,
        )

    async def _notify_change(self, payload: Optional[Dict[str, Any]]) -> None:
        """推送已持久化计划卡的实时通知。

        Args:
            payload: 持久化层生成的通知 payload。
        """
        if self._notification_callback is not None and payload is not None:
            await self._notification_callback(payload)


def _compact_plan_tool_result(result_text: str) -> str:
    """生成计划阶段消息流里展示的工具结果摘要。

    Args:
        result_text: 完整工具结果文本。

    Returns:
        面向聊天流的短摘要。
    """
    normalized = " ".join(str(result_text or "").split())
    return normalized or _t("messages.tool_empty_result")


def _plan_iteration_budget_instruction(iteration_budget: int) -> str:
    """生成计划阶段工具预算耗尽后的收束提示。

    Args:
        iteration_budget: 已允许的最大工具循环轮数。

    Returns:
        要求 LLM 不再调用工具、直接输出计划卡的提示。
    """
    template = (
        PLAN_ITERATION_BUDGET_INSTRUCTION_EN
        if str(settings.SYSTEM_LANGUAGE).lower().startswith("en")
        else PLAN_ITERATION_BUDGET_INSTRUCTION_ZH
    )
    return template.format(iteration_budget=iteration_budget)
