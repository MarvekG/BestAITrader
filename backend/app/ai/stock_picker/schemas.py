from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class StockPickerRunCreate(BaseModel):
    scope: str = Field(..., pattern="^(warehouse|core|all)$")
    style: str = Field(..., pattern="^(balanced|momentum|value|growth|defensive)$")
    recommendation_count: int = Field(default=5, ge=4, le=8)
    risk_level: str = Field(default="medium", pattern="^(low|medium|high)$")
    factor_candidate_limit: Optional[int] = Field(default=None, ge=4)
    research_candidate_limit: Optional[int] = Field(default=None, ge=4)
    allowed_industries: List[str] = Field(default_factory=list)
    same_industry_limit: Optional[int] = Field(default=None, ge=1)


class StockPickerRunResponse(BaseModel):
    run_id: UUID
    status: str
    message: str


class StockPickerRunSummary(BaseModel):
    run_id: UUID
    scope: str
    style: str
    risk_level: str
    recommendation_count: int
    factor_candidate_limit: int
    research_candidate_limit: int
    allowed_industries: List[str]
    same_industry_limit: int
    status: str
    current_stage: str
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime
    summary_payload: Optional[Dict[str, Any]] = None


class StockPickerEventResponse(BaseModel):
    id: int
    run_id: UUID
    stage: str
    event_type: str
    message: str
    payload: Optional[Dict[str, Any]] = None
    created_at: datetime


class StockPickerRecommendationResponse(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    rank: int
    conviction_score: float
    recommendation_reason: str
    risk_flags: List[str]
    holding_horizon: str
    decision: str


class StockPickerQuantSupportResponse(BaseModel):
    style_fit_score: float
    liquidity_score: float
    risk_penalty: float
    final_quant_score: float


class StockPickerCandidateResponse(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    industry: Optional[str] = None
    market: Optional[str] = None
    factor_score: float
    ai_score: float
    final_score: float
    quant_support: Optional[StockPickerQuantSupportResponse] = None
    decision: str
    eliminated_stage: Optional[str] = None
    eliminated_reason: Optional[str] = None
    research_payload: Optional[Dict[str, Any]] = None


class StockPickerRecommendationsPayload(BaseModel):
    stocks: List[StockPickerRecommendationResponse]
    recommendation_logic: str
    style: str
    scope: str
    generated_at: Optional[datetime] = None


class StockPickerResultResponse(BaseModel):
    run: StockPickerRunSummary
    summary: Dict[str, Any]
    recommendations: StockPickerRecommendationsPayload
    alternatives: List[StockPickerCandidateResponse]
    risk_summary: Dict[str, Any]
