import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.types import JSON

from app.ai.stock_picker.interactive_research.constants import (
    CACHE_CONTEXT_VERSION,
    MESSAGE_ROLES,
    MESSAGE_STATUSES,
    MESSAGE_TYPES,
    RESEARCH_PHASES,
    RESEARCH_RUN_STATUSES,
)
from app.core.database import Base


INTERACTIVE_STOCK_PICKER_SCHEMA = "stock_picker_interactive"


def _sql_in(values: set[str]) -> str:
    """生成 SQL CheckConstraint 使用的字符串枚举表达式。

    Args:
        values: 允许值集合。

    Returns:
        形如 `'a', 'b'` 的 SQL 字面量片段。
    """
    return ", ".join(f"'{item}'" for item in sorted(values))


class InteractiveResearchRun(Base):
    """聊天式 Deep Research 选股 run 主表。"""

    __tablename__ = "research_runs"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_in(RESEARCH_RUN_STATUSES)})",
            name="ck_interactive_research_runs_status",
        ),
        CheckConstraint(
            f"current_phase IN ({_sql_in(RESEARCH_PHASES)})",
            name="ck_interactive_research_runs_phase",
        ),
        Index("ix_interactive_research_runs_user_created_at", "user_id", "created_at"),
        Index("ix_interactive_research_runs_status_created_at", "status", "created_at"),
        {"schema": INTERACTIVE_STOCK_PICKER_SCHEMA},
    )

    run_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(50), nullable=False, default="drafting_plan", index=True)
    current_stage = Column(String(50), nullable=False, default="drafting_plan")
    current_phase = Column(String(30), nullable=False, default="planning", index=True)
    title = Column(String(160), nullable=False)
    raw_requirement = Column(Text, nullable=False)
    pending_message_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    checkpoint_payload = Column(JSON, nullable=False, default=dict)
    cache_context_version = Column(String(80), nullable=False, default=CACHE_CONTEXT_VERSION)
    version = Column(Integer, nullable=False, default=1)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)
    finished_at = Column(DateTime, nullable=True)

    messages = relationship(
        "InteractiveResearchMessage",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="InteractiveResearchMessage.sequence_no",
    )


class InteractiveResearchMessage(Base):
    """聊天式 Deep Research 消息流表。"""

    __tablename__ = "research_messages"
    __table_args__ = (
        CheckConstraint(f"role IN ({_sql_in(MESSAGE_ROLES)})", name="ck_interactive_messages_role"),
        CheckConstraint(
            f"message_type IN ({_sql_in(MESSAGE_TYPES)})",
            name="ck_interactive_messages_type",
        ),
        CheckConstraint(
            f"status IN ({_sql_in(MESSAGE_STATUSES)})",
            name="ck_interactive_messages_status",
        ),
        UniqueConstraint("run_id", "sequence_no", name="uq_interactive_messages_run_sequence"),
        Index("ix_interactive_messages_run_sequence", "run_id", "sequence_no"),
        Index("ix_interactive_messages_run_created_at", "run_id", "created_at"),
        {"schema": INTERACTIVE_STOCK_PICKER_SCHEMA},
    )

    message_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(
        UUID(as_uuid=True),
        ForeignKey(f"{INTERACTIVE_STOCK_PICKER_SCHEMA}.research_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = Column(String(20), nullable=False)
    message_type = Column(String(40), nullable=False, index=True)
    content = Column(Text, nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    parent_message_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    sequence_no = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="completed")
    visible_to_user = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.now)

    run = relationship("InteractiveResearchRun", back_populates="messages")
