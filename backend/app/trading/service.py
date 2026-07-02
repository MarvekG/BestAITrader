import json
from typing import Dict, Any
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import database as database_module
from app.core.database import AsyncSessionLocal as async_session_factory
from app.core.logger import get_logger
from app.trading.trading_engine import TradingEngine
from app.models.account import Account
from app.models.position import Position
from app.models.order import Order
from app.models.trade_record import TradeRecord
from app.websocket.manager import ws_manager
from app.models.pm_decision import PMDecisionRecord
from app.data.market_utils import is_trading_time
from app.data.storage import data_storage_service
from app.risk_control.service import portfolio_risk_control_service

logger = get_logger(__name__)


async def _extract_session_stop_loss(db: AsyncSession, session_id: UUID | None) -> Decimal | None:
    if not session_id:
        return None

    result = await db.execute(select(PMDecisionRecord).where(PMDecisionRecord.session_id == session_id))
    pm_decision = result.scalar_one_or_none()
    if not pm_decision or pm_decision.stop_loss in (None, ""):
        return None

    try:
        return Decimal(str(pm_decision.stop_loss))
    except Exception:
        logger.warning("Invalid stop_loss in PM decision for session %s: %s", session_id, pm_decision.stop_loss)
        return None


def _normalize_stop_loss_value(stop_loss: Any, *, session_id: UUID | None = None) -> Decimal | None:
    if stop_loss in (None, ""):
        return None

    try:
        return Decimal(str(stop_loss))
    except Exception:
        logger.warning("Invalid explicit stop_loss for session %s: %s", session_id, stop_loss)
        return None


def _merge_purchase_details_with_stop_loss(purchase_details: Any, stop_loss: Decimal | None) -> Dict[str, Any]:
    details = purchase_details.copy() if isinstance(purchase_details, dict) else {}
    if stop_loss is not None:
        details["stop_loss"] = float(stop_loss)
    return details


def _empty_risk_result() -> dict[str, Any]:
    """
    构建无需再次组合风控时的空风控结果。

    Returns:
        空风控结果字典。
    """
    return {
        "enabled": False,
        "passed": True,
        "severity": "none",
        "accepted": [],
        "blocks": [],
        "metrics": {},
    }


def _order_remaining_shares(order: Order) -> int:
    """
    计算订单尚未成交的股数。

    Args:
        order: 订单模型。

    Returns:
        未成交股数，最小为 0。
    """
    return max(int(order.shares or 0) - int(order.filled_shares or 0), 0)


def _calculate_buy_reservation(engine: TradingEngine, price: float | Decimal, shares: int) -> Decimal:
    """
    计算买入限价挂单需要冻结的现金。

    Args:
        engine: 交易引擎实例。
        price: 委托价格。
        shares: 委托股数。

    Returns:
        需要冻结的现金金额。
    """
    price_decimal = Decimal(str(price))
    turnover = price_decimal * Decimal(str(shares))
    fee = engine.calculate_fee(float(price_decimal), shares, True)["total_fee"]
    return turnover + Decimal(str(fee))


def _build_pending_order_remark(stop_loss: Decimal | None) -> str | None:
    """
    将挂单成交后需要恢复的轻量字段编码到订单备注。

    Args:
        stop_loss: 买入止损价。

    Returns:
        JSON 字符串备注；无内部字段时返回 None。
    """
    if stop_loss is None:
        return None
    return json.dumps({"stop_loss": float(stop_loss)}, ensure_ascii=False)


def _extract_pending_order_stop_loss(order: Order) -> Decimal | None:
    """
    从待成交订单备注中恢复买入止损价。

    Args:
        order: 订单模型。

    Returns:
        止损价；不存在或格式非法时返回 None。
    """
    if not order.remark:
        return None
    try:
        payload = json.loads(order.remark)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("stop_loss") in (None, ""):
        return None
    try:
        return Decimal(str(payload["stop_loss"]))
    except Exception:
        return None


def _limit_order_triggered(order: Order, latest_price: float) -> bool:
    """
    判断限价挂单是否被最新价触发。

    Args:
        order: 待成交限价单。
        latest_price: 最新行情价。

    Returns:
        满足成交条件时返回 True。
    """
    limit_price = float(order.price or 0)
    if order.action == "buy":
        return latest_price <= limit_price
    if order.action == "sell":
        return latest_price >= limit_price
    return False


