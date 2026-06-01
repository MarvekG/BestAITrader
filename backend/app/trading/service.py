from typing import Dict, Any
from uuid import UUID
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from app.core.logger import get_logger
from app.trading.trading_engine import TradingEngine
from app.models.account import Account
from app.models.position import Position
from app.models.order import Order
from app.models.trade_record import TradeRecord
from app.models.debate_message import DebateMessage
from app.websocket.manager import ws_manager
from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER

logger = get_logger(__name__)


def _extract_session_stop_loss(db: Session, session_id: UUID | None) -> Decimal | None:
    if not session_id:
        return None

    debate_message = db.query(DebateMessage).filter(
        DebateMessage.session_id == session_id,
        DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER
    ).order_by(DebateMessage.created_at.desc()).first()

    if not debate_message or not isinstance(debate_message.analysis, dict):
        return None

    stop_loss = debate_message.analysis.get("stop_loss")
    if stop_loss in (None, ""):
        return None

    try:
        return Decimal(str(stop_loss))
    except Exception:
        logger.warning("Invalid stop_loss in PM decision for session %s: %s", session_id, stop_loss)
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


class TradingService:
    def __init__(self):
        self.engine = TradingEngine()

    async def execute_order_and_update_db(
        self,
        db: Session,
        session_id: UUID,
        account: Account,
        stock_code: str,
        action: str,
        shares: int,
        price: float,
        order_type: str = "market",
        stop_loss: float | None = None,
    ) -> Dict[str, Any]:
        """
        执行订单并同步更新数据库中的账户和持仓信息
        Execute order and sync update Account and Position in DB
        """
        locked_account = db.query(Account).filter(
            Account.account_id == account.account_id
        ).with_for_update().first()
        if not locked_account:
            raise ValueError(f"Account {account.account_id} not found during trade execution")

        # 1. 构造初始订单记录
        order = Order(
            session_id=session_id,
            account_id=locked_account.account_id,
            stock_code=stock_code,
            action=action,
            order_type=order_type,
            price=price,
            shares=shares,
            status="pending",
            # 记录订单来源：AI自动交易包含session_id，否则为手动下单
            # Record order source: AI auto-trade includes session_id, otherwise manual
            source=f"ai:{session_id}" if session_id else "manual"
        )
        db.add(order)
        db.flush()
        db.refresh(order)

        # 2. 准备交易引擎所需的字典数据
        account_dict = {
            "cash_balance": float(locked_account.available_cash),
            "total_assets": float(locked_account.total_assets),
            "market_value": float(locked_account.market_value),
            "total_profit_loss": float(locked_account.total_profit_loss or 0.0)
        }

        position = db.query(Position).filter(
            Position.account_id == locked_account.account_id,
            Position.stock_code == stock_code
        ).with_for_update().first()
        session_stop_loss = _normalize_stop_loss_value(stop_loss, session_id=session_id)
        if session_stop_loss is None:
            session_stop_loss = _extract_session_stop_loss(db, session_id)

        position_dict = self.engine.build_position_snapshot(position) if position else None

        order_params = {
            "id": order.order_id,
            "session_id": session_id,
            "action": action,
            "shares": shares,
            "price": price,
            "order_type": order_type,
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
                        session_stop_loss,
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
                            session_stop_loss,
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
                        session_stop_loss if session_stop_loss is not None else (
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
                    db.delete(position)

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
            db.flush()
            db.refresh(db_trade_record)
            trade_result["trade_record"]["id"] = db_trade_record.trade_id
            trade_result["trade_record"]["order_id"] = order.order_id

            # 5. [FEATURE] 实时同步账户全局资产，防止市值对不上
            from sqlalchemy import func
            total_mv = db.query(func.sum(Position.market_value)).filter(
                Position.account_id == locked_account.account_id).scalar() or 0
            locked_account.market_value = Decimal(str(total_mv))
            locked_account.total_assets = locked_account.available_cash + locked_account.market_value
            db.commit()

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
            updated_pos = db.query(Position).filter(
                Position.account_id == account.account_id,
                Position.stock_code == stock_code
            ).first()
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

            return {"success": True, "order": order, "trade_result": trade_result}
        else:
            logger.warning(f"❌ [TradingService] Order execution failed: {trade_result['message']}")
            order.status = "rejected"
            order.remark = trade_result["message"]
            db.commit()

            # 推送拒绝通知
            await ws_manager.send_order_status(str(session_id), {
                "order_id": str(order.order_id),
                "status": "rejected",
                "stock_code": stock_code,
                "message": trade_result["message"],
                "updated_at": datetime.now().isoformat()
            })

            return {"success": False, "message": trade_result["message"], "order": order}


trading_service = TradingService()
