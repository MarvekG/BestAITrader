import uuid
from datetime import date, datetime

from sqlalchemy import DECIMAL, Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class AccountEquitySnapshot(Base):
    """记录模拟账户每日净值和基准对比。"""

    __tablename__ = "account_equity_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "snapshot_date",
            "benchmark_code",
            name="idx_account_equity_snapshot_unique",
        ),
    )

    snapshot_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.account_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_date = Column(Date, nullable=False, index=True, default=date.today)

    total_assets = Column(DECIMAL(18, 4), nullable=False)
    available_cash = Column(DECIMAL(18, 4), nullable=False)
    market_value = Column(DECIMAL(18, 4), nullable=False)
    position_count = Column(Integer, nullable=False, default=0)

    daily_return = Column(DECIMAL(18, 8), nullable=True)
    cumulative_return = Column(DECIMAL(18, 8), nullable=True)
    benchmark_code = Column(String(20), nullable=False, default="000300.SH")
    benchmark_close = Column(DECIMAL(18, 6), nullable=True)
    benchmark_daily_return = Column(DECIMAL(18, 8), nullable=True)
    benchmark_cumulative_return = Column(DECIMAL(18, 8), nullable=True)
    excess_return = Column(DECIMAL(18, 8), nullable=True)
    max_drawdown = Column(DECIMAL(18, 8), nullable=True)
    missing_benchmark_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.now, nullable=False)
