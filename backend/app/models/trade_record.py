from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, DECIMAL, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.core.database import Base

class TradeRecord(Base):
    __tablename__ = "trade_records"
    
    trade_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id"))
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.account_id", ondelete="CASCADE"))
    
    order_id = Column(UUID(as_uuid=True))
    stock_code = Column(String(10), index=True)
    
    action = Column(String(10), index=True)
    quantity = Column(Integer)
    fill_price = Column(DECIMAL(10, 4))
    
    commission = Column(DECIMAL(10, 4))
    stamp_duty = Column(DECIMAL(10, 4))
    transfer_fee = Column(DECIMAL(10, 4))
    total_fees = Column(DECIMAL(10, 4))
    
    net_amount = Column(DECIMAL(15, 4))
    trade_time = Column(DateTime, default=datetime.now, name="trade_time")
    created_at = Column(DateTime, default=datetime.now)
    
    session = relationship("Session", back_populates="trade_records")
    account = relationship("Account", back_populates="trade_records")
    
    __table_args__ = (
        Index('idx_session', 'session_id'),
        Index('idx_stock', 'stock_code'),
        Index('idx_time', 'trade_time'),
    )
