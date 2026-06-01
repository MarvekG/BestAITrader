from sqlalchemy import Column, BigInteger, String, DateTime, DECIMAL, Enum, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
from app.core.database import Base


class Order(Base):
    __tablename__ = "orders"

    order_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id"))
    account_id = Column(UUID(as_uuid=True), ForeignKey("accounts.account_id", ondelete="CASCADE"))

    stock_code = Column(String(10), index=True)

    action = Column(String(10), index=True)
    order_type = Column(String(10))
    price = Column(DECIMAL(10, 4))
    shares = Column(BigInteger)

    status = Column(
        Enum("pending", "partial", "filled", "cancelled", "rejected", name="order_status"),
        default="pending"
    )
    filled_shares = Column(BigInteger, default=0)
    avg_fill_price = Column(DECIMAL(10, 4))
    realized_pnl = Column(DECIMAL(15, 4))

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    filled_at = Column(DateTime)

    remark = Column(String(500))
    # 订单来源：AI自动交易为 "ai:<session_id>"，手动下单为 "manual"
    # Order source: "ai:<session_id>" for AI auto-trade, "manual" for manual orders
    source = Column(String(100), nullable=True)

    session = relationship("Session", back_populates="orders")
    account = relationship("Account", back_populates="orders")

    __table_args__ = (
        Index('idx_order_session', 'session_id'),
        Index('idx_order_status', 'status'),
        Index('idx_order_created', 'created_at'),
    )
