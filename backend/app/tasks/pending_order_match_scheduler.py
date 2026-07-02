from typing import Any
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func
from sqlalchemy import select

from app.core import database as database_module
from app.core.logger import get_logger
from app.models.account import Account
from app.models.order import Order
from app.models.position import Position
from app.models.trade_record import TradeRecord
from app.tasks.scheduled_task_registry import ScheduledTask, ScheduledTaskSnapshot
from app.trading.service import _extract_pending_order_stop_loss
from app.trading.service import _limit_order_triggered
from app.trading.service import _merge_purchase_details_with_stop_loss
from app.trading.service import _order_remaining_shares
from app.trading.service import _resolve_order_price
from app.trading.service import trading_service
from app.data.market_utils import is_trading_time

logger = get_logger(__name__)

PENDING_ORDER_MATCH_JOB_ID = "pending_order_match_scan"
PENDING_ORDER_MATCH_LIMIT = 200


def _calculate_buy_reservation(price: float | Decimal, shares: int) -> Decimal:
    """计算买入限价挂单冻结现金。"""
    price_decimal = Decimal(str(price))
    turnover = price_decimal * Decimal(str(shares))
    fee = trading_service.engine.calculate_fee(float(price_decimal), shares, True)["total_fee"]
    return turnover + Decimal(str(fee))


async def _get_pending_sell_reserved_shares(
    db,
    *,
    account_id: object,
    stock_code: str,
    exclude_order_id: object,
) -> int:
    """异步统计同账户同股票其他待成交卖单占用股数。"""
    result = await db.execute(
        select(Order).where(
            Order.account_id == account_id,
            Order.stock_code == stock_code,
            Order.action == "sell",
            Order.order_type == "limit",
            Order.status == "pending",
            Order.order_id != exclude_order_id,
        )
    )
    return sum(_order_remaining_shares(order) for order in result.scalars().all())


async def _validate_sell_limit_reservation(db, *, order: Order, position: Position | None) -> dict[str, Any]:
    """异步校验待成交卖单是否仍满足可卖股数约束。"""
    position_snapshot = trading_service.engine.build_position_snapshot(position) if position else None
    executable_shares = trading_service.engine.get_executable_sell_shares(position_snapshot)
    reserved_shares = await _get_pending_sell_reserved_shares(
        db,
        account_id=order.account_id,
        stock_code=order.stock_code,
        exclude_order_id=order.order_id,
    )
    available_after_reservation = max(executable_shares - reserved_shares, 0)
    if _order_remaining_shares(order) > available_after_reservation:
        return {
            "success": False,
            "message": "Insufficient available shares after pending sell orders",
            "reason": "insufficient_available_shares_after_pending_orders",
            "available_shares": executable_shares,
            "reserved_shares": reserved_shares,
        }
    return {"success": True, "available_shares": executable_shares, "reserved_shares": reserved_shares}


