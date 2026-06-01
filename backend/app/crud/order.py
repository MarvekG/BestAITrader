from sqlalchemy.orm import Session
from typing import List, Dict, Any, Optional
from uuid import UUID

from app.crud.base import CRUDBase
from app.models.order import Order
from app.schemas.order import OrderCreate, OrderUpdate


class CRUDOrder(CRUDBase[Order, OrderCreate, OrderUpdate]):
    def get_by_id(self, db: Session, order_id: UUID) -> Optional[Order]:
        """根据订单ID获取订单"""
        return db.query(self.model).filter(self.model.order_id == order_id).first()
    
    def get_by_session(self, db: Session, session_id: UUID, skip: int = 0, limit: int = 100) -> List[Order]:
        """根据会话ID获取订单列表"""
        return db.query(self.model).filter(
            self.model.session_id == session_id
        ).order_by(
            self.model.created_at.desc()
        ).offset(skip).limit(limit).all()
    
    def get_by_status(self, db: Session, session_id: UUID, status: str, skip: int = 0, limit: int = 100) -> List[Order]:
        """根据会话ID和状态获取订单列表"""
        return db.query(self.model).filter(
            self.model.session_id == session_id,
            self.model.status == status
        ).order_by(
            self.model.created_at.desc()
        ).offset(skip).limit(limit).all()
    
    def cancel_order(self, db: Session, order_id: UUID) -> Dict[str, Any]:
        """取消订单"""
        order = self.get_by_id(db, order_id)
        if not order:
            return {
                "success": False,
                "message": "Order does not exist",
                "order": None
            }
        
        if order.status == "pending":
            order.status = "cancelled"
            db.commit()
            db.refresh(order)
            return {
                "success": True,
                "message": "Order cancelled successfully",
                "order": order
            }
        else:
            return {
                "success": False,
                "message": f"Order cannot be cancelled, current status: {order.status}",
                "order": order
            }
    
    def update_order_status(self, db: Session, order_id: UUID, status: str, filled_shares: int = None, avg_fill_price: float = None) -> Optional[Order]:
        """更新订单状态"""
        order = self.get_by_id(db, order_id)
        if order:
            order.status = status
            if filled_shares is not None:
                order.filled_shares = filled_shares
            if avg_fill_price is not None:
                order.avg_fill_price = avg_fill_price
            db.commit()
            db.refresh(order)
        return order


crud_order = CRUDOrder(Order)
