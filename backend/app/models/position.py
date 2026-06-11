from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, DECIMAL, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.core.database import Base


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("account_id", "stock_code", name="uq_positions_account_stock_code"),
    )

    position_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.account_id", ondelete="CASCADE"))
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id"), nullable=True)  # 添加 session_id，关联到具体的交易 Session
    stock_code = Column(String(10), index=True)

    # 持仓详情
    total_shares = Column(Integer)
    available_shares = Column(Integer)
    frozen_shares = Column(Integer)

    avg_cost = Column(DECIMAL(10, 4))
    current_price = Column(DECIMAL(10, 4))
    market_value = Column(DECIMAL(15, 4))

    profit_loss = Column(DECIMAL(15, 4))
    profit_loss_pct = Column(DECIMAL(5, 4))

    purchase_details = Column(JSON)

    # PM 决策纪律（由最近一次辩论的 PM 结构化字段同步，market_watch 盘中扫描据此判定触发）
    stop_loss = Column(DECIMAL(10, 4), nullable=True)
    take_profit = Column(DECIMAL(10, 4), nullable=True)
    horizon_deadline = Column(DateTime, nullable=True)  # PM 决策时间 + holding_horizon_days
    pm_session_id = Column(UUID(as_uuid=True), nullable=True)  # 给出该纪律的会话（无外键，会话删除后仍保留）

    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关联关系
    account = relationship("Account", back_populates="positions")
    session = relationship("Session")  # 添加到 Session 的关系
