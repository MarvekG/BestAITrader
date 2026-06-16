from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.ai.llm_providers.factory import build_chat_model
from app.ai.stock_picker.interactive_research.constants import (
    planning_initial_user_message,
    planning_stage_prompt,
)
from app.ai.stock_picker.interactive_research.persistence import load_plan_turn_record, persist_plan_card_record
from app.core.config import settings
from app.core.i18n import i18n_service
from app.crud.llm_usage_log import record_llm_usage


LLMFactory = Callable[[], Any]
PlanAgentNotificationCallback = Callable[[Dict[str, Any]], Awaitable[None]]


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
        llm_factory: Optional[LLMFactory] = None,
        notification_callback: Optional[PlanAgentNotificationCallback] = None,
    ) -> None:
        """初始化计划阶段 Agent。

        Args:
            llm_factory: 可选 LLM 工厂；测试可注入 fake LLM。
            notification_callback: 计划卡写入后的实时通知回调。
        """
        self._llm_factory = llm_factory
        self._notification_callback = notification_callback
        self._plan_messages: Dict[UUID, List[Any]] = {}
        self._latest_plan_outputs: Dict[UUID, str] = {}

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
        turn_record = load_plan_turn_record(run_id)
        if turn_record is None:
            raise LookupError(_t("errors.run_not_found"))
        if turn_record["status"] != "awaiting_plan_approval":
            return

        messages = self._build_plan_messages(
            run_id,
            llm_input,
            persisted_messages=turn_record["persisted_messages"],
        )
        plan_message, usage_record = await self._invoke_plan_markdown(run_id, messages)

        latest_turn_record = load_plan_turn_record(run_id)
        if latest_turn_record is None:
            raise LookupError(_t("errors.run_not_found"))
        if latest_turn_record["status"] != "awaiting_plan_approval":
            return

        result = persist_plan_card_record(
            run_id,
            plan_message=plan_message,
            usage_record=usage_record,
            reason="plan_drafted" if initial else "plan_updated",
            bump_version=not initial,
        )
        persisted = result["persisted"]
        if not persisted:
            return
        await self._notify_change(result["notification"])

        self._remember_plan_turn(run_id, effective_history_input, plan_message)
        self._latest_plan_outputs[run_id] = plan_message

    def latest_plan_output(self, run_id: UUID) -> str:
        """读取当前进程内指定 run 的最新计划输出。

        Args:
            run_id: 当前研究 run ID。

        Returns:
            最新计划 Markdown；未生成时返回空字符串。
        """
        latest_plan = self._latest_plan_outputs.get(run_id, "")
        if latest_plan:
            return latest_plan

        turn_record = load_plan_turn_record(run_id)
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
        self, run_id: UUID, messages: List[Any]
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """调用计划阶段 LLM 生成 Markdown 研究计划。

        Args:
            run_id: 当前研究 run ID。
            messages: 计划阶段 LLM 消息上下文。

        Returns:
            计划 Agent 输出的 Markdown 正文和 usage 记录。
        """
        llm = self._build_llm()
        response = await llm.ainvoke(messages)
        usage_record = record_llm_usage(
            response,
            settings.LLM_MODEL,
            "interactive_stock_research",
            session_id=run_id,
            workflow="interactive_stock_research",
            stage="planning",
            call_kind="plan_markdown",
            iteration_index=1,
        )
        return str(getattr(response, "content", "") or "").strip(), usage_record

    async def _notify_change(self, payload: Optional[Dict[str, Any]]) -> None:
        """推送已持久化计划卡的实时通知。

        Args:
            payload: 持久化层生成的通知 payload。
        """
        if self._notification_callback is not None and payload is not None:
            await self._notification_callback(payload)
