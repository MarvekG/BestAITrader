from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, JSON, String, text

from app.core.database import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"
    __table_args__ = (
        Index(
            "ix_system_settings_global_key_unique",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
            sqlite_where=text("user_id IS NULL"),
        ),
        Index(
            "ix_system_settings_user_key_unique",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
            sqlite_where=text("user_id IS NOT NULL"),
        ),
    )
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key = Column(String(100), nullable=False, index=True)
    value = Column(JSON, nullable=False)
    description = Column(String(500))
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    created_at = Column(DateTime, default=datetime.now)
