from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from app.core.database import Base


class StockWarehouse(Base):
    __tablename__ = "stock_warehouse"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(
        String(20),
        ForeignKey("data.stock_basic.stock_code", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    added_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)  # 标记是否为上证50股票

    auto_analysis_enabled = Column(Boolean, default=False, nullable=False)
    auto_analysis_frequency = Column(String(20), default="daily", nullable=False)
    auto_analysis_time = Column(String(5), default="09:35", nullable=False)
    auto_analysis_trading_frequency = Column(String(50), default="中长线持有 (Position Trading)", nullable=False)
    auto_analysis_trading_strategy = Column(String(50), default="价值投资 (Value Investing)", nullable=False)
    auto_analysis_run_immediately = Column(Boolean, default=False, nullable=False)
    last_auto_analysis_at = Column(DateTime, nullable=True)
    last_auto_analysis_session_id = Column(String(36), nullable=True)
    last_auto_analysis_task_id = Column(String(36), nullable=True)
    last_auto_analysis_error = Column(Text, nullable=True)

    # 外键关系
    user_id = Column(
        Integer, ForeignKey("users.id"), nullable=False, index=True
    )

    __table_args__ = (
        UniqueConstraint('stock_code', 'user_id', name='_stock_user_uc'),
    )

    def __repr__(self):
        return (
            f"<StockWarehouse(stock_code={self.stock_code}, "
            f"is_default={self.is_default}, "
            f"user_id={self.user_id})>"
        )
