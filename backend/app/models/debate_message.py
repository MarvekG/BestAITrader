"""
辩论消息数据模型
存储辩论过程中的每条消息
"""
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from app.core.database import Base


class DebateMessage(Base):
    """辩论消息表"""
    __tablename__ = "debate_messages"
    message_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="CASCADE"),  # 添加级联删除
        nullable=False,
        index=True
    )

    # 消息信息
    stage = Column(String(50), nullable=False, index=True)  # 辩论阶段
    round_number = Column(Integer, nullable=False)  # 轮次
    agent_name = Column(String(100), nullable=False)  # 角色名称
    agent_role = Column(String(50), nullable=False)  # 角色类型

    # 消息内容
    decision = Column(Text)  # 决策(buy/sell/hold)

    confidence = Column(Float)  # 信心度(0-1)
    reasoning = Column(Text)  # 推理过程
    prompt_input = Column(Text)  # 推理输入（发送给AI的prompt）
    analysis = Column(JSON)  # 详细分析(JSON格式)

    # 元数据
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    # 关系
    session = relationship("Session", back_populates="debate_messages")

    def to_dict(self, exclude_prompt=False):
        """转换为字典

        Args:
            exclude_prompt: 是否排除 prompt_input 字段（用于 WebSocket 推送，减少消息大小）
        """
        result = {
            "message_id": str(self.message_id),
            "session_id": str(self.session_id),
            "stage": self.stage,
            "round_number": self.round_number,
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "decision": self.decision,
            "reasoning": self.reasoning,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

        # 只在不排除时包含 prompt_input（WebSocket 推送时排除以减少消息大小）
        if not exclude_prompt:
            result["prompt_input"] = self.prompt_input

        return result
