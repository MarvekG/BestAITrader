from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Literal

from langchain.tools import tool
from pydantic import BaseModel, Field


FLOW_CONTROL_STATUSES = {"continue", "ask", "done"}
FLOW_CONTROL_TOOL_NAME = "control_research_flow"


class FlowControlToolInput(BaseModel):
    """Research Agent 流程控制工具入参。"""

    action: Literal["continue", "ask", "done"] = Field(
        ...,
        description="下一步流程动作：continue 继续研究，ask 暂停并向用户提问，done 输出最终答案。",
    )
    message: str = Field(..., min_length=1, description="展示给用户的进展、问题或最终 Markdown 答案。")


@dataclass(frozen=True)
class FlowControlDecision:
    """LLM 对交互式研究下一步流程的最小决策。"""

    status: str
    message: str


@tool(
    FLOW_CONTROL_TOOL_NAME,
    args_schema=FlowControlToolInput,
    description=(
        "Internal control tool for the interactive stock research workflow. Use it when you want "
        "to report progress, ask the user a question, or provide the final answer. If the same assistant turn also "
        "contains evidence-gathering tools, the workflow executes those tools before applying this decision."
    ),
)
async def control_research_flow(action: str, message: str) -> dict[str, str]:
    """返回流程控制参数，实际状态迁移由 workflow 处理。

    Args:
        action: 下一步流程动作。
        message: 展示给用户的进展、问题或最终答案。

    Returns:
        结构化流程控制参数。
    """
    return {"action": action, "message": message}


def flow_control_decision_from_tool_args(args: Any) -> FlowControlDecision:
    """从流程控制工具参数解析决策。

    Args:
        args: LLM tool_call 中的 args 字段。

    Returns:
        已校验的流程控制决策。

    Raises:
        ValueError: 参数缺失、动作不合法或正文为空时抛出。
    """
    if not isinstance(args, dict):
        raise ValueError("Flow control tool args must be an object")
    action = str(args.get("action") or "").strip().lower()
    message = str(args.get("message") or "").strip()
    if action not in FLOW_CONTROL_STATUSES:
        raise ValueError(f"Unsupported flow control action: {action}")
    if not message:
        raise ValueError("Flow control message cannot be empty")
    return FlowControlDecision(status=action, message=message)
