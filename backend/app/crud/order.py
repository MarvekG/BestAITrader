from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.base import CRUDBase
from app.models.order import Order
from app.schemas.order import OrderCreate, OrderUpdate


class CRUDOrder(CRUDBase[Order, OrderCreate, OrderUpdate]):
    async def get_by_id(self, db: AsyncSession, order_id: UUID) -> Order | None:
        """根据订单 ID 获取订单。"""
        result = await db.execute(select(self.model).where(self.model.order_id == order_id))
        return result.scalar_one_or_none()

    async def get_by_session(
        self,
        db: AsyncSession,
        session_id: UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """根据会话 ID 获取订单列表。"""
        result = await db.execute(
            select(self.model)
            .where(self.model.session_id == session_id)
            .order_by(self.model.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_status(
        self,
        db: AsyncSession,
        session_id: UUID,
        status: str,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        """根据会话 ID 和状态获取订单列表。"""
        result = await db.execute(
            select(self.model)
            .where(self.model.session_id == session_id, self.model.status == status)
            .order_by(self.model.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def cancel_order(self, db: AsyncSession, order_id: UUID) -> dict[str, Any]:
        """取消订单。"""
        order = await self.get_by_id(db, order_id)
        if not order:
            return {
                "success": False,
                "message": "Order does not exist",
                "order": None,
            }

        if order.status == "pending":
            order.status = "cancelled"
            await db.commit()
            await db.refresh(order)
            return {
                "success": True,
                "message": "Order cancelled successfully",
                "order": order,
            }

        return {
            "success": False,
            "message": f"Order cannot be cancelled, current status: {order.status}",
            "order": order,
        }

    async def update_order_status(
        self,
        db: AsyncSession,
        order_id: UUID,
        status: str,
        filled_shares: int | None = None,
        avg_fill_price: float | None = None,
    ) -> Order | None:
        """更新订单状态。"""
        order = await self.get_by_id(db, order_id)
        if order:
            order.status = status
            if filled_shares is not None:
                order.filled_shares = filled_shares
            if avg_fill_price is not None:
                order.avg_fill_price = avg_fill_price
            await db.commit()
            await db.refresh(order)
        return order


crud_order = CRUDOrder(Order)
