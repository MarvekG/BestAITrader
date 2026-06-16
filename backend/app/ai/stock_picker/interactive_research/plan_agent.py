from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.ai.json_utils import stable_json_dumps
from app.ai.llm_providers.factory import build_chat_model
from app.ai.stock_picker.interactive_research.constants import (
    planning_initial_user_message,
    planning_stage_prompt,
)
from app.ai.stock_picker.interactive_research.persistence import load_plan_turn_record, persist_plan_card_record
from app.ai.stock_picker.interactive_research.planning import build_plan_preview_payload
from app.core.config import settings
from app.core.i18n import i18n_service
from app.crud.llm_usage_log import record_llm_usage


LLMFactory = Callable[[], Any]


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

    def __init__(self, llm_factory: Optional[LLMFactory] = None) -> None:
        """初始化计划阶段 Agent。

        Args:
            llm_factory: 可选 LLM 工厂；测试可注入 fake LLM。
        """
        self._llm_factory = llm_factory
        self._plan_messages: Dict[UUID, List[Any]] = {}

    async def draft_initial_plan(self, run_id: UUID, raw_requirement: str) -> Dict[str, Any]:
        """生成首轮待用户确认的计划卡。

        Args:
            run_id: 当前研究 run ID。
            raw_requirement: 原始用户需求。

        Returns:
            最新计划 payload。
        """
        return await self._iterate(
            run_id,
            [planning_initial_user_message(raw_requirement)],
            history_inputs=[raw_requirement],
            initial=True,
        )

    async def revise_plan(self, run_id: UUID, user_input: str) -> Dict[str, Any]:
        """根据用户补充输入迭代下一版计划卡。

        Args:
            run_id: 当前研究 run ID。
            user_input: 用户本轮补充要求。

        Returns:
            最新计划 payload。
        """
        return await self._iterate(run_id, [user_input.strip()])

    def forget(self, run_id: UUID) -> None:
        """清理指定 run 的计划阶段对话缓存。

        Args:
            run_id: 当前研究 run ID。
        """
        self._plan_messages.pop(run_id, None)

    async def _iterate(
        self,
        run_id: UUID,
        user_inputs: List[str],
        *,
        history_inputs: Optional[List[str]] = None,
        initial: bool = False,
    ) -> Dict[str, Any]:
        """执行计划阶段循环，每轮结束后停下来等用户输入或确认。

        Args:
            run_id: 当前研究 run ID。
            user_inputs: 待发送给计划 Agent 的计划阶段用户输入序列。
            history_inputs: 可选历史记录输入；未提供时与 user_inputs 相同。
            initial: 是否为首轮计划生成。

        Returns:
            最新计划 payload。

        Raises:
            LookupError: run 不存在时抛出。
            ValueError: 用户输入为空时抛出。
        """
        plan_payload: Dict[str, Any] = {}
        for input_index, user_input in enumerate(user_inputs):
            normalized_input = user_input.strip()
            if not normalized_input:
                raise ValueError(_t("errors.plan_update_empty"))
            history_input = (
                history_inputs[input_index]
                if history_inputs is not None and input_index < len(history_inputs)
                else normalized_input
            )
            is_initial_input = initial and input_index == 0

            turn_record = load_plan_turn_record(run_id)
            if turn_record is None:
                raise LookupError(_t("errors.run_not_found"))
            if turn_record["status"] != "awaiting_plan_approval":
                break
            plan_payload = turn_record["plan_payload"]
            llm_messages = self._plan_messages_for_turn(
                run_id,
                normalized_input,
                plan_payload,
                persisted_messages=turn_record["persisted_messages"],
                initial=is_initial_input,
            )

            plan_message, usage_record = await self._invoke_plan_markdown(run_id, llm_messages)

            latest_turn_record = load_plan_turn_record(run_id)
            if latest_turn_record is None:
                raise LookupError(_t("errors.run_not_found"))
            if latest_turn_record["status"] != "awaiting_plan_approval":
                break
            plan_payload = latest_turn_record["plan_payload"]
            if not is_initial_input:
                plan_payload = self._update_plan_payload_from_message(
                    plan_payload,
                    history_input,
                    plan_message,
                )
            persisted = persist_plan_card_record(
                run_id,
                plan_message=plan_message,
                plan_preview_payload=build_plan_preview_payload(plan_payload),
                plan_payload=plan_payload,
                usage_record=usage_record,
                reason="plan_drafted" if is_initial_input else "plan_updated",
                bump_version=not is_initial_input,
            )
            if not persisted:
                break
            self._remember_plan_turn(run_id, history_input, plan_message)
        return plan_payload

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
        updated_plan["objective_summary"] = self._plan_objective(updated_plan)
        return updated_plan

    def _plan_messages_for_turn(
        self,
        run_id: UUID,
        content: str,
        plan_payload: Dict[str, Any],
        *,
        persisted_messages: List[Dict[str, str]],
        initial: bool,
    ) -> List[Any]:
        """复用缓存的计划阶段消息，仅刷新系统提示并追加本轮用户输入。

        计划对话缓存只保存到上一条计划回复为止（不含本轮待回答输入），因此每轮只需在缓存
        基础上追加一条用户消息构造调用上下文，而不必每次从历史重新构建整个消息列表。

        Args:
            run_id: 当前研究 run ID。
            content: 用户本轮输入。
            plan_payload: 当前计划 payload。
            persisted_messages: 已持久化的计划阶段消息快照。
            initial: 是否为首轮计划生成。

        Returns:
            本轮调用 LLM 的消息列表。
        """
        system_message = SystemMessage(content=_build_planning_stage_prompt(plan_payload))
        if initial:
            conversation: List[Any] = [system_message]
            self._plan_messages[run_id] = conversation
        else:
            conversation = self._plan_messages.get(run_id)
            if conversation is None:
                conversation = self._restore_plan_messages(persisted_messages, system_message)
                self._plan_messages[run_id] = conversation
            else:
                conversation[0] = system_message
        return [*conversation, HumanMessage(content=content)]

    def _restore_plan_messages(
        self,
        persisted_messages: List[Dict[str, str]],
        system_message: Any,
    ) -> List[Any]:
        """进程缓存缺失时，从已持久化消息重建计划阶段对话缓存。

        Args:
            persisted_messages: 已持久化的计划阶段消息快照。
            system_message: 本轮系统提示消息。

        Returns:
            截止到上一条计划回复的消息缓存（丢弃尾部尚未回复的用户输入）。
        """
        conversation: List[Any] = [system_message]
        for item in persisted_messages:
            if item.get("role") == "user":
                conversation.append(HumanMessage(content=item.get("content") or ""))
            elif item.get("role") == "assistant" and item.get("message_type") == "plan_card":
                conversation.append(AIMessage(content=item.get("content") or ""))
        while len(conversation) > 1 and isinstance(conversation[-1], HumanMessage):
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

    def _plan_objective(self, plan_payload: Dict[str, Any]) -> str:
        """读取计划目标摘要。

        Args:
            plan_payload: 当前计划 payload。

        Returns:
            计划目标摘要。
        """
        return str(plan_payload.get("objective_summary") or "").strip()

def _build_planning_stage_prompt(plan_payload: Dict[str, Any]) -> str:
    """构造计划阶段流程控制提示词。

    Args:
        plan_payload: 当前计划 payload。

    Returns:
        当前系统语言下的计划阶段提示词。
    """
    return planning_stage_prompt(stable_json_dumps(plan_payload), "")
