from typing import Dict, Any, Type, Optional
from abc import ABC, abstractmethod
from dataclasses import dataclass
import re
from pydantic import BaseModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from app.ai.json_utils import stable_json_dumps
from app.ai.agentic.tool_output_summarizer import (
    should_summarize_tool_output,
    summarize_tool_output,
)
from app.ai.llm_routing import get_research_usage_lane
from app.ai.llm_providers import get_llm_provider
from app.core.config import settings
from app.ai.agentic.tools import get_all_tools
from app.ai.agentic.memory_tools import build_memory_tools
from app.ai.agentic.skills_loader.runtime import (
    build_skills_catalog_prompt,
    get_skills_loader_tools,
)
from app.ai.llm_engine.prompts import templates
from app.crud.llm_usage_log import record_llm_usage
from app.core.logger import get_logger
import tiktoken

logger = get_logger(__name__)
MIN_REPORT_SECTIONS = 3
MIN_REPORT_PARAGRAPHS = 4
MAX_LLM_ITERATIONS = 60
STRUCTURED_OUTPUT_RETRY_LIMIT = 3

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+\S")
LIST_ITEM_PATTERN = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)")


@dataclass(frozen=True)
class MarkdownReportShape:
    title_count: int
    section_count: int
    paragraph_count: int

    def is_complete(
        self,
        *,
        min_sections: int = MIN_REPORT_SECTIONS,
        min_paragraphs: int = MIN_REPORT_PARAGRAPHS,
    ) -> bool:
        return (
            self.title_count >= 1
            and self.section_count >= min_sections
            and self.paragraph_count >= min_paragraphs
        )


