from sqlalchemy import Column, String, Float, Date, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime
from app.core.database import Base

class StockIndicators(Base):
    """
    Stock Technical Indicators Pre-calculation Table
    Stores daily calculated indicators to accelerate experience review and analysis.
    """
    __tablename__ = "stock_indicators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    stock_code = Column(String(20), ForeignKey('data.stock_basic.stock_code', ondelete='CASCADE'), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)

    # --- Trend Indicators ---
    # Moving Averages
    ma5 = Column(Float)
    ma10 = Column(Float)
    ma20 = Column(Float)
    ma30 = Column(Float)
    ma60 = Column(Float)
    ma120 = Column(Float)
    ma250 = Column(Float)

    # --- Momentum Indicators ---
    # MACD (Moving Average Convergence Divergence)
    macd = Column(Float)         # DIF
    macd_signal = Column(Float)  # DEA
    macd_hist = Column(Float)    # MACD Bar

    # KDJ (Stochastic Oscillator) - Note: A-share specific calculation might be needed
    kdj_k = Column(Float)
    kdj_d = Column(Float)
    kdj_j = Column(Float)

    # RSI (Relative Strength Index)
    rsi_6 = Column(Float)
    rsi_12 = Column(Float)
    rsi_24 = Column(Float)

    # CCI (Commodity Channel Index)
    cci = Column(Float)  # Default N=14

    # WR (Williams %R)
    wr_14 = Column(Float) # Williams %R 14

    # --- Volatility Indicators ---
    # BOLL (Bollinger Bands)
    boll_upper = Column(Float)
    boll_mid = Column(Float)
    boll_lower = Column(Float)

    # ATR (Average True Range)
    atr = Column(Float)   # Default N=14

    # --- Volume Indicators ---
    # OBV (On-Balance Volume)
    obv = Column(Float)

    # --- Meta ---
    data_source = Column(String(20), default='calculated')
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        UniqueConstraint('stock_code', 'trade_date', name='idx_stock_indicators_unique'),
        {"schema": "data"}
    )
