from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import desc, select

from app.core import database as database_module
from app.models.account import Account
from app.models.data_storage import KlineData, StockRealtimeMarket
from app.models.order import Order
from app.models.position import Position
from app.models.session import Session as DebateSession
from app.trading.service import _order_remaining_shares
from app.trading.trading_engine import TradingEngine


_TRADING_ENGINE = TradingEngine()
_LOT_SIZE = TradingEngine.MIN_TRANSACTION_SHARES


def build_executable_position_plan(
    *,
    target_position: float,
    price: float,
    total_assets: float,
    available_cash: float,
    current_total_shares: int,
    current_available_shares: int,
    pending_buy_shares: int,
    pending_sell_shares: int,
    order_price: float | None = None,
) -> dict[str, Any]:
    """根据账户快照计算可执行目标仓位。

    Args:
        target_position: 交易后的绝对目标仓位。
        price: 本次计算采用的股票价格。
        total_assets: 账户总资产。
        available_cash: 账户可用现金。
        current_total_shares: 当前总持股数。
        current_available_shares: 当前可卖股数。
        pending_buy_shares: 待成交买单剩余股数。
        pending_sell_shares: 待成交卖单剩余股数。
        order_price: 实际委托价格；仅用于金额、费用和资金校验。

    Returns:
        可执行方向、整手数量和实际目标仓位。
    """
    normalized_target = float(target_position)
    if normalized_target < 0 or normalized_target > 1:
        return _failure("invalid_target_position", "target_position must be between 0 and 1")
    if price <= 0:
        return _failure("price_unavailable", "A positive reference price is required")
    normalized_order_price = float(order_price) if order_price is not None else float(price)
    if normalized_order_price <= 0:
        return _failure("invalid_order_price", "A positive order price is required")
    if total_assets <= 0:
        return _failure("invalid_total_assets", "Account total_assets must be greater than 0")

    normalized_total_shares = max(int(current_total_shares or 0), 0)
    normalized_available_shares = min(
        max(int(current_available_shares or 0), 0),
        normalized_total_shares,
    )
    normalized_pending_buy_shares = max(int(pending_buy_shares or 0), 0)
    normalized_pending_sell_shares = max(int(pending_sell_shares or 0), 0)
    effective_total_shares = max(
        normalized_total_shares
        + normalized_pending_buy_shares
        - normalized_pending_sell_shares,
        0,
    )
    available_shares_after_pending_orders = max(
        normalized_available_shares - normalized_pending_sell_shares,
        0,
    )
    raw_target_shares = total_assets * normalized_target / price
    raw_delta_shares = raw_target_shares - effective_total_shares
    current_position = normalized_total_shares * price / total_assets
    effective_position = effective_total_shares * price / total_assets
    minimum_lot_value = price * _LOT_SIZE
    minimum_lot_position = minimum_lot_value / total_assets

    plan = {
        "success": True,
        "executable": False,
        "reason": None,
        "action": "hold",
        "requested_target_position": normalized_target,
        "position_reference_price": float(price),
        "order_price": normalized_order_price,
        "current_position": current_position,
        "effective_position": effective_position,
        "raw_target_shares": raw_target_shares,
        "raw_delta_shares": raw_delta_shares,
        "current_total_shares": normalized_total_shares,
        "current_available_shares": normalized_available_shares,
        "pending_order_policy": "retain",
        "pending_buy_shares": normalized_pending_buy_shares,
        "pending_sell_shares": normalized_pending_sell_shares,
        "effective_total_shares": effective_total_shares,
        "available_shares_after_pending_orders": available_shares_after_pending_orders,
        "lot_size": _LOT_SIZE,
        "minimum_lot_value": minimum_lot_value,
        "minimum_lot_position": minimum_lot_position,
        "order_shares": 0,
        "actual_target_shares": effective_total_shares,
        "actual_target_position": effective_position,
        "estimated_fee": 0.0,
        "estimated_trade_value": 0.0,
        "target_fully_reachable": True,
    }

    if abs(raw_delta_shares) < 1e-9:
        plan["reason"] = (
            "target_covered_by_pending_orders"
            if normalized_pending_buy_shares or normalized_pending_sell_shares
            else "target_already_met"
        )
        return plan

    if raw_delta_shares > 0:
        plan["action"] = "buy"
        if normalized_pending_sell_shares > 0:
            plan["reason"] = "pending_sell_order_conflicts_with_target"
            plan["target_fully_reachable"] = False
            return plan
        rounded_order_shares = (int(raw_delta_shares) // _LOT_SIZE) * _LOT_SIZE
        if rounded_order_shares <= 0:
            plan["reason"] = "below_minimum_buy_lot"
            plan["target_fully_reachable"] = False
            return plan

        estimated_fee = _TRADING_ENGINE.calculate_fee(
            normalized_order_price,
            rounded_order_shares,
            True,
        )["total_fee"]
        estimated_trade_value = normalized_order_price * rounded_order_shares
        if estimated_trade_value + estimated_fee > max(float(available_cash or 0), 0.0):
            plan["reason"] = "insufficient_available_cash"
            plan["target_fully_reachable"] = False
            return plan

        actual_target_shares = effective_total_shares + rounded_order_shares
        plan.update(
            {
                "executable": True,
                "order_shares": rounded_order_shares,
                "actual_target_shares": actual_target_shares,
                "actual_target_position": actual_target_shares * price / total_assets,
                "estimated_fee": estimated_fee,
                "estimated_trade_value": estimated_trade_value,
            }
        )
        return plan

    plan["action"] = "sell"
    if normalized_pending_buy_shares > 0:
        plan["reason"] = "pending_buy_order_conflicts_with_target"
        plan["target_fully_reachable"] = False
        return plan
    desired_sell_shares = (int(abs(raw_delta_shares)) // _LOT_SIZE) * _LOT_SIZE
    available_lot_shares = (available_shares_after_pending_orders // _LOT_SIZE) * _LOT_SIZE
    order_shares = min(desired_sell_shares, available_lot_shares)
    if order_shares <= 0:
        if normalized_pending_sell_shares > 0 and available_shares_after_pending_orders <= 0:
            plan["reason"] = "insufficient_available_shares_after_pending_orders"
        elif normalized_available_shares <= 0:
            plan["reason"] = "no_available_shares"
        else:
            plan["reason"] = "below_minimum_sell_lot"
        plan["target_fully_reachable"] = False
        return plan

    actual_target_shares = effective_total_shares - order_shares
    target_fully_reachable = order_shares == desired_sell_shares
    estimated_fee = _TRADING_ENGINE.calculate_fee(
        normalized_order_price,
        order_shares,
        False,
    )["total_fee"]
    plan.update(
        {
            "executable": True,
            "reason": None if target_fully_reachable else "partially_executable_available_shares",
            "order_shares": order_shares,
            "actual_target_shares": actual_target_shares,
            "actual_target_position": actual_target_shares * price / total_assets,
            "estimated_fee": estimated_fee,
            "estimated_trade_value": normalized_order_price * order_shares,
            "target_fully_reachable": target_fully_reachable,
        }
    )
    return plan


async def calculate_executable_position_plan(
    *,
    session_id: UUID | str,
    target_position: float,
) -> dict[str, Any]:
    """读取 PM 会话和账户数据并计算可执行仓位。

    Args:
        session_id: 当前辩论会话 ID。
        target_position: 交易后的绝对目标仓位。

    Returns:
        带行情来源和账户约束的仓位计算结果。
    """
    try:
        normalized_session_id = UUID(str(session_id))
    except (TypeError, ValueError):
        return _failure("invalid_session_id", "session_id must be a valid UUID")

    async with database_module.AsyncSessionLocal() as db:
        session_obj = (
            await db.execute(
                select(DebateSession).where(DebateSession.session_id == normalized_session_id)
            )
        ).scalar_one_or_none()
        if session_obj is None:
            return _failure("session_not_found", "Debate session was not found")
        if not session_obj.stock_code:
            return _failure("stock_code_missing", "Debate session stock_code is required")

        account = (
            await db.execute(select(Account).where(Account.user_id == session_obj.user_id))
        ).scalars().first()
        if account is None:
            return _failure("account_not_found", "Associated account was not found")

        price, price_source, price_as_of = await load_reference_price(db, session_obj.stock_code)
        if price <= 0:
            return _failure("price_unavailable", "A valid stock price could not be determined")

        position = (
            await db.execute(
                select(Position).where(
                    Position.account_id == account.account_id,
                    Position.stock_code == session_obj.stock_code,
                )
            )
        ).scalar_one_or_none()
        position_snapshot = _TRADING_ENGINE.build_position_snapshot(position) if position else {}
        pending_buy_shares, pending_sell_shares = await load_pending_order_shares(
            db,
            account.account_id,
            session_obj.stock_code,
        )

        plan = build_executable_position_plan(
            target_position=target_position,
            price=price,
            total_assets=float(account.total_assets or 0),
            available_cash=float(account.available_cash or 0),
            current_total_shares=int(position_snapshot.get("current_shares", 0) or 0),
            current_available_shares=int(position_snapshot.get("available_shares", 0) or 0),
            pending_buy_shares=pending_buy_shares,
            pending_sell_shares=pending_sell_shares,
        )
        plan.update(
            {
                "stock_code": session_obj.stock_code,
                "price": price,
                "price_source": price_source,
                "price_as_of": price_as_of,
                "total_assets": float(account.total_assets or 0),
                "available_cash": float(account.available_cash or 0),
            }
        )
        return plan


async def load_pending_order_shares(
    db: Any,
    account_id: UUID,
    stock_code: str,
) -> tuple[int, int]:
    """读取同账户同股票待成交限价单的剩余股数。

    Args:
        db: 数据库会话。
        account_id: 账户 ID。
        stock_code: 股票代码。

    Returns:
        待买股数和待卖股数。
    """
    orders = (
        await db.execute(
            select(Order).where(
                Order.account_id == account_id,
                Order.stock_code == stock_code,
                Order.order_type == "limit",
                Order.status == "pending",
                Order.action.in_(("buy", "sell")),
            )
        )
    ).scalars().all()
    pending_buy_shares = sum(
        _order_remaining_shares(order) for order in orders if order.action == "buy"
    )
    pending_sell_shares = sum(
        _order_remaining_shares(order) for order in orders if order.action == "sell"
    )
    return pending_buy_shares, pending_sell_shares


async def load_reference_price(
    db: Any,
    stock_code: str,
) -> tuple[float, str | None, str | None]:
    """读取仓位计算使用的统一市场参考价。

    Args:
        db: 数据库会话。
        stock_code: 股票代码。

    Returns:
        参考价、行情来源和行情时间。
    """
    latest_market = (
        await db.execute(
            select(StockRealtimeMarket)
            .where(StockRealtimeMarket.stock_code == stock_code)
            .order_by(desc(StockRealtimeMarket.timestamp))
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_market is not None and float(latest_market.current_price or 0) > 0:
        timestamp = latest_market.timestamp.isoformat() if latest_market.timestamp else None
        return float(latest_market.current_price), "realtime", timestamp

    latest_kline = (
        await db.execute(
            select(KlineData)
            .where(KlineData.stock_code == stock_code, KlineData.freq == "D")
            .order_by(desc(KlineData.date))
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_kline is not None and float(latest_kline.close or 0) > 0:
        trade_date = latest_kline.date.isoformat() if latest_kline.date else None
        return float(latest_kline.close), "daily_close", trade_date
    return 0.0, None, None


def _failure(reason: str, message: str) -> dict[str, Any]:
    return {
        "success": False,
        "executable": False,
        "reason": reason,
        "message": message,
    }
