from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class Session(Base):
    __tablename__ = "sessions"

    session_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # 添加外键约束
    stock_code = Column(String(10), index=True)
    trading_frequency = Column(String(50), nullable=False)
    trading_strategy = Column(String(50), nullable=False)
    source = Column(String(20), nullable=False, default="manual")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    status = Column(Enum("active", "completed", "failed", "archived", name="session_status"), default="active")

    user = relationship("User")  # 添加到 User 的关系

    debate_messages = relationship("DebateMessage", back_populates="session",
                                   uselist=True, lazy="dynamic", cascade="all, delete-orphan")
    pm_decision = relationship(
        "PMDecisionRecord",
        back_populates="session",
        uselist=False,
        cascade="all, delete-orphan",
    )
    trade_records = relationship("TradeRecord", back_populates="session", uselist=True)
    # account 关系已移除，改为通过 user.account 访问
    orders = relationship("Order", back_populates="session", uselist=True)