async def _match_pending_order(db, order_id: UUID) -> dict[str, Any]:
    """异步撮合单笔待成交限价单。"""
    locked_order_result = await db.execute(
        select(Order)
        .where(Order.order_id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked_order = locked_order_result.scalar_one_or_none()
    if not locked_order:
        return {"success": False, "matched": False, "reason": "order_not_found"}
    if locked_order.status != "pending" or locked_order.order_type != "limit":
        return {"success": False, "matched": False, "reason": "order_not_matchable", "order": locked_order}

    latest_price = await _resolve_order_price(locked_order.stock_code, "market", 0.0)
    if latest_price is None or latest_price <= 0:
        return {"success": False, "matched": False, "reason": "market_price_unavailable", "order": locked_order}
    if not _limit_order_triggered(locked_order, latest_price):
        return {
            "success": True,
            "matched": False,
            "reason": "limit_price_not_triggered",
            "latest_price": latest_price,
            "order": locked_order,
        }

    account_result = await db.execute(
        select(Account).where(Account.account_id == locked_order.account_id).with_for_update()
    )
    account = account_result.scalar_one_or_none()
    if not account:
        return {"success": False, "matched": False, "reason": "account_not_found", "order": locked_order}

    position_result = await db.execute(
        select(Position)
        .where(
            Position.account_id == account.account_id,
            Position.stock_code == locked_order.stock_code,
        )
        .with_for_update()
    )
    position = position_result.scalar_one_or_none()
    position_dict = trading_service.engine.build_position_snapshot(position) if position else None
    shares = _order_remaining_shares(locked_order)
    if shares <= 0:
        return {"success": False, "matched": False, "reason": "empty_order", "order": locked_order}

    stop_loss_decimal = _extract_pending_order_stop_loss(locked_order)
    if locked_order.action == "buy":
        reserved_cash = _calculate_buy_reservation(locked_order.price, shares)
        current_frozen_cash = Decimal(str(account.frozen_cash or 0))
        released_cash = min(reserved_cash, current_frozen_cash)
        account.frozen_cash = current_frozen_cash - released_cash
        account.available_cash = Decimal(str(account.available_cash or 0)) + released_cash
    elif locked_order.action == "sell":
        reservation_result = await _validate_sell_limit_reservation(db, order=locked_order, position=position)
        if not reservation_result["success"]:
            locked_order.status = "rejected"
            locked_order.remark = reservation_result["message"]
            await db.commit()
            return {**reservation_result, "matched": False, "order": locked_order}

    trade_result = await trading_service.engine.execute_order(
        {
            "id": locked_order.order_id,
            "session_id": locked_order.session_id,
            "action": locked_order.action,
            "shares": shares,
            "price": latest_price,
            "order_type": "limit",
            "stock_code": locked_order.stock_code,
        },
        trading_service._build_account_dict(account),
        position_dict,
    )

    if not trade_result["success"]:
        locked_order.status = "rejected"
        locked_order.remark = trade_result["message"]
        await db.commit()
        return {"success": False, "matched": False, "message": trade_result["message"], "order": locked_order}

    account.available_cash = Decimal(str(trade_result["updated_account"]["cash_balance"]))
    account.total_assets = Decimal(str(trade_result["updated_account"]["total_assets"]))
    account.market_value = Decimal(str(trade_result["updated_account"]["market_value"]))
    account.total_profit_loss = Decimal(str(trade_result["updated_account"]["total_profit_loss"]))

    if locked_order.action == "buy":
        if position:
            position.total_shares = trade_result["updated_position"]["current_shares"]
            position.available_shares = trade_result["updated_position"]["available_shares"]
            position.frozen_shares = trade_result["updated_position"]["frozen_shares"]
            position.avg_cost = Decimal(str(trade_result["updated_position"]["avg_cost"]))
            position.current_price = Decimal(str(trade_result["trade_record"]["price"]))
            position.market_value = Decimal(str(trade_result["updated_position"]["market_value"]))
            position.profit_loss = Decimal(str(trade_result["updated_position"].get("unrealized_pnl", 0)))
            position.profit_loss_pct = (position.current_price - position.avg_cost) / position.avg_cost if position.avg_cost > 0 else 0
            position.purchase_details = _merge_purchase_details_with_stop_loss(
                trade_result["updated_position"]["purchase_details"],
                stop_loss_decimal,
            )
        else:
            db.add(
                Position(
                    account_id=account.account_id,
                    session_id=locked_order.session_id,
                    stock_code=locked_order.stock_code,
                    total_shares=trade_result["updated_position"]["current_shares"],
                    available_shares=trade_result["updated_position"]["available_shares"],
                    frozen_shares=trade_result["updated_position"]["frozen_shares"],
                    avg_cost=trade_result["updated_position"]["avg_cost"],
                    current_price=Decimal(str(trade_result["trade_record"]["price"])),
                    market_value=Decimal(str(trade_result["updated_position"]["market_value"])),
                    profit_loss=Decimal(str(trade_result["updated_position"].get("unrealized_pnl", 0))),
                    profit_loss_pct=Decimal("0.00"),
                    purchase_details=_merge_purchase_details_with_stop_loss(
                        trade_result["updated_position"]["purchase_details"],
                        stop_loss_decimal,
                    ),
                )
            )
    elif locked_order.action == "sell":
        if trade_result["updated_position"]:
            position.total_shares = trade_result["updated_position"]["current_shares"]
            position.available_shares = trade_result["updated_position"]["available_shares"]
            position.frozen_shares = trade_result["updated_position"]["frozen_shares"]
            position.current_price = Decimal(str(trade_result["trade_record"]["price"]))
            position.market_value = Decimal(str(trade_result["updated_position"]["market_value"]))
            position.profit_loss = Decimal(str(trade_result["updated_position"].get("unrealized_pnl", 0)))
            position.profit_loss_pct = (position.current_price - position.avg_cost) / position.avg_cost if position.avg_cost > 0 else 0
            existing_stop_loss = position.purchase_details.get("stop_loss") if isinstance(position.purchase_details, dict) else None
            position.purchase_details = _merge_purchase_details_with_stop_loss(
                trade_result["updated_position"]["purchase_details"],
                stop_loss_decimal if stop_loss_decimal is not None else (
                    Decimal(str(existing_stop_loss)) if existing_stop_loss not in (None, "") else None
                ),
            )
        elif position:
            await db.delete(position)
        realized_pnl = Decimal(str(trade_result["realized_pnl"]))
        account.total_trades = (account.total_trades or 0) + 1
        if realized_pnl > 0:
            current_wins = round((account.win_rate or 0) * (account.total_trades - 1) / 100)
            account.win_rate = (current_wins + 1) / account.total_trades * 100
        elif account.total_trades > 1:
            current_wins = round((account.win_rate or 0) * (account.total_trades - 1) / 100)
            account.win_rate = current_wins / account.total_trades * 100
        else:
            account.win_rate = 0 if realized_pnl <= 0 else 100
        starting_capital = account.initial_capital or account.total_assets
        if starting_capital and starting_capital > 0:
            account.profit_loss_pct = (account.total_profit_loss / starting_capital) * 100

    locked_order.status = trade_result["order_status"]
    locked_order.filled_shares = trade_result["executed_shares"]
    locked_order.avg_fill_price = Decimal(str(trade_result["trade_record"]["price"]))
    locked_order.realized_pnl = Decimal(str(trade_result["realized_pnl"]))
    locked_order.filled_at = datetime.now() if locked_order.status == "filled" else None
    trade_data = trade_result["trade_record"]
    db.add(
        TradeRecord(
            session_id=locked_order.session_id,
            account_id=account.account_id,
            order_id=locked_order.order_id,
            stock_code=locked_order.stock_code,
            action=locked_order.action,
            quantity=trade_data["shares"],
            fill_price=Decimal(str(trade_data["price"])),
            commission=Decimal(str(trade_data["commission"])),
            stamp_duty=Decimal(str(trade_data["stamp_duty"])),
            transfer_fee=Decimal(str(trade_data["transfer_fee"])),
            total_fees=Decimal(str(trade_data["total_fee"])),
            net_amount=Decimal(str(trade_data["net_amount"])),
            trade_time=datetime.now(),
        )
    )
    await db.flush()
    total_mv_result = await db.execute(
        select(func.sum(Position.market_value)).where(Position.account_id == account.account_id)
    )
    account.market_value = Decimal(str(total_mv_result.scalar() or 0))
    account.total_assets = account.available_cash + account.market_value
    await db.commit()
    return {"success": True, "matched": True, "order": locked_order, "trade_result": trade_result}


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """
    返回待成交挂单撮合任务定义。

    Returns:
        中央异步调度器可加载的任务快照。
    """
    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=run_pending_order_match_scan,
                task_name="Pending Order Match Scan",
                trigger_type="interval",
                job_id=PENDING_ORDER_MATCH_JOB_ID,
                trigger_args={"minutes": 1},
                coalesce=True,
                max_instances=1,
                misfire_grace_time=60,
            )
        ],
        disabled_job_ids=[],
    )


