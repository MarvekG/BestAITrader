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
    checkpoint_payload = {
        "status": run.status,
        "current_stage": run.current_stage,
        "current_phase": run.current_phase,
        "pending_message_id": str(run.pending_message_id) if run.pending_message_id else None,
        "version": run.version,
        "reason": reason,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        **(extra_payload or {}),
    }
    run.checkpoint_payload = checkpoint_payload
    db.flush()
    return checkpoint_payload


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
