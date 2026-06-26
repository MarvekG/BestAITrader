from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import Base


class PMDecisionRecord(Base):
    """PM 结构化决策记录。"""

    __tablename__ = "pm_decisions"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_pm_decisions_session_id"),
    )

    decision_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)

    target_position = Column(Float, nullable=False)
    confidence_score = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    holding_horizon_days = Column(Integer, nullable=True)
    source = Column(String(50), nullable=False, default="pm_tool")
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    session = relationship("Session", back_populates="pm_decision")
    user = relationship("User")

    def to_dict(self) -> dict:
        """转换为 API 和 Agent 上下文可复用的字典。"""
        return {
            "decision_id": str(self.decision_id),
            "session_id": str(self.session_id),
            "user_id": self.user_id,
            "stock_code": self.stock_code,
            "target_position": self.target_position,
            "confidence_score": self.confidence_score,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "holding_horizon_days": self.holding_horizon_days,
            "source": self.source,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
