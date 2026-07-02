from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class InteractiveResearchRunCreate(BaseModel):
    """聊天式 Deep Research run 创建请求。"""

    requirement: str = Field(..., min_length=1, max_length=20000)
    scope: str = Field(default="core", pattern="^(warehouse|core|all)$")
    research_depth: str = Field(default="standard", pattern="^(light|standard|deep)$")
    expected_count: int = Field(default=5, ge=1, le=8)
    risk_level: str = Field(default="medium", pattern="^(low|medium|high)$")
    style: Optional[str] = Field(default=None, pattern="^(balanced|momentum|value|growth|defensive)$")
    allowed_industries: List[str] = Field(default_factory=list)
    excluded_industries: List[str] = Field(default_factory=list)
    exclude_recent_ipos: bool = False
    min_listing_days: Optional[int] = Field(default=None, ge=1, le=5000)
    max_iterations: int = Field(default=60, ge=10)


class InteractiveResearchMessageCreate(BaseModel):
    """聊天消息追加请求。"""

    content: str = Field(..., min_length=1, max_length=20000)
    payload: Dict[str, Any] = Field(default_factory=dict)


class InteractiveResearchActionRequest(BaseModel):
    """run 动作请求。"""

    action: str = Field(..., pattern="^(approve|cancel)$")
    content: Optional[str] = Field(default=None, max_length=20000)
    payload: Dict[str, Any] = Field(default_factory=dict)


class InteractiveResearchRunSummary(BaseModel):
    """run 摘要响应。"""

    run_id: UUID
    user_id: int
    status: str
    current_stage: str
    current_phase: str
    title: str
    raw_requirement: str
    pending_message_id: Optional[UUID] = None
    checkpoint_payload: Dict[str, Any] = Field(default_factory=dict)
    cache_context_version: str
    version: int
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    finished_at: Optional[datetime] = None


class InteractiveResearchMessageResponse(BaseModel):
    """聊天消息响应。"""

    message_id: UUID
    run_id: UUID
    role: str
    message_type: str
    content: str
    display_type: str
    markdown: str
    execution_status: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    parent_message_id: Optional[UUID] = None
    sequence_no: int
    status: str
    visible_to_user: bool
    created_at: datetime


class InteractiveResearchRunResponse(BaseModel):
    """run 操作响应。"""

    run: InteractiveResearchRunSummary
    messages: List[InteractiveResearchMessageResponse] = Field(default_factory=list)


class InteractiveResearchMessageAppendResponse(BaseModel):
    """消息追加响应。"""

    run: InteractiveResearchRunSummary
    message: InteractiveResearchMessageResponse


class InteractiveResearchActionResponse(BaseModel):
    """动作执行响应。"""

    run: InteractiveResearchRunSummary
    messages: List[InteractiveResearchMessageResponse] = Field(default_factory=list)
