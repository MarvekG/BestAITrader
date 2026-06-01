import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from app.core.database import Base


class StockSelectionRun(Base):
    __tablename__ = "stock_selection_runs"
    __table_args__ = (
        CheckConstraint("scope IN ('warehouse', 'core', 'all')", name="ck_stock_selection_runs_scope"),
        CheckConstraint(
            "style IN ('balanced', 'momentum', 'value', 'growth', 'defensive')",
            name="ck_stock_selection_runs_style",
        ),
        CheckConstraint("risk_level IN ('low', 'medium', 'high')", name="ck_stock_selection_runs_risk_level"),
        CheckConstraint("recommendation_count BETWEEN 4 AND 8", name="ck_stock_selection_runs_recommendation_count"),
        Index("ix_stock_selection_runs_user_created_at", "user_id", "created_at"),
        Index("ix_stock_selection_runs_status_created_at", "status", "created_at"),
        {"schema": "stock_picker"},
    )

    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    scope = Column(String(20), nullable=False)
    style = Column(String(20), nullable=False)
    risk_level = Column(String(20), nullable=False, default="medium")
    recommendation_count = Column(Integer, nullable=False, default=5)
    status = Column(String(50), nullable=False, default="created", index=True)
    current_stage = Column(String(50), nullable=False, default="created")
    request_payload = Column(JSON, nullable=False, default=dict)
    summary_payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)

    events = relationship("StockSelectionEvent", back_populates="run", passive_deletes=True)
    candidates = relationship("StockSelectionCandidate", back_populates="run", passive_deletes=True)


class StockSelectionEvent(Base):
    __tablename__ = "stock_selection_events"
    __table_args__ = (
        Index("ix_stock_selection_events_run_created_at", "run_id", "created_at"),
        Index("ix_stock_selection_events_run_stage_created_at", "run_id", "stage", "created_at"),
        {"schema": "stock_picker"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stock_picker.stock_selection_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(50), nullable=False, index=True)
    event_type = Column(String(50), nullable=False, default="info")
    message = Column(Text, nullable=False)
    payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    run = relationship("StockSelectionRun", back_populates="events")


class StockSelectionCandidate(Base):
    __tablename__ = "stock_selection_candidates"
    __table_args__ = (
        CheckConstraint("decision IN ('keep', 'watch', 'drop')", name="ck_stock_selection_candidates_decision"),
        CheckConstraint("factor_score >= 0 AND factor_score <= 100", name="ck_stock_selection_candidates_factor_score"),
        CheckConstraint("ai_score >= 0 AND ai_score <= 100", name="ck_stock_selection_candidates_ai_score"),
        CheckConstraint("final_score >= 0 AND final_score <= 100", name="ck_stock_selection_candidates_final_score"),
        UniqueConstraint("run_id", "stock_code", name="uq_stock_selection_candidate_run_stock"),
        Index("ix_stock_selection_candidates_run_final_score", "run_id", "final_score"),
        Index("ix_stock_selection_candidates_run_decision", "run_id", "decision"),
        {"schema": "stock_picker"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey("stock_picker.stock_selection_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stock_code = Column(String(20), nullable=False, index=True)
    source_scope = Column(String(20), nullable=False)
    style = Column(String(20), nullable=False)
    factor_score = Column(Float, nullable=False, default=0.0)
    ai_score = Column(Float, nullable=False, default=0.0)
    final_score = Column(Float, nullable=False, default=0.0)
    decision = Column(String(20), nullable=False, default="watch", index=True)
    eliminated_stage = Column(String(50), nullable=True)
    eliminated_reason = Column(Text, nullable=True)
    research_payload = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    run = relationship("StockSelectionRun", back_populates="candidates")