async def _resolve_order_price(stock_code: str, order_type: str, price: float) -> float | None:
    """
    解析最终用于风控和交易执行的订单价格。

    Args:
        stock_code: 股票代码。
        order_type: 订单类型。
        price: 请求价格。

    Returns:
        可执行价格；市价单行情不可用时返回 None。
    """
    if order_type != "market":
        return float(price)

    try:
        realtime_data = await data_storage_service.get_stock_realtime_market(stock_code)
        latest_price = realtime_data.get("latest_price") if realtime_data else None
        if latest_price:
            return float(latest_price)
    except Exception as exc:
        logger.error("Failed to obtain market price for stock", extra={"stock_code": stock_code, "error": str(exc)})
    return None


class TradingService:
    def __init__(self):
        self.engine = TradingEngine()

    def _build_account_dict(self, account: Account) -> dict[str, float]:
        """
        将账户模型转换为交易引擎使用的账户快照。

        Args:
            account: 已锁定的账户模型。

        Returns:
            交易引擎账户快照。
        """
        return {
            "cash_balance": float(account.available_cash),
            "total_assets": float(account.total_assets),
            "market_value": float(account.market_value),
            "total_profit_loss": float(account.total_profit_loss or 0.0)
        }

    async def _get_pending_sell_reserved_shares(
        self,
        db: AsyncSession,
        account_id: UUID,
        stock_code: str,
        exclude_order_id: UUID | None = None,
    ) -> int:
        """
        计算同账户同股票待成交卖单已经占用的股数。

        Args:
            db: 数据库会话。
            account_id: 账户 ID。
            stock_code: 股票代码。
            exclude_order_id: 需要排除的订单 ID。

        Returns:
            已占用的待卖股数。
        """
        stmt = select(Order).where(
            Order.account_id == account_id,
            Order.stock_code == stock_code,
            Order.action == "sell",
            Order.order_type == "limit",
            Order.status == "pending",
        )
        if exclude_order_id is not None:
            stmt = stmt.where(Order.order_id != exclude_order_id)
        result = await db.execute(stmt)
        return sum(_order_remaining_shares(order) for order in result.scalars().all())

    async def _validate_sell_limit_reservation(
        self,
        db: AsyncSession,
        account_id: UUID,
        stock_code: str,
        shares: int,
        position: Position | None,
        exclude_order_id: UUID | None = None,
    ) -> dict[str, Any]:
        """
        校验新增卖出挂单是否会超过当前可卖股数。

        Args:
            db: 数据库会话。
            account_id: 账户 ID。
            stock_code: 股票代码。
            shares: 本次挂单股数。
            position: 当前持仓模型。
            exclude_order_id: 修改或撮合时需要排除的订单 ID。

        Returns:
            校验结果字典。
        """
        position_snapshot = self.engine.build_position_snapshot(position) if position else None
        executable_shares = self.engine.get_executable_sell_shares(position_snapshot)
        reserved_shares = await self._get_pending_sell_reserved_shares(
            db,
            account_id,
            stock_code,
            exclude_order_id=exclude_order_id,
        )
        available_after_reservation = max(executable_shares - reserved_shares, 0)
        if shares > available_after_reservation:
            return {
                "success": False,
                "message": "Insufficient available shares after pending sell orders",
                "reason": "insufficient_available_shares_after_pending_orders",
                "available_shares": executable_shares,
                "reserved_shares": reserved_shares,
            }
        return {"success": True, "available_shares": executable_shares, "reserved_shares": reserved_shares}

    async def _send_pending_order_status(self, order: Order, message: str) -> None:
        """
        推送待成交订单状态事件。

        Args:
            order: 订单模型。
            message: 状态说明。
        """
        await ws_manager.send_order_status(str(order.session_id), {
            "order_id": str(order.order_id),
            "status": order.status,
            "stock_code": order.stock_code,
            "action": order.action,
            "price": float(order.price),
            "shares": order.shares,
            "filled_shares": order.filled_shares,
            "message": message,
            "updated_at": datetime.now().isoformat(),
        })

    async def _create_pending_limit_order(
        self,
        db: AsyncSession,
        *,
        session_id: UUID | None,
        account: Account,
        stock_code: str,
        action: str,
        shares: int,
        price: float,
        stop_loss: Decimal | None,
        position: Position | None,
    ) -> dict[str, Any]:
        """
        创建待成交限价挂单并处理资源占用。

        Args:
            db: 数据库会话。
            session_id: 关联投研会话 ID。
            account: 已锁定账户。
            stock_code: 股票代码。
            action: 买卖方向。
            shares: 委托股数。
            price: 委托价格。
            stop_loss: 买入止损价。
            position: 当前持仓。

        Returns:
            挂单创建结果。
        """
        validity = self.engine.check_order_validity(
            {"action": action, "shares": shares, "price": price, "order_type": "limit", "stock_code": stock_code},
            self._build_account_dict(account),
            self.engine.build_position_snapshot(position) if position else None,
        )
        if not validity["is_valid"]:
            return {"success": False, "message": validity["message"], "reason": "order_invalid"}

        if action == "sell":
            reservation_result = await self._validate_sell_limit_reservation(
                db,
                account.account_id,
                stock_code,
                shares,
                position,
            )
            if not reservation_result["success"]:
                return reservation_result

        reserved_cash = Decimal("0")
        if action == "buy":
            reserved_cash = _calculate_buy_reservation(self.engine, price, shares)
            if account.available_cash < reserved_cash:
                return {"success": False, "message": "Insufficient funds", "reason": "insufficient_funds"}
            account.available_cash -= reserved_cash
            account.frozen_cash = Decimal(str(account.frozen_cash or 0)) + reserved_cash

        order = Order(
            session_id=session_id,
            account_id=account.account_id,
            stock_code=stock_code,
            action=action,
            order_type="limit",
            price=Decimal(str(price)),
            shares=shares,
            status="pending",
            filled_shares=0,
            source=f"ai:{session_id}" if session_id else "manual",
            remark=_build_pending_order_remark(stop_loss),
        )
        db.add(order)
        await db.flush()
        await db.refresh(order)
        await db.commit()
        return {
            "success": True,
            "message": "Limit order accepted and pending match",
            "reason": "limit_order_pending",
            "order": order,
            "status": "pending",
            "reserved_cash": float(reserved_cash),
        }

    def _release_buy_order_reservation(self, account: Account, order: Order) -> Decimal:
        """
        释放买入限价挂单冻结现金。

        Args:
            account: 已锁定账户。
            order: 待释放订单。

        Returns:
            实际释放的现金金额。
        """
        if order.action != "buy" or order.order_type != "limit":
            return Decimal("0")
        reserved_cash = _calculate_buy_reservation(self.engine, order.price, _order_remaining_shares(order))
        current_frozen_cash = Decimal(str(account.frozen_cash or 0))
        released_cash = min(reserved_cash, current_frozen_cash)
        account.frozen_cash = current_frozen_cash - released_cash
        account.available_cash = Decimal(str(account.available_cash or 0)) + released_cash
        return released_cash

    async def cancel_order(self, order_id: UUID, user_id: int | None = None) -> dict[str, Any]:
        """
        撤销待成交订单并释放资源占用。

        Args:
            order_id: 待撤销订单 ID。
            user_id: 当前用户 ID，用于校验订单归属；后台内部调用可为空。

        Returns:
            撤单结果。
        """
        async with async_session_factory() as db:
            order_stmt = select(Order).where(Order.order_id == order_id).with_for_update()
            if user_id is not None:
                order_stmt = order_stmt.join(Account, Order.account_id == Account.account_id).where(Account.user_id == user_id)
            order_result = await db.execute(order_stmt)
            order = order_result.scalar_one_or_none()
            if not order:
                raise ValueError(f"Order {order_id} not found during order cancellation")
            if order.status != "pending":
                return {
                    "success": False,
                    "message": f"Order cannot be cancelled, current status: {order.status}",
                    "order": order,
                }

            account_result = await db.execute(
                select(Account).where(Account.account_id == order.account_id).with_for_update()
            )
            account = account_result.scalar_one_or_none()
            if not account:
                raise ValueError(f"Account {order.account_id} not found during order cancellation")

            released_cash = self._release_buy_order_reservation(account, order)
            order.status = "cancelled"
            order.remark = "cancelled"
            await db.commit()
            await db.refresh(order)
        await self._send_pending_order_status(order, "Order cancelled successfully")
        return {
            "success": True,
            "message": "Order cancelled successfully",
            "order": order,
            "released_cash": float(released_cash),
        }

    async def update_order(self, order_id: UUID, update_data: dict[str, Any]) -> Order:
        """更新待处理订单字段。

        Args:
            order_id: 订单 ID。
            update_data: 需要更新的字段和值。

        Returns:
            更新后的订单。

        Raises:
            ValueError: 订单不存在时抛出。
        """
        async with async_session_factory() as db:
            result = await db.execute(select(Order).where(Order.order_id == order_id).with_for_update())
            order = result.scalar_one_or_none()
            if order is None:
                raise ValueError("Order not found")
            for field, value in update_data.items():
                setattr(order, field, value)
            await db.commit()
            await db.refresh(order)
            return order

    async def match_pending_order(self, order: Order) -> dict[str, Any]:
        """
        尝试按最新价撮合单笔待成交限价单。

        Args:
            db: 数据库会话。
            order: 待撮合订单。

        Returns:
            撮合结果；价格未触发时返回 `matched=False`。
        """
        async with async_session_factory() as db:
            order_result = await db.execute(select(Order).where(Order.order_id == order.order_id))
            current_order = order_result.scalar_one_or_none()
            if not current_order:
                return {"success": False, "matched": False, "reason": "order_not_found"}
            if current_order.status != "pending" or current_order.order_type != "limit":
                return {"success": False, "matched": False, "reason": "order_not_matchable", "order": current_order}

            latest_price = await _resolve_order_price(current_order.stock_code, "market", 0.0)
            if latest_price is None or latest_price <= 0:
                return {"success": False, "matched": False, "reason": "market_price_unavailable", "order": current_order}
            if not _limit_order_triggered(current_order, latest_price):
                return {
                    "success": True,
                    "matched": False,
                    "reason": "limit_price_not_triggered",
                    "latest_price": latest_price,
                    "order": current_order,
                }

            account_result = await db.execute(select(Account).where(Account.account_id == current_order.account_id))
            account = account_result.scalar_one_or_none()
            if not account:
                return {"success": False, "matched": False, "reason": "account_not_found", "order": current_order}

            order_snapshot = current_order
        result = await self.execute_order_and_update_db(
            session_id=order_snapshot.session_id,
            account_id=account.account_id,
            stock_code=order_snapshot.stock_code,
            action=order_snapshot.action,
            shares=_order_remaining_shares(order_snapshot),
            price=float(order_snapshot.price),
            order_type="limit",
            existing_order=order_snapshot,
            execution_price=latest_price,
        )
        result["matched"] = bool(result.get("success"))
        result["latest_price"] = latest_price
        return result

    async def match_pending_orders(self, limit: int = 200) -> dict[str, Any]:
        """
        在交易时间内扫描并撮合待成交限价单。

        Args:
            db: 数据库会话。
            limit: 单次最多扫描订单数量。

        Returns:
            扫描统计结果。
        """
        if not is_trading_time():
            return {"success": True, "skipped": True, "reason": "not_trading_time", "scanned": 0, "matched": 0}

        async with async_session_factory() as db:
            pending_orders_result = await db.execute(
                select(Order)
                .where(Order.status == "pending", Order.order_type == "limit")
                .order_by(Order.created_at.asc())
                .limit(limit)
            )
            pending_orders = pending_orders_result.scalars().all()

        matched = 0
        failed = 0
        for order in pending_orders:
            result = await self.match_pending_order(order)
            if result.get("matched"):
                matched += 1
            elif result.get("success") is False and result.get("reason") != "market_price_unavailable":
                failed += 1

        return {
            "success": True,
            "skipped": False,
            "scanned": len(pending_orders),
            "matched": matched,
            "failed": failed,
        }

    async def execute_order_and_update_db(
        self,
        session_id: UUID | None,
        stock_code: str,
        action: str,
        shares: int,
        price: float,
        order_type: str = "market",
        stop_loss: float | None = None,
        existing_order: Order | None = None,
        execution_price: float | None = None,
        account_id: UUID | None = None,
        user_id: int | None = None,
    ) -> Dict[str, Any]:
        """
        在账户和持仓锁内完成下单、撮合执行和数据库同步。

        Args:
            session_id: 关联的投研会话 ID；手动下单可为空。
            stock_code: 股票代码。
            action: 交易方向。
            shares: 交易股数。
            price: 请求价格。
            order_type: 订单类型。
            stop_loss: 买入止损价。
            existing_order: 后台撮合时传入的既有待成交订单。
            execution_price: 后台撮合时使用的最新成交价。
            account_id: 当前账户 ID。
            user_id: 当前用户 ID，未传账户 ID 时用于定位账户。

        Returns:
            下单或交易执行结果；风控阻断时包含 `reason=risk_control_blocked` 和风控详情。

        Raises:
            ValueError: 加锁后无法找到目标账户时抛出。
        """
        async with async_session_factory() as db:
            should_notify = existing_order is None
            if existing_order is not None:
                locked_order_result = await db.execute(
                    select(Order).where(Order.order_id == existing_order.order_id).with_for_update()
                )
                existing_order = locked_order_result.scalar_one_or_none()
                if existing_order is None:
                    return {"success": False, "message": "Order not found", "reason": "order_not_found"}
                target_account_id = existing_order.account_id
            elif account_id is not None:
                target_account_id = account_id
            elif user_id is not None:
                account_lookup = await db.execute(select(Account.account_id).where(Account.user_id == user_id))
                target_account_id = account_lookup.scalar_one_or_none()
            else:
                target_account_id = None
            if target_account_id is None:
                raise ValueError("Account id is required during trade execution")
            locked_account_result = await db.execute(
                select(Account).where(Account.account_id == target_account_id).with_for_update()
            )
            locked_account = locked_account_result.scalar_one_or_none()
            if not locked_account:
                raise ValueError(f"Account {target_account_id} not found during trade execution")

            if existing_order is not None:
                if existing_order.status != "pending":
                    return {
                        "success": False,
                        "message": f"Order cannot be matched, current status: {existing_order.status}",
                        "reason": "order_not_pending",
                        "order": existing_order,
                    }
                session_id = existing_order.session_id
                stock_code = existing_order.stock_code
                action = existing_order.action
                shares = _order_remaining_shares(existing_order)
                price = float(execution_price or 0)
                order_type = existing_order.order_type
                stop_loss_decimal = _extract_pending_order_stop_loss(existing_order)
            else:
                stop_loss_decimal = _normalize_stop_loss_value(stop_loss, session_id=session_id)
                if stop_loss_decimal is None:
                    stop_loss_decimal = await _extract_session_stop_loss(db, session_id)

            if shares <= 0:
                return {"success": False, "message": "No remaining shares to execute", "reason": "empty_order"}

            position_result = await db.execute(
                select(Position)
                .where(
                    Position.account_id == locked_account.account_id,
                    Position.stock_code == stock_code,
                )
                .with_for_update()
            )
            position = position_result.scalar_one_or_none()
            position_dict = self.engine.build_position_snapshot(position) if position else None
            if existing_order is None and order_type == "market" and not is_trading_time():
                return {
                    "success": False,
                    "message": "Market orders are only allowed during trading time",
                    "reason": "market_order_not_allowed_outside_trading_time",
                }

            if existing_order is not None:
                resolved_price = float(execution_price or 0)
            else:
                resolved_price = await _resolve_order_price(stock_code, order_type, price)
            if resolved_price is None or resolved_price <= 0:
                return {
                    "success": False,
                    "message": "Market price unavailable, cannot execute market order",
                    "reason": "market_price_unavailable",
                }
            if action == "buy" and stop_loss_decimal is not None and stop_loss_decimal >= Decimal(str(resolved_price)):
                return {
                    "success": False,
                    "message": "Buy stop_loss must be below the order price",
                    "reason": "invalid_buy_stop_loss",
                    "stop_loss": float(stop_loss_decimal),
                    "price": resolved_price,
                }
            if existing_order is not None and action == "buy":
                self._release_buy_order_reservation(locked_account, existing_order)

            if existing_order is None:
                estimated_fee = self.engine.calculate_fee(resolved_price, shares, action == "buy")["total_fee"]
                risk_result = await portfolio_risk_control_service.evaluate_order(
                    user_id=locked_account.user_id,
                    stock_code=stock_code,
                    action=action,
                    shares=shares,
                    price=resolved_price,
                    order_type=order_type,
                    stop_loss=float(stop_loss_decimal) if stop_loss_decimal is not None else None,
                    estimated_fee=float(estimated_fee),
                )
            else:
                risk_result = _empty_risk_result()
            if risk_result["blocks"]:
                return {
                    "success": False,
                    "message": "Order blocked by portfolio risk control",
                    "reason": "risk_control_blocked",
                    "risk_control": risk_result,
                }

            if existing_order is None and order_type == "limit":
                pending_result = await self._create_pending_limit_order(
                    db,
                    session_id=session_id,
                    account=locked_account,
                    stock_code=stock_code,
                    action=action,
                    shares=shares,
                    price=price,
                    stop_loss=stop_loss_decimal,
                    position=position,
                )
                if pending_result["success"]:
                    await self._send_pending_order_status(pending_result["order"], pending_result["message"])
                    pending_result["risk_control"] = risk_result
                return pending_result

            if existing_order is not None and action == "sell":
                reservation_result = await self._validate_sell_limit_reservation(
                    db,
                    locked_account.account_id,
                    stock_code,
                    shares,
                    position,
                    exclude_order_id=existing_order.order_id,
                )
                if not reservation_result["success"]:
                    existing_order.status = "rejected"
                    existing_order.remark = reservation_result["message"]
                    await db.commit()
                    return {**reservation_result, "order": existing_order}

            # 2. 准备交易引擎所需的字典数据
            account_dict = self._build_account_dict(locked_account)

            # 1. 构造初始订单记录
            if existing_order is None:
                order = Order(
                    session_id=session_id,
                    account_id=locked_account.account_id,
                    stock_code=stock_code,
                    action=action,
                    order_type=order_type,
                    price=resolved_price if order_type == "market" else price,
                    shares=shares,
                    status="pending",
                    # 记录订单来源：AI自动交易包含session_id，否则为手动下单
                    # Record order source: AI auto-trade includes session_id, otherwise manual
                    source=f"ai:{session_id}" if session_id else "manual"
                )
                db.add(order)
                await db.flush()
                await db.refresh(order)
            else:
                order = existing_order

            order_params = {
                "id": order.order_id,
                "session_id": session_id,
                "action": action,
                "shares": shares,
                "price": resolved_price,
                "order_type": "limit",
                "stock_code": stock_code
            }

            # 3. 调用交易引擎执行撮合
            trade_result = await self.engine.execute_order(order_params, account_dict, position_dict)

            if trade_result["success"]:
                logger.info(f"✅ [TradingService] Order executed successfully: {trade_result['message']}")
                deleted_position_event = None

                # 4. 持久化变更到数据库
                locked_account.available_cash = Decimal(str(trade_result["updated_account"]["cash_balance"]))
                locked_account.total_assets = Decimal(str(trade_result["updated_account"]["total_assets"]))
                locked_account.market_value = Decimal(str(trade_result["updated_account"]["market_value"]))
                locked_account.total_profit_loss = Decimal(str(trade_result["updated_account"]["total_profit_loss"]))

                if action == "buy":
                    if position:
                        position.total_shares = trade_result["updated_position"]["current_shares"]
                        position.available_shares = trade_result["updated_position"]["available_shares"]
                        position.frozen_shares = trade_result["updated_position"]["frozen_shares"]
                        position.avg_cost = Decimal(str(trade_result["updated_position"]["avg_cost"]))
                        position.current_price = Decimal(str(trade_result["trade_record"]["price"]))
                        position.market_value = Decimal(str(trade_result["updated_position"]["market_value"]))
                        position.profit_loss = Decimal(str(trade_result["updated_position"].get("unrealized_pnl", 0)))
                        # 计算盈亏比例 (Calculate profit/loss percentage)
                        if position.avg_cost > 0:
                            position.profit_loss_pct = (position.current_price - position.avg_cost) / position.avg_cost
                        else:
                            position.profit_loss_pct = 0
                        position.purchase_details = _merge_purchase_details_with_stop_loss(
                            trade_result["updated_position"]["purchase_details"],
                            stop_loss_decimal,
                        )
                    else:
                        new_pos = Position(
                            account_id=locked_account.account_id,
                            session_id=session_id,
                            stock_code=stock_code,
                            total_shares=trade_result["updated_position"]["current_shares"],
                            available_shares=trade_result["updated_position"]["available_shares"],
                            frozen_shares=trade_result["updated_position"]["frozen_shares"],
                            avg_cost=trade_result["updated_position"]["avg_cost"],
                            current_price=Decimal(str(trade_result["trade_record"]["price"])),
                            market_value=Decimal(str(trade_result["updated_position"]["market_value"])),
                            profit_loss=Decimal(str(trade_result["updated_position"].get("unrealized_pnl", 0))),
                            profit_loss_pct=Decimal("0.00"),  # 初始买入，成本即价格，盈亏率为0
                            purchase_details=_merge_purchase_details_with_stop_loss(
                                trade_result["updated_position"]["purchase_details"],
                                stop_loss_decimal,
                            )
                        )
                        db.add(new_pos)
                elif action == "sell":
                    if trade_result["updated_position"]:
                        position.total_shares = trade_result["updated_position"]["current_shares"]
                        position.available_shares = trade_result["updated_position"]["available_shares"]
                        position.frozen_shares = trade_result["updated_position"]["frozen_shares"]
                        position.current_price = Decimal(str(trade_result["trade_record"]["price"]))
                        position.market_value = Decimal(str(trade_result["updated_position"]["market_value"]))
                        position.profit_loss = Decimal(str(trade_result["updated_position"].get("unrealized_pnl", 0)))
                        # 计算盈亏比例
                        if position.avg_cost > 0:
                            position.profit_loss_pct = (position.current_price - position.avg_cost) / position.avg_cost
                        else:
                            position.profit_loss_pct = 0
                        existing_stop_loss = None
                        if isinstance(position.purchase_details, dict):
                            existing_stop_loss = position.purchase_details.get("stop_loss")
                        position.purchase_details = _merge_purchase_details_with_stop_loss(
                            trade_result["updated_position"]["purchase_details"],
                            stop_loss_decimal if stop_loss_decimal is not None else (
                                Decimal(str(existing_stop_loss)) if existing_stop_loss not in (None, "") else None
                            ),
                        )
                    else:
                        deleted_position_event = {
                            "position_id": str(position.position_id),
                            "stock_code": position.stock_code,
                            "current_shares": 0,
                            "available_shares": 0,
                            "frozen_shares": 0,
                            "avg_cost": 0.0,
                            "market_value": 0.0,
                            "unrealized_pnl": 0.0,
                            "removed": True,
                            "updated_at": datetime.now().isoformat(),
                        }
                        await db.delete(position)

                    # 更新账户统计信息 (Update account statistics)
                    realized_pnl = Decimal(str(trade_result["realized_pnl"]))
                    locked_account.total_trades = (locked_account.total_trades or 0) + 1

                    # 如果是盈利交易，更新胜率 (If profitable, update win rate)
                    if realized_pnl > 0:
                        current_wins = round((locked_account.win_rate or 0) * (locked_account.total_trades - 1) / 100)
                        locked_account.win_rate = (current_wins + 1) / locked_account.total_trades * 100
                    elif locked_account.total_trades > 1:
                        current_wins = round((locked_account.win_rate or 0) * (locked_account.total_trades - 1) / 100)
                        locked_account.win_rate = current_wins / locked_account.total_trades * 100
                    else:
                        locked_account.win_rate = 0 if realized_pnl <= 0 else 100

                    # 更新盈亏比 (Update P/L ratio)
                    # Formula: ((realized_pnl + floating_pnl) / initial_capital) * 100
                    starting_capital = locked_account.initial_capital or locked_account.total_assets
                    if starting_capital and starting_capital > 0:
                        # Note: account.market_value and positions are not fully updated in this object yet,
                        # but we can estimate or wait for the flush below.
                        # For accuracy, we use the total_profit_loss which is updated.
                        # We'll also need floating_pnl. Since this is complex here,
                        # we'll rely on the /my-assets dynamic calculation for the UI.
                        # But we update the persisted pct as a fallback.
                        locked_account.profit_loss_pct = (locked_account.total_profit_loss / starting_capital) * 100

                order.status = trade_result["order_status"]
                if order.order_type == "market":
                    order.price = Decimal(str(trade_result["trade_record"]["price"]))
                order.filled_shares = trade_result["executed_shares"]
                order.avg_fill_price = Decimal(str(trade_result["trade_record"]["price"]))
                order.realized_pnl = Decimal(str(trade_result["realized_pnl"]))
                order.filled_at = datetime.now() if order.status == "filled" else None

                # 4.1 持久化交易记录
                trade_data = trade_result["trade_record"]
                db_trade_record = TradeRecord(
                    session_id=session_id,
                    account_id=locked_account.account_id,
                    order_id=order.order_id,
                    stock_code=stock_code,
                    action=action,
                    quantity=trade_data["shares"],
                    fill_price=Decimal(str(trade_data["price"])),
                    commission=Decimal(str(trade_data["commission"])),
                    stamp_duty=Decimal(str(trade_data["stamp_duty"])),
                    transfer_fee=Decimal(str(trade_data["transfer_fee"])),
                    total_fees=Decimal(str(trade_data["total_fee"])),
                    net_amount=Decimal(str(trade_data["net_amount"])),
                    trade_time=datetime.now()
                )
                db.add(db_trade_record)
                await db.flush()
                await db.refresh(db_trade_record)
                trade_result["trade_record"]["id"] = db_trade_record.trade_id
                trade_result["trade_record"]["order_id"] = order.order_id

                # 5. [FEATURE] 实时同步账户全局资产，防止市值对不上
                total_mv_result = await db.execute(
                    select(func.sum(Position.market_value)).where(Position.account_id == locked_account.account_id)
                )
                total_mv = total_mv_result.scalar() or 0
                locked_account.market_value = Decimal(str(total_mv))
                locked_account.total_assets = locked_account.available_cash + locked_account.market_value
                await db.commit()

                if should_notify:
                    # 6. 推送 WebSocket 通知
                    # Order status
                    await ws_manager.send_order_status(str(session_id), {
                        "order_id": str(order.order_id),
                        "status": order.status,
                        "stock_code": stock_code,
                        "action": action,
                        "price": float(trade_result["trade_record"]["price"]),
                        "shares": shares,
                        "filled_shares": trade_result["executed_shares"],
                        "message": trade_result["message"],
                        "updated_at": datetime.now().isoformat()
                    })

                    # Position update
                    updated_pos_result = await db.execute(
                        select(Position).where(
                            Position.account_id == locked_account.account_id,
                            Position.stock_code == stock_code,
                        )
                    )
                    updated_pos = updated_pos_result.scalar_one_or_none()
                    if updated_pos:
                        await ws_manager.send_position_update(str(session_id), {
                            "position_id": str(updated_pos.position_id),
                            "stock_code": updated_pos.stock_code,
                            "current_shares": updated_pos.total_shares,
                            "available_shares": updated_pos.available_shares,
                            "frozen_shares": updated_pos.frozen_shares,
                            "avg_cost": float(updated_pos.avg_cost),
                            "market_value": float(updated_pos.market_value or 0.0),
                            "unrealized_pnl": float(updated_pos.profit_loss or 0.0),
                            "updated_at": updated_pos.updated_at.isoformat()
                        })
                    elif deleted_position_event:
                        await ws_manager.send_position_update(str(session_id), deleted_position_event)

                    # Trade execution notification
                    await ws_manager.send_trade_executed(str(session_id), {
                        "trade_id": str(trade_result["trade_record"]["id"]),
                        "order_id": str(order.order_id),
                        "stock_code": stock_code,
                        "action": action,
                        "price": float(trade_result["trade_record"]["price"]),
                        "shares": trade_result["executed_shares"],
                        "turnover": float(trade_result["trade_record"]["turnover"]),
                        "commission": float(trade_result["trade_record"]["commission"]),
                        "total_fee": float(trade_result["trade_record"]["total_fee"]),
                        "trade_time": datetime.now().isoformat()
                    })

                return {"success": True, "order": order, "trade_result": trade_result, "risk_control": risk_result}
            else:
                logger.warning(f"❌ [TradingService] Order execution failed: {trade_result['message']}")
                order.status = "rejected"
                order.remark = trade_result["message"]
                await db.commit()

                # 推送拒绝通知
                if should_notify:
                    await ws_manager.send_order_status(str(session_id), {
                        "order_id": str(order.order_id),
                        "status": "rejected",
                        "stock_code": stock_code,
                        "message": trade_result["message"],
                        "updated_at": datetime.now().isoformat()
                    })

                return {"success": False, "message": trade_result["message"], "order": order}


trading_service = TradingService()
