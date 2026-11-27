from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class ExperienceReviewRun(Base):
    """经验复盘运行与队列状态。"""

    __tablename__ = "experience_review_runs"

    review_run_id = Column(String(36), primary_key=True)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_code = Column(String(20), nullable=False)
    review_horizon = Column(String(8), nullable=False)
    status = Column(String(20), nullable=False, default="queued", index=True)
    error_message = Column(Text, nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.now, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_experience_review_runs_status_created", "status", "created_at"),
        Index("ix_experience_review_runs_user_stock_status", "user_id", "stock_code", "status"),
        Index("ix_experience_review_runs_session_status", "session_id", "status"),
        Index(
            "uq_experience_review_runs_active_session",
            "session_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "uq_experience_review_runs_active_stock",
            "user_id",
            "stock_code",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )
