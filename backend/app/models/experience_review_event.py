from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ExperienceReviewEvent(Base):
    __tablename__ = "experience_review_events"

    event_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_run_id = Column(String(36), nullable=False, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, default="experience_review_update")
    stage = Column(String(50), nullable=False, index=True)
    status = Column(String(30), nullable=False, index=True)
    message_key = Column(String(255), nullable=True)
    message_params = Column(JSON, nullable=False, default=dict)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
