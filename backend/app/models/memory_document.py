from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class MemoryDocument(Base):
    """保存单个用户单只股票锚定的整份记忆文档。"""

    __tablename__ = "memory_documents"
    __table_args__ = (
        UniqueConstraint("user_id", "stock_code", name="uq_memory_documents_user_stock"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stock_code = Column(String(20), nullable=False)
    content = Column(Text, nullable=False, default="")
    size_chars = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)
