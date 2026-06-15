from __future__ import annotations
from dataclasses import dataclass
from typing import Any


FLOW_CONTROL_STATUSES = {"continue", "ask", "done"}


@dataclass(frozen=True)
class FlowControlDecision:
    """LLM 对交互式研究下一步流程的最小决策。"""

    status: str
    message: str


def parse_flow_control_decision(content: Any) -> FlowControlDecision:
    """解析 LLM 输出的首行动作协议。

    Args:
        content: LLM 返回的文本内容，第一行必须是 ACTION: CONTINUE|ASK|DONE。

    Returns:
        已校验的流程控制决策。

    Raises:
        ValueError: 内容缺少动作行、动作不合法或正文为空时抛出。
    """
    raw_content = str(content or "").strip()
    if not raw_content:
        raise ValueError("Flow control output cannot be empty")

    first_line, separator, remainder = raw_content.partition("\n")
    if not separator:
        raise ValueError("Flow control output must start with ACTION and include body text")
    prefix, action_separator, action = first_line.strip().partition(":")
    if prefix.strip().lower() != "action" or not action_separator:
        raise ValueError("Flow control first line must be ACTION: CONTINUE|ASK|DONE")

    status = action.strip().lower()
    message = remainder.strip()
    if status not in FLOW_CONTROL_STATUSES:
        raise ValueError(f"Unsupported flow control action: {status}")
    if not message:
        raise ValueError("Flow control body cannot be empty")
    return FlowControlDecision(status=status, message=message)
