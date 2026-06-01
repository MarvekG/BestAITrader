from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, DECIMAL, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.core.database import Base

class Position(Base):
    __tablename__ = "positions"
    
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
    
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    # 关联关系
    account = relationship("Account", back_populates="positions")
    session = relationship("Session")  # 添加到 Session 的关系
