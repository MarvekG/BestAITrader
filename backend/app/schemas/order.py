from pydantic import BaseModel, ConfigDict
from typing import Optional
from uuid import UUID
from datetime import datetime
from enum import Enum


class OrderStatus(str, Enum):
    """订单状态枚举"""
    pending = "pending"
    partial = "partial"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"


class OrderBase(BaseModel):
    """订单基础模型"""
    stock_code: str
    stock_name: Optional[str] = None
    action: str
    order_type: str
    price: float
    shares: int
    session_id: Optional[UUID] = None
    account_id: Optional[UUID] = None


class OrderCreate(OrderBase):
    """创建订单模型"""
    pass


class OrderUpdate(BaseModel):
    """更新订单模型"""
    status: Optional[OrderStatus] = None
    filled_shares: Optional[int] = None
    avg_fill_price: Optional[float] = None
    remark: Optional[str] = None


class OrderResponse(OrderBase):
    """订单响应模型"""
    order_id: UUID
    status: OrderStatus
    filled_shares: int
    avg_fill_price: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    filled_at: Optional[datetime] = None
    remark: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)