from app.models.data_storage import StockBasic
from app.core.utils.formatters import StockCodeStandardizer
from app.data.storage import data_storage_service  # Added for stock name lookup
from app.websocket.manager import ws_manager
from app.trading.trading_engine import TradingEngine
from app.api.ownership import (
    get_current_user_account,
    get_owned_order,
    get_owned_session,
    get_owned_trade_record,
)
from app.crud.order import crud_order
from app.models.order import Order
from app.schemas.order import OrderUpdate, PlaceOrderRequest
from app.core.security import get_current_user
from app.models.user import User
from app.models.trade_record import TradeRecord
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from uuid import UUID

from datetime import datetime

from app.core.database import get_db
from app.core.logger import get_logger
from app.crud.account import ensure_user_account
from app.tasks.async_scheduler import async_task_scheduler
from app.trading.discipline_service import scan_position_disciplines
from app.trading.discipline_settings import (
    PositionDisciplineSettingsResponse,
    PositionDisciplineSettingsUpdate,
    get_position_discipline_settings,
    upsert_position_discipline_settings,
)

# Get logger
logger = get_logger(__name__)


router = APIRouter()

trading_engine = TradingEngine()


@router.get("/discipline-settings", response_model=PositionDisciplineSettingsResponse)
def read_position_discipline_settings(
    current_user: User = Depends(get_current_user),
) -> PositionDisciplineSettingsResponse:
    """读取当前用户止损止盈扫描设置。"""
    return get_position_discipline_settings(current_user.id)


