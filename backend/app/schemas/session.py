from typing import Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict


SESSION_SOURCE_VALUES = "^(manual|scheduled|market_watch|stop_loss|take_profit)$"


# 基本会话模型
class SessionBase(BaseModel):
    user_id: Optional[int] = None
    stock_code: str
    trading_frequency: str
    trading_strategy: str
    source: str = Field(default="manual", pattern=SESSION_SOURCE_VALUES)


# 创建会话模型
class SessionCreate(SessionBase):
    pass


# 更新会话模型
class SessionUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(active|completed|archived)$")
    stock_code: Optional[str] = None
    source: Optional[str] = Field(None, pattern=SESSION_SOURCE_VALUES)


# 会话响应模型
class SessionResponse(SessionBase):
    session_id: UUID
    created_at: datetime
    updated_at: datetime
    ended_at: Optional[datetime] = None
    status: str
    stock_name: Optional[str] = None

    # 关联数据ID
    data_snapshot_id: Optional[UUID] = None
    debate_thread_id: Optional[UUID] = None

    # 会话上下文
    current_position: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class SessionListResponse(BaseModel):
    """会话分页列表响应。"""

    total: int
    items: list[SessionResponse]
    limit: int
    skip: int
