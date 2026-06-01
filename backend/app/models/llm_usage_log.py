"""
LLM 使用日志模型
用于统计 Token 消耗和调用频率
"""
from datetime import datetime
import uuid

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base


class LLMUsageLog(Base):
    """LLM 使用日志表"""
    __tablename__ = "llm_usage_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    model = Column(String(100), nullable=False, index=True)  # 模型名称
    role = Column(String(50), nullable=False, index=True)   # 角色标识 (fundamental, technical, etc.)
    
    # Token 统计
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    cached_tokens = Column(Integer, default=0)
    cache_miss_tokens = Column(Integer, default=0)
    reasoning_tokens = Column(Integer, default=0)

    # 观测维度
    workflow = Column(String(50), nullable=True, index=True)
    stage = Column(String(80), nullable=True, index=True)
    call_kind = Column(String(50), nullable=True, index=True)
    iteration_index = Column(Integer, nullable=True)
    cache_lane = Column(String(50), nullable=True, index=True)
    api_key_alias = Column(String(80), nullable=True, index=True)

    # 关联信息
    session_id = Column(UUID(as_uuid=True), index=True, nullable=True)

    # 记录时间
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)

    def to_dict(self):
        return {
            "id": str(self.id),
            "model": self.model,
            "role": self.role,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "workflow": self.workflow,
            "stage": self.stage,
            "call_kind": self.call_kind,
            "iteration_index": self.iteration_index,
            "cache_lane": self.cache_lane,
            "api_key_alias": self.api_key_alias,
            "session_id": str(self.session_id) if self.session_id else None,
            "created_at": self.created_at.isoformat()
        }
