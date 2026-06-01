from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ExperienceIndex(Base):
    """保存 Memory 经验的轻量展示索引。"""

    __tablename__ = "experience_indexes"
    __table_args__ = (
        UniqueConstraint("user_id", "memory_observation_id", name="uq_experience_indexes_user_memory_observation"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    memory_observation_id = Column(String(100), nullable=True, index=True)
    memory_source_id = Column(String(100), nullable=True, index=True)
    review_run_id = Column(String(36), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    stock_code = Column(String(20), nullable=True, index=True)
    stock_name = Column(String(100), nullable=True)
    industry = Column(String(100), nullable=True, index=True)
    strategy = Column(String(100), nullable=True, index=True)
    review_horizon = Column(String(10), nullable=True, index=True)
    outcome_label = Column(String(50), nullable=True, index=True)
    correctness = Column(String(50), nullable=True, index=True)
    importance = Column(String(20), nullable=True, index=True)
    summary = Column(Text, nullable=False)
    tags = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
