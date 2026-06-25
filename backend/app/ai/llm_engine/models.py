from enum import Enum
from typing import List

from pydantic import BaseModel, Field
from app.ai.llm_engine.roles import (
    AGENT_ROLE_AGGRESSIVE,
    AGENT_ROLE_BEAR,
    AGENT_ROLE_BULL,
    AGENT_ROLE_CAPITAL_FLOW,
    AGENT_ROLE_CONSERVATIVE,
    AGENT_ROLE_FUNDAMENTAL,
    AGENT_ROLE_NEUTRAL,
    AGENT_ROLE_PORTFOLIO_MANAGER,
    AGENT_ROLE_SENTIMENT,
    AGENT_ROLE_TECHNICAL,
)


class AnalystSignal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class AnalystRole(str, Enum):
    # Layer 1: Vertical
    FUNDAMENTAL = AGENT_ROLE_FUNDAMENTAL
    TECHNICAL = AGENT_ROLE_TECHNICAL
    CAPITAL_FLOW = AGENT_ROLE_CAPITAL_FLOW
    SENTIMENT = AGENT_ROLE_SENTIMENT
    RISK_CONTROL = "risk_control"

    # Layer 2: Strategic
    BULL = AGENT_ROLE_BULL
    BEAR = AGENT_ROLE_BEAR
    AGGRESSIVE = AGENT_ROLE_AGGRESSIVE
    CONSERVATIVE = AGENT_ROLE_CONSERVATIVE
    NEUTRAL = AGENT_ROLE_NEUTRAL

    # Decision & Execution
    PORTFOLIO_MANAGER = AGENT_ROLE_PORTFOLIO_MANAGER


class AnalystOutput(BaseModel):
    """Standard output format for Level 1 Analysts"""
    score: float = Field(..., ge=0, le=100, description="Confidence score from 0 to 100")
    signal: AnalystSignal = Field(..., description="Trading signal")
    analysis_summary: str = Field(..., description="One sentence summary of the analysis")
    key_factors: List[str] = Field(..., description="List of key factors influencing the decision")
    detailed_reasoning: str = Field(..., description="Detailed reasoning for the analysis")


class StrategicReport(BaseModel):
    """Output format for Strategic Analysts (Markdown)"""
    markdown_content: str = Field(..., description="The full markdown report following the specified template.")
