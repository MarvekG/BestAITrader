from sqlalchemy import Column, String, DateTime, ForeignKey, DECIMAL, Float, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.core.database import Base

class Account(Base):
    __tablename__ = "accounts"
    
    account_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)  # 改为用户级别，一个用户一个账户
    
    # 资金
    total_assets = Column(DECIMAL(15, 4))
    available_cash = Column(DECIMAL(15, 4))
    frozen_cash = Column(DECIMAL(15, 4))
    market_value = Column(DECIMAL(15, 4))
    initial_capital = Column(DECIMAL(15, 4), default=1000000.00)
    
    # 盈亏
    total_profit_loss = Column(DECIMAL(15, 4))
    profit_loss_pct = Column(DECIMAL(5, 2))
    
    # 统计
    total_trades = Column(Integer, default=0)
    win_rate = Column(DECIMAL(5, 2))
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关联关系
    user = relationship("User", back_populates="account")
    positions = relationship("Position", back_populates="account", cascade="all, delete-orphan", passive_deletes=True)
    trade_records = relationship("TradeRecord", back_populates="account", cascade="all, delete-orphan", passive_deletes=True)
    orders = relationship("Order", back_populates="account", cascade="all, delete-orphan", passive_deletes=True)

