from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer, JSON, Text
from sqlalchemy.sql import func
from app.core.database import Base
import uuid


class AsyncTask(Base):
    """Async task model
    
    Used to store and manage async task status and result
    """
    __tablename__ = "async_tasks"
    
    task_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True, comment="Owner User ID")
    task_name = Column(String(255), nullable=False, comment="Task Name")
    task_type = Column(String(100), nullable=False, comment="Task Type")
    status = Column(
        String(20), 
        nullable=False, 
        default="pending",
        comment="Task Status: pending/running/completed/failed"
    )
    allow_concurrent = Column(Boolean, default=True, comment="Whether to allow concurrency")
    parameters = Column(JSON, comment="Task Parameters")
    result = Column(JSON, comment="Task Result")
    error_message = Column(Text, comment="Error Message")
    
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="Creation Time")
    started_at = Column(DateTime(timezone=True), comment="Start Time")
    completed_at = Column(DateTime(timezone=True), comment="Completion Time")
    
    def to_dict(self):
        """Convert to dictionary format"""
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_type": self.task_type,
            "status": self.status,
            "allow_concurrent": self.allow_concurrent,
            "parameters": self.parameters,
            "result": self.result,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None
        }
