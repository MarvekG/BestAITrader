"""
LLM 使用量 Schema
"""
from typing import Optional, Dict, Any
from pydantic import BaseModel, UUID4, ConfigDict
from datetime import datetime


class LLMUsageLogSchema(BaseModel):
    """单条使用记录"""
    id: UUID4
    model: str
    role: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    workflow: Optional[str] = None
    stage: Optional[str] = None
    call_kind: Optional[str] = None
    iteration_index: Optional[int] = None
    cache_lane: Optional[str] = None
    api_key_alias: Optional[str] = None
    session_id: Optional[UUID4] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LLMUsageStatsSchema(BaseModel):
    """汇总统计结果"""
    total_calls: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int
    cached_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    cache_hit_rate: float = 0.0
    by_role: Dict[str, int]
    by_role_detail: Dict[str, Any] | None = None
    by_workflow: Dict[str, Any] | None = None
    by_stage: Dict[str, Any] | None = None
    by_workflow_stage: Dict[str, Any] | None = None
    by_workflow_call_kind: Dict[str, Any] | None = None
    by_call_kind: Dict[str, Any] | None = None
    by_cache_lane: Dict[str, Any] | None = None
    by_api_key_alias: Dict[str, Any] | None = None
    backend: Dict[str, Any] | None = None
    memory: Dict[str, Any] | None = None
    combined: Dict[str, Any] | None = None