async def run_pending_order_match_scan() -> dict[str, Any]:
    """
    扫描并撮合交易时间内满足条件的待成交限价单。

    Returns:
        本次扫描统计结果。
    """
    try:
        if not is_trading_time():
            return {"success": True, "skipped": True, "reason": "not_trading_time", "scanned": 0, "matched": 0}

        async with database_module.AsyncSessionLocal() as db:
            pending_orders_result = await db.execute(
                select(Order.order_id)
                .where(
                    Order.status == "pending",
                    Order.order_type == "limit",
                )
                .order_by(Order.created_at.asc())
                .limit(PENDING_ORDER_MATCH_LIMIT)
            )
            pending_order_ids = list(pending_orders_result.scalars().all())

        matched = 0
        failed = 0
        for order_id in pending_order_ids:
            async with database_module.AsyncSessionLocal() as db:
                result = await _match_pending_order(db, order_id)
                if result.get("matched"):
                    matched += 1
                elif result.get("success") is False and result.get("reason") != "market_price_unavailable":
                    failed += 1

        result = {
            "success": True,
            "skipped": False,
            "scanned": len(pending_order_ids),
            "matched": matched,
            "failed": failed,
        }
        if result.get("matched"):
            logger.info("Pending order match scan completed", extra={"result": result})
        return result
    except Exception as exc:
        logger.exception("Pending order match scan failed", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}
