from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.stock_picker.interactive_research.models import (
    InteractiveResearchMessage,
    InteractiveResearchRun,
)


def append_message(
    db: Session,
    run: InteractiveResearchRun,
    *,
    role: str,
    message_type: str,
    content: str,
    payload: Optional[Dict[str, Any]] = None,
    parent_message_id: Optional[UUID] = None,
    status: str = "completed",
    visible_to_user: bool = True,
) -> InteractiveResearchMessage:
    """向 run 的聊天流追加一条消息。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        role: 消息角色。
        message_type: 消息类型。
        content: 展示文本。
        payload: 小型结构化 payload。
        parent_message_id: 父消息 ID。
        status: 消息状态。
        visible_to_user: 是否展示给用户。

    Returns:
        已写入数据库的消息对象。
    """
    message = InteractiveResearchMessage(
        run_id=run.run_id,
        role=role,
        message_type=message_type,
        content=content,
        payload=payload or {},
        parent_message_id=parent_message_id,
        sequence_no=next_message_sequence(db, run.run_id),
        status=status,
        visible_to_user=visible_to_user,
    )
    db.add(message)
    db.flush()
    return message


def write_checkpoint(
    db: Session,
    run: InteractiveResearchRun,
    *,
    reason: str,
    extra_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """更新 run 的最小恢复 checkpoint。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        reason: checkpoint 生成原因。
        extra_payload: 额外恢复上下文。

    Returns:
        已写入 run 的 checkpoint payload。
    """
    existing_payload = run.checkpoint_payload if isinstance(run.checkpoint_payload, dict) else {}
    checkpoint_payload = {
        "status": run.status,
        "current_stage": run.current_stage,
        "current_phase": run.current_phase,
        "pending_message_id": str(run.pending_message_id) if run.pending_message_id else None,
        "version": run.version,
        "reason": reason,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "llm_usage": existing_payload.get("llm_usage") or {},
        **(extra_payload or {}),
    }
    run.checkpoint_payload = checkpoint_payload
    db.flush()
    return checkpoint_payload


def accumulate_llm_usage(
    db: Session,
    run: InteractiveResearchRun,
    usage_record: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """把一次 LLM 调用的 usage 累加到 run checkpoint。

    Args:
        db: 数据库会话。
        run: 当前研究 run。
        usage_record: record_llm_usage 返回的单次调用记录。

    Returns:
        累加后的 run 级 usage 汇总。
    """
    checkpoint_payload = dict(run.checkpoint_payload or {})
    current_usage = dict(checkpoint_payload.get("llm_usage") or {})
    if not usage_record:
        return current_usage

    current_usage["calls"] = int(current_usage.get("calls") or 0) + 1
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_tokens",
        "cache_miss_tokens",
        "reasoning_tokens",
    ):
        current_usage[key] = int(current_usage.get(key) or 0) + int(usage_record.get(key) or 0)

    input_tokens = int(current_usage.get("input_tokens") or 0)
    cached_tokens = int(current_usage.get("cached_tokens") or 0)
    current_usage["cache_hit_rate"] = cached_tokens / input_tokens if input_tokens > 0 else 0.0
    checkpoint_payload["llm_usage"] = current_usage
    run.checkpoint_payload = checkpoint_payload
    db.flush()
    return current_usage


def next_message_sequence(db: Session, run_id: UUID) -> int:
    """计算 run 内下一条消息序号。

    Args:
        db: 数据库会话。
        run_id: 研究 run ID。

    Returns:
        下一条从 1 开始递增的序号。
    """
    current = (
        db.query(func.max(InteractiveResearchMessage.sequence_no))
        .filter(InteractiveResearchMessage.run_id == run_id)
        .scalar()
    )
    return int(current or 0) + 1