class BaseAgent(ABC):
    """
    Abstract base class for AI Analyst Agents.
    Integrates with LangChain for LLM interaction and structured output parsing.
    """

    def __init__(
        self,
        role_name: str,
        model_name: Optional[str] = None,
        temperature: float = 0.5,
        state: Optional[Dict[str, Any]] = None,
    ):
        self.role_name = role_name
        self.model_name = model_name or settings.LLM_MODEL
        self.temperature = temperature
        self.state = dict(state or {})
        self.state.setdefault("agent_role", role_name)
        self.session_id = (
            str(self.state.get("session_id"))
            if self.state.get("session_id") is not None
            else None
        )
        self.user_id = self.state.get("user_id")
        self.stock_code = self.state.get("stock_code")
        self.stage = self.state.get("stage")
        self.round_number = self.state.get("round_number")
        self.trading_strategy = self.state.get("trading_strategy")
        self.trading_frequency = self.state.get("trading_frequency")

        # Initialize LLM
        self.llm_provider = get_llm_provider()
        self.llm = self.llm_provider.build_chat_model(
            model=self.model_name,
            temperature=self.temperature,
        )
        self.last_prompt = ""

        # 集成所有工具 (Integrate all tools)
        self.tools = self.get_tools()
        self.llm_with_tools = self.llm.bind_tools(self.tools)

    def get_tools(self) -> list:
        """获取 Agent 工具列表，默认为全局工具。子类可覆盖或扩展。"""
        tools = list(get_all_tools())
        tools.extend(build_memory_tools(state=self.state))
        tools.extend(get_skills_loader_tools())
        return tools

    async def get_final_output_feedback(self, final_content: str) -> Optional[str]:
        """在接受最终输出前获取角色级补充指令。

        Args:
            final_content: LLM 即将作为最终报告返回的 Markdown 文本。

        Returns:
            若当前输出可接受则返回 None；否则返回需要追加给 LLM 的反馈文本。
        """
        return None

    @abstractmethod
    async def get_system_prompt(
        self,
        trading_frequency: str,
        trading_strategy: str,
    ) -> str:
        """Returns the system prompt for the specific agent role."""
        pass

    @abstractmethod
    def get_output_model(self) -> Type[BaseModel]:
        """Returns the Pydantic model for the expected output."""
        pass

    def _extract_json_from_content(self, text: str) -> str:
        """从 markdown 代码围栏或混合文本中提取纯 JSON。

        Args:
            text: 可能包含 markdown 格式的文本

        Returns:
            提取的 JSON 字符串
        """
        # 尝试提取 ```json ... ```
        json_block = re.search(r'```json\s*\n(.*?)\n```', text, re.DOTALL)
        if json_block:
            return json_block.group(1).strip()

        # 尝试提取 ``` ... ```
        code_block = re.search(r'```\s*\n(.*?)\n```', text, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()

        # 尝试提取第一个完整的 { ... } 对象
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            return json_match.group(0)

        return text

    def _get_encoding(self):
        try:
            return tiktoken.encoding_for_model(self.model_name)
        except KeyError:
            return tiktoken.get_encoding("cl100k_base")

    def _count_tokens(self, text: str) -> int:
        """Counts tokens in a string."""
        if not text:
            return 0
        return len(self._get_encoding().encode(text))

    def _measure_markdown_report_shape(self, text: str) -> MarkdownReportShape:
        title_count = 0
        section_count = 0
        paragraph_count = 0
        in_paragraph = False

        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()

            if not stripped:
                in_paragraph = False
                continue

            heading_match = HEADING_PATTERN.match(stripped)
            if heading_match:
                in_paragraph = False
                level = len(heading_match.group(1))
                if level == 1:
                    title_count += 1
                else:
                    section_count += 1
                continue

            if LIST_ITEM_PATTERN.match(stripped):
                paragraph_count += 1
                in_paragraph = False
                continue

            if not in_paragraph:
                paragraph_count += 1
                in_paragraph = True

        return MarkdownReportShape(
            title_count=title_count,
            section_count=section_count,
            paragraph_count=paragraph_count,
        )

    def _report_shape_feedback(self, shape: MarkdownReportShape) -> Optional[str]:
        missing = []
        if shape.title_count < 1:
            missing.append("缺少一级标题 / missing H1 title")
        if shape.section_count < MIN_REPORT_SECTIONS:
            missing.append(
                f"章节数不足（当前 {shape.section_count}，至少 {MIN_REPORT_SECTIONS}）"
                f" / insufficient sections ({shape.section_count} < {MIN_REPORT_SECTIONS})"
            )
        if shape.paragraph_count < MIN_REPORT_PARAGRAPHS:
            missing.append(
                f"段落数不足（当前 {shape.paragraph_count}，至少 {MIN_REPORT_PARAGRAPHS}）"
                f" / insufficient paragraphs ({shape.paragraph_count} < {MIN_REPORT_PARAGRAPHS})"
            )
        if not missing:
            return None
        return "；".join(missing)

    def _build_iteration_budget_exceeded_message(self) -> str:
        return (
            f"你已经达到最大迭代次数上限（{MAX_LLM_ITERATIONS}）。"
            "禁止继续调用任何工具。请严格基于当前对话记录、已有工具返回结果和上下文，"
            "直接输出最终结论/最终分析报告，不要请求更多外部信息，不要再补查。\n"
            f"You have reached the maximum iteration limit ({MAX_LLM_ITERATIONS}). "
            "Do not call any more tools. Based strictly on the current conversation history, "
            "existing tool outputs, and available context, produce the final conclusion/report now "
            "without requesting additional external information."
        )

    def _build_common_system_prompt(self, skills_catalog_prompt: str = "") -> str:
        common_prompt = templates.get_common_agent_system_prompt()
        if not skills_catalog_prompt:
            return common_prompt
        return f"{common_prompt}\n\n{skills_catalog_prompt}"

    def _build_context_messages(
        self,
        static_context: Dict[str, Any],
        context: Dict[str, Any],
    ) -> list[HumanMessage]:
        """构建传给 Agent 的紧凑上下文消息。

        Args:
            static_context: 工作流固定上下文。
            context: 当前运行时上下文。

        Returns:
            包含静态上下文和运行时上下文的消息列表。
        """
        return [
            HumanMessage(content=(
                "STATIC_CONTEXT:\n"
                f"{stable_json_dumps(static_context)}"
            )),
            HumanMessage(content=(
                "RUNTIME_CONTEXT:\n"
                f"{stable_json_dumps(context)}"
            )),
        ]

    def _format_context_messages_for_prompt_log(self, context_messages: list[HumanMessage]) -> str:
        return "\n\n".join(
            f"User: {message.content}"
            for message in context_messages
        )

    async def _summarize_tool_output(
        self,
        tool_name: str,
        content: str,
        tool_args: Optional[Dict[str, Any]] = None,
        iteration_index: int | None = None,
    ) -> str:
        return await summarize_tool_output(
            self.llm,
            role_name=self.role_name,
            tool_name=tool_name,
            content=content,
            tool_args=tool_args,
            workflow="debate_analysis",
            stage=str(self.stage or self.role_name),
            iteration_index=iteration_index,
        )

    def _record_llm_usage(
        self,
        response: Any,
        *,
        call_kind: str,
        iteration_index: int | None = None,
    ) -> None:
        cache_lane, api_key_alias = get_research_usage_lane()
        record_llm_usage(
            response,
            self.model_name,
            self.role_name,
            session_id=self.state.get("session_id"),
            workflow="debate_analysis",
            stage=str(self.stage or self.role_name),
            call_kind=call_kind,
            iteration_index=iteration_index,
            cache_lane=cache_lane,
            api_key_alias=api_key_alias,
        )

    async def run(
        self,
        static_context: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Runs the agent with the given context.

        Args:
            static_context: Static input fixed for the workflow run.
            context: Runtime input produced during the current workflow.

        Returns:
            Parsed Pydantic object based on get_output_model(), or str if model is str.
        """
        context = dict(context or {})
        output_model = self.get_output_model()

        system_prompt = await self.get_system_prompt(
            trading_frequency=self.trading_frequency or "",
            trading_strategy=self.trading_strategy or "",
        )
        skills_catalog_prompt = build_skills_catalog_prompt()
        common_system_prompt = self._build_common_system_prompt(skills_catalog_prompt)
        context_messages = self._build_context_messages(static_context, context)
        context_prompt_log = self._format_context_messages_for_prompt_log(context_messages)

        if output_model is str:
            messages = [
                SystemMessage(content=common_system_prompt),
                SystemMessage(content=system_prompt),
                *context_messages,
            ]
            self.last_prompt = (
                f"Common System: {common_system_prompt}\n\n"
                f"Role System: {system_prompt}\n\n"
                f"{context_prompt_log}"
            )
        else:
            parser = PydanticOutputParser(pydantic_object=output_model)
            format_instructions = parser.get_format_instructions()
            messages = [
                SystemMessage(content=common_system_prompt),
                SystemMessage(content=system_prompt),
                SystemMessage(content=format_instructions),
                *context_messages,
            ]
            self.last_prompt = (
                f"Common System: {common_system_prompt}\n\n"
                f"Role System: {system_prompt}\n\n"
                f"Format instructions:\n{format_instructions}\n\n"
                f"{context_prompt_log}"
            )

        execution_error: Exception | None = None

        reached_iteration_limit = True

        # 工具调用循环 (Tool-calling loop)
        for i in range(MAX_LLM_ITERATIONS):
            try:
                logger.info(
                    f"[{self.role_name}] Starting LLM iteration "
                    f"{i + 1}/{MAX_LLM_ITERATIONS}..."
                )
                response = await self.llm_with_tools.ainvoke(messages)
                self._record_llm_usage(response, call_kind="agent", iteration_index=i + 1)

                response, invalid_tool_calls = self.llm_provider.sanitize_tool_call_response_for_replay(response)
                messages.append(response)

                if not response.tool_calls and not invalid_tool_calls:
                    reached_iteration_limit = False
                    final_text = (response.content or "").strip()
                    if output_model is str:
                        report_shape = self._measure_markdown_report_shape(final_text)
                        shape_feedback = self._report_shape_feedback(report_shape)
                    else:
                        shape_feedback = None

                    if shape_feedback:
                        logger.warning(
                            f"[{self.role_name}] Final text rejected: {shape_feedback}. "
                            "Requesting the LLM to continue."
                        )
                        messages.append(HumanMessage(content=(
                            f"你的上一条回复结构不完整：{shape_feedback}。"
                            "请继续补全为一份完整的 Markdown 分析报告，补足缺失的章节和内容段落，"
                            "不要重复已经完成的部分。\n"
                            f"Your previous reply is structurally incomplete: {shape_feedback}. "
                            "Please continue the Markdown report, add the missing sections and content paragraphs, "
                            "and avoid repeating parts that are already complete."
                        )))
                        continue
                    role_feedback = await self.get_final_output_feedback(final_text)
                    if role_feedback:
                        logger.warning(
                            f"[{self.role_name}] Final text rejected by role validator. "
                            "Requesting the LLM to continue."
                        )
                        messages.append(HumanMessage(content=role_feedback))
                        continue
                    logger.info(f"[{self.role_name}] No more tool calls at iteration {i+1}.")
                    break

                # 处理工具调用 (Handle tool calls)
                for tool_call in response.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]
                    logger.info(f"[{self.role_name}] Executing tool: {tool_name} with args: {tool_args}")

                    tool_func = next((t for t in self.tools if t.name == tool_name), None)
                    if tool_func:
                        try:
                            result = await tool_func.ainvoke(tool_args)
                            if isinstance(result, (dict, list)):
                                result_str = stable_json_dumps(result)
                            else:
                                result_str = str(result)

                            # [智能摘要逻辑] 仅针对搜索工具且长输出进行精准摘要
                            if should_summarize_tool_output(tool_name, result_str):
                                logger.warning(
                                    f"[{self.role_name}] Tool output(Large) ({len(result_str)} chars) "
                                    f"from '{tool_name}' with args: {tool_args}. Summarizing..."
                                )
                                result_str = await self._summarize_tool_output(
                                    tool_name,
                                    result_str,
                                    tool_args,
                                    iteration_index=i + 1,
                                )
                            else:
                                logger.info(
                                    f"[{self.role_name}] Tool output ({len(result_str)} chars) "
                                    f"from '{tool_name}' with args: {tool_args}. No summarization needed."
                                )

                            # 截断长结果用于日志显示 (Truncate for logging display)
                            display_result = (result_str[:500] + '...') if len(result_str) > 500 else result_str
                            logger.info(f"[{self.role_name}] Tool {tool_name} result: {display_result}")
                            messages.append(ToolMessage(
                                tool_call_id=tool_call["id"],
                                content=result_str
                            ))
                        except Exception as e:
                            logger.error(f"[{self.role_name}] Tool {tool_name} failed: {e}")
                            messages.append(ToolMessage(
                                tool_call_id=tool_call["id"],
                                content=f"Error: {e}"
                            ))
                    else:
                        logger.error(f"[{self.role_name}] Tool {tool_name} not found")
                        messages.append(ToolMessage(
                            tool_call_id=tool_call["id"],
                            content=f"Error: Tool {tool_name} not found"
                        ))

                if invalid_tool_calls:
                    logger.warning(
                        f"[{self.role_name}] Received {len(invalid_tool_calls)} invalid tool call(s); "
                        "requesting the LLM to retry with valid JSON arguments."
                    )
                    messages.append(
                        HumanMessage(
                            content=self.llm_provider.build_invalid_tool_call_retry_message(invalid_tool_calls)
                        )
                    )
            except Exception as e:
                logger.exception(f"[{self.role_name}] Execution error at iteration {i}: {e}")
                execution_error = e
                break

        if execution_error is not None:
            raise execution_error

        if reached_iteration_limit:
            logger.warning(
                f"[{self.role_name}] Reached max iterations ({MAX_LLM_ITERATIONS}); "
                "switching to final answer mode without tools."
            )
            messages.append(HumanMessage(content=self._build_iteration_budget_exceeded_message()))
            final_response = await self.llm.ainvoke(messages)
            self._record_llm_usage(
                final_response,
                call_kind="final_no_tools",
                iteration_index=MAX_LLM_ITERATIONS + 1,
            )
            messages.append(final_response)

        # 解析最终输出 (Parse final output)
        final_content = (messages[-1].content or "").strip()
        if output_model is str:
            report_shape = self._measure_markdown_report_shape(final_content)
            shape_feedback = self._report_shape_feedback(report_shape)
            if shape_feedback:
                raise ValueError(f"Incomplete report output: {shape_feedback}")
            role_feedback = await self.get_final_output_feedback(final_content)
            if role_feedback:
                raise ValueError(role_feedback)
            return final_content
        else:
            parser = PydanticOutputParser(pydantic_object=output_model)
            try:
                # 尝试提取纯 JSON（去除 markdown 代码围栏）
                cleaned = self._extract_json_from_content(final_content)
                return parser.parse(cleaned)
            except Exception as e:
                last_error = e
                for retry_index in range(STRUCTURED_OUTPUT_RETRY_LIMIT):
                    logger.warning(
                        f"[{self.role_name}] Failed to parse final output. "
                        f"Requesting JSON-only retry {retry_index + 1}/"
                        f"{STRUCTURED_OUTPUT_RETRY_LIMIT} without tools: {last_error}"
                    )
                    messages.append(HumanMessage(content=(
                        "你的上一条最终回复不是合法 JSON，无法被系统解析。"
                        "不要调用任何工具；请严格按下面的 schema 重新输出一个 JSON 对象，"
                        "不要输出解释文字或代码围栏。\n"
                        "Your previous final response was not valid JSON. Do not call any tools. "
                        "Return one JSON object only, following this schema:\n\n"
                        f"{parser.get_format_instructions()}"
                    )))
                    retry_response = await self.llm.ainvoke(messages)
                    self._record_llm_usage(
                        retry_response,
                        call_kind="json_retry",
                        iteration_index=retry_index + 1,
                    )
                    messages.append(retry_response)
                    retry_content = (retry_response.content or "").strip()
                    try:
                        # 同样尝试提取纯 JSON
                        cleaned_retry = self._extract_json_from_content(retry_content)
                        return parser.parse(cleaned_retry)
                    except Exception as retry_error:
                        last_error = retry_error

                logger.error(
                    f"[{self.role_name}] Failed to parse JSON retry output after "
                    f"{STRUCTURED_OUTPUT_RETRY_LIMIT} attempts: {last_error}"
                )
                raise last_error from e
