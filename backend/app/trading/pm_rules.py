"""PM 决策纪律的持久化与盘中判定。

PM 每轮辩论输出的 stop_loss / take_profit / holding_horizon_days 三个结构化字段
由 `sync_pm_discipline_to_position` 写到对应持仓行；market_watch 盘中扫描调用
`evaluate_position_disciplines` 做纯确定性判定，触发后只发布事件并启动复议辩论，
不直接下单。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core import database as database_module
from app.core.logger import get_logger
from app.models.account import Account
from app.models.position import Position
from app.models.user import User

logger = get_logger(__name__)

TRIGGER_STOP_LOSS = "stop_loss"
TRIGGER_TAKE_PROFIT = "take_profit"
TRIGGER_HORIZON_EXPIRED = "horizon_expired"


def _to_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _positive_trigger_price(value: Any) -> Decimal | None:
    """解析需要写入持仓监控的正数价格。

    Args:
        value: PM 决策输出中的价格字段。

    Returns:
        大于 0 的价格；缺失、非法或非正数时返回 None。
    """
    price = _to_decimal(value)
    if price is None or price <= 0:
        return None
    return price


async def sync_pm_discipline_to_position(
    *,
    session_id: Any,
    user_id: int | None,
    stock_code: str,
    decision: dict[str, Any],
) -> bool:
    """把 PM 决策的止损/止盈/持有期写到对应持仓行。

    Args:
        session_id: 给出该纪律的辩论会话 ID。
        user_id: 决策所属用户 ID。
        stock_code: 标准股票代码。
        decision: PM 结构化决策字段。

    Returns:
        是否写入成功。无持仓（如 buy 未成交）时跳过并返回 False。
    """
    if user_id is None:
        logger.warning("sync_pm_discipline_to_position skipped: user_id missing")
        return False

    async with database_module.AsyncSessionLocal() as db:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalars().first()
        if user is None:
            logger.warning("sync_pm_discipline_to_position skipped: user not found", extra={"user_id": user_id})
            return False

        account_result = await db.execute(select(Account).where(Account.user_id == user.id))
        account = account_result.scalars().first()
        if account is None:
            account = Account(
                user_id=user.id,
                total_assets=Decimal("1000000.00"),
                initial_capital=Decimal("1000000.00"),
                available_cash=Decimal("1000000.00"),
                frozen_cash=Decimal("0.00"),
                market_value=Decimal("0.00"),
                total_profit_loss=Decimal("0.00"),
                profit_loss_pct=Decimal("0.00"),
                total_trades=0,
                win_rate=Decimal("0.00"),
            )
            db.add(account)
            await db.flush()

        position_result = await db.execute(
            select(Position).where(
                Position.account_id == account.account_id,
                Position.stock_code == stock_code,
            )
        )
        position = position_result.scalars().first()
        if position is None or not (position.total_shares or 0):
            logger.info(
                "sync_pm_discipline_to_position skipped: no open position; discipline will be backfilled after open",
                extra={"stock_code": stock_code, "user_id": user_id},
            )
            return False

        stop_loss = _positive_trigger_price(decision.get("stop_loss"))
        take_profit = _positive_trigger_price(decision.get("take_profit"))
        horizon_days = decision.get("holding_horizon_days")
        horizon_deadline = None
        try:
            if horizon_days and int(horizon_days) > 0:
                horizon_deadline = datetime.now() + timedelta(days=int(horizon_days))
        except (TypeError, ValueError):
            horizon_deadline = None

        # 合理性检查：buy/hold 时应满足 stop_loss < 现价 < take_profit。异常仅告警，不阻塞写入。
        current_price = _to_decimal(position.current_price)
        decision_action = str(decision.get("decision") or "").lower()
        if decision_action in ("buy", "hold") and current_price is not None:
            if stop_loss is not None and stop_loss >= current_price:
                logger.warning(
                    "PM discipline sanity warning: stop_loss is not below current price",
                    extra={"stock_code": stock_code, "stop_loss": str(stop_loss), "current_price": str(current_price)},
                )
            if take_profit is not None and take_profit <= current_price:
                logger.warning(
                    "PM discipline sanity warning: take_profit is not above current price",
                    extra={"stock_code": stock_code, "take_profit": str(take_profit), "current_price": str(current_price)},
                )

        position.stop_loss = stop_loss
        position.take_profit = take_profit
        position.horizon_deadline = horizon_deadline
        position.pm_session_id = session_id
        await db.commit()
        logger.info(
            "PM discipline synced to position",
            extra={
                "stock_code": stock_code,
                "stop_loss": str(stop_loss) if stop_loss is not None else None,
                "take_profit": str(take_profit) if take_profit is not None else None,
                "horizon_deadline": horizon_deadline.isoformat() if horizon_deadline else None,
                "session_id": str(session_id),
            },
        )
        return True


async def evaluate_position_disciplines(*, user_id: int) -> list[dict[str, Any]]:
    """对用户所有带 PM 纪律的持仓做确定性触发判定。纯比较，无 LLM。

    Args:
        user_id: 用户 ID。

    Returns:
        触发列表，每项含 stock_code / trigger / threshold / latest_price / pm_session_id。
    """
    from app.ai.agentic.tools import _resolve_latest_stock_price

    async with database_module.AsyncSessionLocal() as db:
        user_result = await db.execute(select(User).where(User.id == user_id))
        user = user_result.scalars().first()
        if user is None:
            return []
        account_result = await db.execute(select(Account).where(Account.user_id == user.id))
        account = account_result.scalars().first()
        if account is None:
            account = Account(
                user_id=user.id,
                total_assets=Decimal("1000000.00"),
                initial_capital=Decimal("1000000.00"),
                available_cash=Decimal("1000000.00"),
                frozen_cash=Decimal("0.00"),
                market_value=Decimal("0.00"),
                total_profit_loss=Decimal("0.00"),
                profit_loss_pct=Decimal("0.00"),
                total_trades=0,
                win_rate=Decimal("0.00"),
            )
            db.add(account)
            await db.flush()

        positions_result = await db.execute(
            select(Position).where(
                Position.account_id == account.account_id,
                Position.total_shares > 0,
            )
        )
        positions = positions_result.scalars().all()

        triggered: list[dict[str, Any]] = []
        now = datetime.now()
        for position in positions:
            if position.stop_loss is None and position.take_profit is None and position.horizon_deadline is None:
                continue

            price_info = await _resolve_latest_stock_price(position.stock_code)
            latest_price = _to_decimal(price_info.get("latest_price")) if price_info.get("success") else None

            trigger = None
            threshold: Any = None
            if latest_price is not None and position.stop_loss is not None and latest_price <= position.stop_loss:
                trigger, threshold = TRIGGER_STOP_LOSS, position.stop_loss
            elif latest_price is not None and position.take_profit is not None and latest_price >= position.take_profit:
                trigger, threshold = TRIGGER_TAKE_PROFIT, position.take_profit
            elif position.horizon_deadline is not None and now >= position.horizon_deadline:
                trigger, threshold = TRIGGER_HORIZON_EXPIRED, position.horizon_deadline

            if trigger is None:
                continue
            triggered.append({
                "stock_code": position.stock_code,
                "trigger": trigger,
                "threshold": str(threshold),
                "latest_price": str(latest_price) if latest_price is not None else None,
                "pm_session_id": str(position.pm_session_id) if position.pm_session_id else None,
            })
            logger.info(
                "PM discipline triggered",
                extra={
                    "stock_code": position.stock_code,
                    "trigger": trigger,
                    "threshold": str(threshold),
                    "latest_price": str(latest_price) if latest_price is not None else None,
                },
            )
        return triggered