@router.put("/discipline-settings", response_model=PositionDisciplineSettingsResponse)
def update_position_discipline_settings(
    payload: PositionDisciplineSettingsUpdate,
    current_user: User = Depends(get_current_user),
) -> PositionDisciplineSettingsResponse:
    """更新当前用户止损止盈扫描设置。"""
    try:
        updated = upsert_position_discipline_settings(current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    async_task_scheduler.refresh_schedule()
    return updated


@router.post("/discipline-scan")
async def scan_position_disciplines_once(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """立即执行一次当前用户止损止盈扫描。"""
    return await scan_position_disciplines(current_user.id, background_tasks=background_tasks)


@router.post("/orders", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def place_order(
    order: PlaceOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Place order"""
    try:
        session_id = order.session_id
        stock_code = order.stock_code
        action = order.action.value
        stop_loss = order.stop_loss

        # Standardize stock code using existing utility
        if stock_code:
            stock_code = StockCodeStandardizer.standardize(stock_code)

        stock_name = order.stock_name

        # [FEATURE] If stock_name is missing, try to fetch it from database
        if not stock_name and stock_code:
            try:
                stock_basic = data_storage_service.get_stock_basic(stock_code)
                if stock_basic:
                    stock_name = stock_basic.get("name")
                    logger.info(f"Retrieved stock name '{stock_name}' for code '{stock_code}' from DB")
            except Exception as e:
                logger.error(f"Failed to fetch stock name for {stock_code}: {e}")

        # Check account using current user
        account = ensure_user_account(db, current_user)

        if session_id:
            get_owned_session(db, session_id, current_user)

        if action != "buy":
            stop_loss = None

        from app.trading.service import trading_service

        # 使用 TradingService 统一处理订单执行和数据库更新
        # Use TradingService to uniformly handle order execution and DB updates
        order_result = await trading_service.execute_order_and_update_db(
            db=db,
            session_id=session_id,
            account=account,
            stock_code=stock_code,
            action=action,
            shares=order.shares,
            price=order.price,
            order_type=order.order_type.value,
            stop_loss=stop_loss,
        )

        order_payload = order.model_dump(mode="json")
        order_payload["stock_code"] = stock_code
        order_payload["action"] = action
        order_payload["order_type"] = order.order_type.value
        order_payload["stop_loss"] = stop_loss

        if order_result["success"]:
            logger.info("[AUTO_TRADE] Order processed via TradingService")
        else:
            msg = order_result.get('message')
            logger.warning(f"❌ [AUTO_TRADE] Order service execution failed: {msg}")

        if not order_result["success"]:
            if order_result.get("reason") == "risk_control_blocked":
                raise HTTPException(
                    status_code=400,
                    detail={
                        "reason": "risk_control_blocked",
                        "message": order_result["message"],
                        "risk_control": order_result["risk_control"],
                        "blocks": order_result["risk_control"].get("blocks", []),
                        "accepted": order_result["risk_control"].get("accepted", []),
                        "metrics": order_result["risk_control"].get("metrics", {}),
                    },
                )
            return {
                "success": False,
                "message": order_result["message"],
                "order": order_payload
            }

        if order_result.get("status") == "pending":
            pending_order = order_result["order"]
            return {
                "success": True,
                "message": order_result.get("message", "Order accepted"),
                "order": {
                    **order_payload,
                    "id": str(pending_order.order_id),
                    "status": pending_order.status,
                    "filled_shares": pending_order.filled_shares,
                    "reserved_cash": order_result.get("reserved_cash", 0.0),
                },
                "risk_control": order_result.get("risk_control"),
                "trade_record": None,
            }

        # 返回前端期望的格式 (Maintain backward compatibility for return schema)
        trade_data = order_result["trade_result"]["trade_record"]

        return {
            "success": True,
            "message": order_result["trade_result"]["message"],
            "order": order_payload,
            "risk_control": order_result.get("risk_control"),
            "trade_record": {
                "id": str(trade_data["id"]),
                "order_id": str(order_result["order"].order_id),
                "session_id": str(session_id) if session_id else None,
                "account_id": str(account.account_id),
                "stock_code": stock_code,
                "stock_name": stock_name or "Unknown",
                "action": action,
                "price": float(trade_data["price"]),
                "shares": trade_data["shares"],
                "turnover": float(trade_data["turnover"]),
                "commission": float(trade_data["commission"]),
                "stamp_duty": float(trade_data["stamp_duty"]),
                "transfer_fee": float(trade_data["transfer_fee"]),
                "total_fee": float(trade_data["total_fee"]),
                "created_at": datetime.now().isoformat()
            }
        }
    except ValueError as e:
        logger.error(f"Invalid UUID format: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Invalid UUID format: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Failed to place order: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.post("/orders/{order_id}/cancel", response_model=Dict[str, Any])
async def cancel_order(
    order_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel order"""
    try:
        order_record = get_owned_order(db, order_id, current_user)
        from app.trading.service import trading_service

        result = await trading_service.cancel_order(db, order_record)

        # Push order status change
        if result["success"]:
            # 动态获取股票名称
            s_name = db.query(StockBasic.name).filter(StockBasic.stock_code ==
                                                      order_record.stock_code).scalar() or "Unknown"

            order_status_message = {
                "order_id": str(order_record.order_id),
                "status": order_record.status,
                "stock_code": order_record.stock_code,
                "stock_name": s_name,
                "action": order_record.action,
                "price": float(order_record.price),
                "shares": order_record.shares,
                "filled_shares": order_record.filled_shares,
                "updated_at": order_record.updated_at.isoformat()
            }
            await ws_manager.send_order_status(str(order_record.session_id), order_status_message)

        return {
            "success": result["success"],
            "message": result["message"],
            "order_id": str(order_id),
            "status": result["order"].status,
            "released_cash": result.get("released_cash", 0.0),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel order: {str(e)}")
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders/history/{session_id}", response_model=List[Dict[str, Any]])
async def get_order_history(
    session_id: UUID,
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get order history"""
    try:
        get_owned_session(db, session_id, current_user)
        account_id = get_current_user_account(db, current_user).account_id
        query = db.query(Order, StockBasic.name).outerjoin(
            StockBasic, Order.stock_code == StockBasic.stock_code
        ).filter(
            Order.session_id == session_id,
            Order.account_id == account_id,
        )

        if status:
            query = query.filter(Order.status == status)

        results = query.order_by(Order.created_at.desc()).offset(skip).limit(limit).all()

        # Convert to response format
        order_history = []
        for order, s_name in results:
            order_history.append({
                "id": str(order.order_id),
                "stock_code": order.stock_code,
                "stock_name": s_name or "Unknown",
                "action": order.action,
                "order_type": order.order_type,
                "price": float(order.price),
                "shares": order.shares,
                "filled_shares": order.filled_shares,
                "avg_fill_price": float(order.avg_fill_price) if order.avg_fill_price else None,
                "realized_pnl": float(order.realized_pnl) if order.realized_pnl else 0.0,
                "status": order.status,
                "remark": order.remark,
                "created_at": order.created_at.isoformat(),
                "updated_at": order.updated_at.isoformat()
            })

        return order_history
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get order history: {str(e)}")
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders/{order_id}", response_model=Dict[str, Any])
async def get_order(
    order_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get single order details"""
    try:
        order = get_owned_order(db, order_id, current_user)

        # Convert to response format
        order_dict = {
            "id": str(order.order_id),
            "session_id": str(order.session_id) if order.session_id else None,
            "source": order.source,
            "stock_code": order.stock_code,
            "stock_name": db.query(StockBasic.name).filter(
                StockBasic.stock_code == order.stock_code
            ).scalar() or "Unknown",
            "action": order.action,
            "order_type": order.order_type,
            "price": float(order.price),
            "shares": order.shares,
            "filled_shares": order.filled_shares,
            "avg_fill_price": float(order.avg_fill_price) if order.avg_fill_price else None,
            "status": order.status,
            "created_at": order.created_at.isoformat(),
            "updated_at": order.updated_at.isoformat()
        }

        return order_dict
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/orders/{order_id}", response_model=Dict[str, Any])
async def update_order(
    order_id: UUID,
    order_update: OrderUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update order"""
    try:
        # Get order
        order = get_owned_order(db, order_id, current_user)

        # Check order status, only pending orders can be updated
        if order.status not in ["pending"]:
            return {
                "success": False,
                "message": f"Order status is {order.status}, cannot update",
                "order_id": str(order_id)
            }

        if order.order_type == "limit":
            return {
                "success": False,
                "message": "Pending limit order update is not supported; cancel and place a new order",
                "order_id": str(order_id),
            }

        # Update order
        update_data = order_update.model_dump(exclude_unset=True)
        updated_order = crud_order.update(db, db_obj=order, obj_in=update_data)

        # Convert to response format
        s_name = db.query(StockBasic.name).filter(StockBasic.stock_code ==
                                                  updated_order.stock_code).scalar() or "Unknown"
        updated_order_dict = {
            "id": str(updated_order.order_id),
            "stock_code": updated_order.stock_code,
            "stock_name": s_name,
            "action": updated_order.action,
            "order_type": updated_order.order_type,
            "price": float(updated_order.price),
            "shares": updated_order.shares,
            "filled_shares": updated_order.filled_shares,
            "avg_fill_price": float(updated_order.avg_fill_price) if updated_order.avg_fill_price else None,
            "status": updated_order.status,
            "created_at": updated_order.created_at.isoformat(),
            "updated_at": updated_order.updated_at.isoformat()
        }

        return {
            "success": True,
            "message": "Order updated successfully",
            "order": updated_order_dict
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades/{session_id}", response_model=List[Dict[str, Any]])
async def get_trade_records(
    session_id: UUID,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get trade records"""
    try:
        get_owned_session(db, session_id, current_user)
        account_id = get_current_user_account(db, current_user).account_id
        query = db.query(TradeRecord, StockBasic.name).outerjoin(
            StockBasic, TradeRecord.stock_code == StockBasic.stock_code
        ).filter(
            TradeRecord.session_id == session_id,
            TradeRecord.account_id == account_id,
        )

        results = query.order_by(TradeRecord.trade_time.desc()).offset(skip).limit(limit).all()

        trade_records = []
        for record, s_name in results:
            trade_records.append({
                "id": str(record.trade_id),
                "order_id": str(record.order_id),
                "session_id": str(record.session_id),
                "stock_code": record.stock_code,
                "stock_name": s_name or "Unknown",
                "action": record.action,
                "price": float(record.fill_price),
                "shares": record.quantity,
                "turnover": float(record.fill_price) * record.quantity,
                "net_amount": float(record.net_amount),
                "commission": float(record.commission),
                "stamp_duty": float(record.stamp_duty),
                "transfer_fee": float(record.transfer_fee),
                "total_fee": float(record.total_fees),
                "created_at": record.trade_time.isoformat()
            })

        return trade_records
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades/single/{trade_id}", response_model=Dict[str, Any])
async def get_trade_record(
    trade_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get single trade record"""
    try:
        record = get_owned_trade_record(db, trade_id, current_user)

        s_name = db.query(StockBasic.name).filter(StockBasic.stock_code == record.stock_code).scalar() or "Unknown"
        trade_record = {
            "id": str(record.trade_id),
            "order_id": str(record.order_id),
            "session_id": str(record.session_id) if record.session_id else None,
            "stock_code": record.stock_code,
            "stock_name": s_name,
            "action": record.action,
            "price": float(record.fill_price),
            "shares": record.quantity,
            "turnover": float(record.fill_price) * record.quantity,
            "net_amount": float(record.net_amount),
            "commission": float(record.commission),
            "stamp_duty": float(record.stamp_duty),
            "transfer_fee": float(record.transfer_fee),
            "total_fee": float(record.total_fees),
            "created_at": record.trade_time.isoformat()
        }

        return trade_record
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-orders", response_model=List[Dict[str, Any]])
async def get_my_orders(
    skip: int = 0,
    limit: int = 100,
    status: Optional[str] = None,
    stock_code: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current user global order history (all sessions and no session)"""
    try:
        account_id = ensure_user_account(db, current_user).account_id

        query = db.query(Order, StockBasic.name).outerjoin(
            StockBasic, Order.stock_code == StockBasic.stock_code
        ).filter(Order.account_id == account_id)
        if status:
            query = query.filter(Order.status == status)
        if stock_code:
            query = query.filter(Order.stock_code == stock_code)

        orders = query.order_by(Order.created_at.desc()).offset(skip).limit(limit).all()

        # Convert to response format
        order_history = []
        for order, stock_name in orders:
            order_history.append({
                "id": str(order.order_id),
                "session_id": str(order.session_id) if order.session_id else None,
                "source": order.source,
                "stock_code": order.stock_code,
                "stock_name": stock_name or "Unknown",
                "action": order.action,
                "order_type": order.order_type,
                "price": float(order.price),
                "shares": order.shares,
                "filled_shares": order.filled_shares,
                "avg_fill_price": float(order.avg_fill_price) if order.avg_fill_price else None,
                "realized_pnl": float(order.realized_pnl) if order.realized_pnl else 0.0,
                "status": order.status,
                "remark": order.remark,
                "created_at": order.created_at.isoformat(),
                "updated_at": order.updated_at.isoformat()
            })

        return order_history
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get user order history: {str(e)}")
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-trades", response_model=List[Dict[str, Any]])
async def get_my_trades(
    skip: int = 0,
    limit: int = 100,
    stock_code: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current user global trade history (all sessions and no session)"""
    try:
        account_id = ensure_user_account(db, current_user).account_id

        query = db.query(TradeRecord, StockBasic.name).outerjoin(
            StockBasic, TradeRecord.stock_code == StockBasic.stock_code
        ).filter(TradeRecord.account_id == account_id)

        if stock_code:
            query = query.filter(TradeRecord.stock_code == stock_code)

        results = query.order_by(TradeRecord.trade_time.desc()).offset(skip).limit(limit).all()

        trade_records = []
        for record, s_name in results:
            trade_records.append({
                "id": str(record.trade_id),
                "order_id": str(record.order_id),
                "session_id": str(record.session_id) if record.session_id else None,
                "stock_code": record.stock_code,
                "stock_name": s_name or "Unknown",
                "action": record.action,
                "price": float(record.fill_price),
                "shares": record.quantity,
                "turnover": float(record.fill_price) * record.quantity,
                "net_amount": float(record.net_amount),
                "commission": float(record.commission),
                "stamp_duty": float(record.stamp_duty),
                "transfer_fee": float(record.transfer_fee),
                "total_fee": float(record.total_fees),
                "created_at": record.trade_time.isoformat()
            })

        return trade_records
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get user trade history: {str(e)}")
        logger.exception(e)
        raise HTTPException(status_code=500, detail=str(e))
