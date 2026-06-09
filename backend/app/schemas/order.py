from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class OrderStatus(str, Enum):
    """订单状态枚举"""
    pending = "pending"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"


class OrderAction(str, Enum):
    """订单方向枚举"""

    buy = "buy"
    sell = "sell"


class OrderType(str, Enum):
    """订单类型枚举"""

    market = "market"
    limit = "limit"


class PlaceOrderRequest(BaseModel):
    """
    下单请求模型，提前校验交易方向、订单类型和基础数值边界。

    Args:
        stock_code: 股票代码，允许前端传入未标准化代码，路由层继续使用标准化工具处理
        stock_name: 股票名称，缺失时由路由层尝试从数据库补齐
        action: 买入或卖出方向
        order_type: 市价单或限价单
        price: 委托价格，市价单允许为 0，限价单必须为正数
        shares: 委托股数，必须为正整数
        session_id: 可选 AI 分析会话 ID
        stop_loss: 可选止损价，买入时若提供必须为正数；是否必填由风控规则判断
    """

    stock_code: str = Field(min_length=1)
    stock_name: Optional[str] = None
    action: OrderAction
    order_type: OrderType = OrderType.market
    price: float = Field(ge=0)
    shares: int = Field(gt=0)
    session_id: Optional[UUID] = None
    stop_loss: Optional[float] = Field(default=None, gt=0)

    @field_validator("stock_code")
    @classmethod
    def validate_stock_code(cls, value: str) -> str:
        """
        清理股票代码输入，拒绝空白字符串。

        Args:
            value: 原始股票代码

        Returns:
            去除首尾空白后的股票代码

        Raises:
            ValueError: 股票代码为空白字符串时抛出
        """
        stock_code = value.strip()
        if not stock_code:
            raise ValueError("stock_code must not be empty")
        return stock_code

    @field_validator("price")
    @classmethod
    def validate_price(cls, value: float, info: ValidationInfo) -> float:
        """
        校验价格边界，限价单必须提供正价格。

        Args:
            value: 委托价格
            info: Pydantic 字段校验上下文

        Returns:
            合法价格

        Raises:
            ValueError: 限价单价格小于等于 0 时抛出
        """
        order_type = info.data.get("order_type")
        if order_type == OrderType.limit and value <= 0:
            raise ValueError("limit order price must be greater than 0")
        return value


class OrderBase(BaseModel):
    """订单基础模型"""
    stock_code: str
    stock_name: Optional[str] = None
    action: OrderAction
    order_type: OrderType
    price: float
    shares: int
    session_id: Optional[UUID] = None
    account_id: Optional[UUID] = None


class OrderCreate(OrderBase):
    """创建订单模型"""
    pass


class OrderUpdate(BaseModel):
    """
    订单更新模型，只允许修改待成交订单的安全字段。

    Args:
        price: 可选委托价格，必须大于等于 0；是否允许修改由路由层按订单状态判断
        shares: 可选委托股数，必须为正整数；是否允许修改由路由层按订单状态判断
        remark: 可选备注
    """

    price: Optional[float] = Field(default=None, ge=0)
    shares: Optional[int] = Field(default=None, gt=0)
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
