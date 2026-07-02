from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.core import database as database_module
from app.core.logger import get_logger
from app.models.pm_decision import PMDecisionRecord
from app.models.session import Session as DebateSession
from app.trading.pm_rules import sync_pm_discipline_to_position

logger = get_logger(__name__)


def normalize_pm_decision_payload(
    *,
    target_position: float,
    confidence_score: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    holding_horizon_days: int | None = None,
) -> dict[str, Any]:
    """校验并归一化 PM 结构化决策字段。

    Args:
        target_position: 操作完成后的目标仓位比例。
        confidence_score: PM 对本次决策的置信度。
        stop_loss: 止损或复议价格。
        take_profit: 止盈或目标价格。
        holding_horizon_days: 预期持有或复议周期天数。

    Returns:
        归一化后的字段字典。

    Raises:
        ValueError: 字段缺失或超出允许范围时抛出。
    """
    normalized_target = float(target_position)
    if normalized_target < 0 or normalized_target > 1:
        raise ValueError("target_position must be between 0 and 1")

    normalized_confidence = float(confidence_score)
    if normalized_confidence < 0 or normalized_confidence > 100:
        raise ValueError("confidence_score must be between 0 and 100")

    normalized_stop_loss = _normalize_optional_positive_float(stop_loss)
    normalized_take_profit = _normalize_optional_positive_float(take_profit)
    normalized_horizon = _normalize_optional_positive_int(holding_horizon_days)

    return {
        "target_position": normalized_target,
        "confidence_score": normalized_confidence,
        "stop_loss": normalized_stop_loss,
        "take_profit": normalized_take_profit,
        "holding_horizon_days": normalized_horizon,
    }


async def save_pm_decision_record(
    *,
    session_id: UUID | str,
    target_position: float,
    confidence_score: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    holding_horizon_days: int | None = None,
    source: str = "pm_tool",
) -> dict[str, Any]:
    """保存 PM 结构化决策并同步持仓纪律。

    Args:
        session_id: Debate 会话 ID。
        target_position: 目标仓位比例。
        confidence_score: 置信度。
        stop_loss: 止损或复议价格。
        take_profit: 止盈或目标价格。
        holding_horizon_days: 预期持有或复议周期天数。
        source: 保存来源。

    Returns:
        保存后的决策字典。

    Raises:
        ValueError: 会话不存在、字段非法或用户缺失时抛出。
    """
    async with database_module.AsyncSessionLocal() as db:
        session_result = await db.execute(select(DebateSession).where(DebateSession.session_id == session_id))
        session_obj = session_result.scalar_one_or_none()
        if session_obj is None:
            raise ValueError("session_id does not exist")
        if session_obj.user_id is None:
            raise ValueError("session user_id is required")

        payload = normalize_pm_decision_payload(
            target_position=target_position,
            confidence_score=confidence_score,
            stop_loss=stop_loss,
            take_profit=take_profit,
            holding_horizon_days=holding_horizon_days,
        )

        record_result = await db.execute(
            select(PMDecisionRecord).where(PMDecisionRecord.session_id == session_obj.session_id)
        )
        record = record_result.scalar_one_or_none()
        if record is None:
            record = PMDecisionRecord(
                session_id=session_obj.session_id,
                user_id=session_obj.user_id,
                stock_code=session_obj.stock_code,
                source=source,
            )
            db.add(record)

        record.user_id = session_obj.user_id
        record.stock_code = session_obj.stock_code
        record.target_position = payload["target_position"]
        record.confidence_score = payload["confidence_score"]
        record.stop_loss = payload["stop_loss"]
        record.take_profit = payload["take_profit"]
        record.holding_horizon_days = payload["holding_horizon_days"]
        record.source = source
        await db.commit()
        await db.refresh(record)

        result = record.to_dict()

    try:
        await sync_pm_discipline_to_position(
            session_id=session_id,
            user_id=result["user_id"],
            stock_code=result["stock_code"],
            decision=result,
        )
    except Exception:
        logger.exception("Failed to sync PM discipline after saving PM decision")
    return result


async def get_pm_decision_for_session(session_id: UUID | str) -> dict[str, Any]:
    """查询指定会话的 PM 结构化决策。

    Args:
        session_id: Debate 会话 ID。

    Returns:
        PM 决策字典；不存在时返回空字典。
    """
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(select(PMDecisionRecord).where(PMDecisionRecord.session_id == session_id))
        record = result.scalar_one_or_none()
        return record.to_dict() if record else {}


def _normalize_optional_positive_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    numeric = float(value)
    if numeric <= 0:
        return None
    return numeric


def _normalize_optional_positive_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    numeric = int(value)
    if numeric <= 0:
        return None
    return numeric
