from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.sql import func

from app.core.database import Base


class MarketWatchEvent(Base):
    """Audit event emitted by market watch scans and downstream decisions."""

    __tablename__ = "market_watch_events"

    event_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)
    watch_ai_decision = Column(JSON, nullable=True)
    debate_parameters = Column(JSON, nullable=True)
    debate_session_id = Column(String(36), nullable=True)
    task_id = Column(String(36), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now, server_default=func.now(), index=True)
