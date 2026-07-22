from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


ReviewHorizonValue = Literal["5d", "20d", "60d"]


class ExperienceAnalyzeRequest(BaseModel):
    session_id: UUID
    review_horizon: Optional[ReviewHorizonValue] = None


class ExperienceReviewSchedulerConfig(BaseModel):
    enabled: bool = False
    schedule_hour: int = Field(18, ge=0, le=23)
    schedule_minute: int = Field(30, ge=0, le=59)
    candidate_lookback: int = Field(200, ge=1, le=5000)
    max_runs_per_tick: int = Field(2, ge=1, le=20)


class ExperienceDebateSessionResponse(BaseModel):
    session_id: UUID
    stock_code: str
    stock_name: Optional[str] = None
    status: str
    trading_frequency: str
    trading_strategy: str
    created_at: datetime
    updated_at: datetime
    pm_decision: Optional[str] = None
    pm_confidence: Optional[float] = None
    has_experience_review: bool = False


class ExperienceAnalyzeResponse(BaseModel):
    review_run_id: Optional[str] = None
    review_horizon: Optional[ReviewHorizonValue] = None
    market_day_count: Optional[int] = None
    session_id: UUID
    stock_code: str
    stock_name: Optional[str] = None
    industry: Optional[str] = None
    style_bucket: str
    trading_frequency: Optional[str] = None
    trading_strategy: Optional[str] = None
    analysis_date: datetime
    reviewed_at: datetime
    analysis_payload: Dict[str, Any] = Field(default_factory=dict)
    tool_trace: List[Dict[str, Any]] = Field(default_factory=list)


class ExperienceReviewEventResponse(BaseModel):
    event_id: UUID
    review_run_id: str
    session_id: UUID | None = None
    event_type: str
    stage: str
    status: str
    message_key: Optional[str] = None
    message_params: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ExperienceReviewRunResponse(BaseModel):
    review_run_id: str
    review_horizon: Optional[ReviewHorizonValue] = None
    market_day_count: Optional[int] = None
    session_id: UUID
    stock_code: str
    stock_name: Optional[str] = None
    trading_frequency: Optional[str] = None
    trading_strategy: Optional[str] = None
    status: str
    stage: str
    message_key: Optional[str] = None
    message_params: Dict[str, Any] = Field(default_factory=dict)
    recommended_action: Optional[str] = None
    debate_correctness: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ExperienceReviewCandidateResponse(BaseModel):
    session_id: UUID
    stock_code: str
    stock_name: Optional[str] = None
    industry: Optional[str] = None
    status: str
    trading_frequency: Optional[str] = None
    trading_strategy: Optional[str] = None
    pm_decision: Optional[str] = None
    pm_confidence: Optional[float] = None
    pm_created_at: Optional[datetime] = None
    market_day_count: int
    eligible_horizons: List[ReviewHorizonValue] = Field(default_factory=list)
    latest_completed_horizons: List[ReviewHorizonValue] = Field(default_factory=list)
    active_horizons: List[ReviewHorizonValue] = Field(default_factory=list)
    failed_horizons: List[ReviewHorizonValue] = Field(default_factory=list)
    review_status: str
    next_horizon: Optional[ReviewHorizonValue] = None
    days_until_next_horizon: Optional[int] = None


class ExperienceReviewCandidateListResponse(BaseModel):
    items: List[ExperienceReviewCandidateResponse] = Field(default_factory=list)
    summary: Dict[str, int] = Field(default_factory=dict)


class ExperienceLibraryItemResponse(BaseModel):
    id: UUID
    memory_id: Optional[str] = None
    review_run_id: str
    session_id: UUID
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    industry: Optional[str] = None
    strategy: Optional[str] = None
    review_horizon: Optional[ReviewHorizonValue] = None
    outcome_label: Optional[str] = None
    correctness: Optional[str] = None
    importance: Optional[str] = None
    summary: str
    tags: Dict[str, List[str]] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ExperienceLibraryListResponse(BaseModel):
    items: List[ExperienceLibraryItemResponse] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20
    summary: Dict[str, int] = Field(default_factory=dict)


class ExperienceLibraryDetailResponse(ExperienceLibraryItemResponse):
    review_triads: Dict[str, Any] = Field(default_factory=dict)
    market_outcome_summary: Dict[str, Any] = Field(default_factory=dict)
    memory: Dict[str, Any] = Field(default_factory=dict)


class ExperienceLibraryRebuildResponse(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
