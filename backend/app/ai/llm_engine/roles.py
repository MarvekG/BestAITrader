from typing import Final

AGENT_NAME_FUNDAMENTAL_ANALYST: Final[str] = "Fundamental Analyst"
AGENT_NAME_TECHNICAL_ANALYST: Final[str] = "Technical Analyst"
AGENT_NAME_CAPITAL_FLOW_ANALYST: Final[str] = "Capital Flow Analyst"
AGENT_NAME_SENTIMENT_ANALYST: Final[str] = "Sentiment Analyst"
AGENT_NAME_RISK_CONTROL_ANALYST: Final[str] = "Risk Control Analyst"
AGENT_NAME_NEWS_ANALYST: Final[str] = "News Analyst"
AGENT_NAME_POLICY_ANALYST: Final[str] = "Policy Analyst"
AGENT_NAME_BULLISH_RESEARCHER: Final[str] = "Bullish Researcher"
AGENT_NAME_BEARISH_RESEARCHER: Final[str] = "Bearish Researcher"
AGENT_NAME_AGGRESSIVE_ANALYST: Final[str] = "Aggressive Analyst"
AGENT_NAME_CONSERVATIVE_ANALYST: Final[str] = "Conservative Analyst"
AGENT_NAME_NEUTRAL_ANALYST: Final[str] = "Neutral Analyst"
AGENT_NAME_PORTFOLIO_MANAGER: Final[str] = "Portfolio Manager"

AGENT_ROLE_FUNDAMENTAL: Final[str] = "fundamental"
AGENT_ROLE_TECHNICAL: Final[str] = "technical"
AGENT_ROLE_CAPITAL_FLOW: Final[str] = "capital_flow"
AGENT_ROLE_SENTIMENT: Final[str] = "sentiment"
AGENT_ROLE_RISK: Final[str] = "risk"
AGENT_ROLE_NEWS_ANALYST: Final[str] = "news_analyst"
AGENT_ROLE_POLICY_ANALYST: Final[str] = "policy_analyst"
AGENT_ROLE_BULL: Final[str] = "bull"
AGENT_ROLE_BEAR: Final[str] = "bear"
AGENT_ROLE_AGGRESSIVE: Final[str] = "aggressive"
AGENT_ROLE_CONSERVATIVE: Final[str] = "conservative"
AGENT_ROLE_NEUTRAL: Final[str] = "neutral"
AGENT_ROLE_PORTFOLIO_MANAGER: Final[str] = "portfolio_manager"

MEMORY_ENABLED_AGENT_NAMES: Final[frozenset[str]] = frozenset(
    {
        AGENT_NAME_RISK_CONTROL_ANALYST,
        AGENT_NAME_BULLISH_RESEARCHER,
        AGENT_NAME_BEARISH_RESEARCHER,
        AGENT_NAME_AGGRESSIVE_ANALYST,
        AGENT_NAME_CONSERVATIVE_ANALYST,
        AGENT_NAME_NEUTRAL_ANALYST,
        AGENT_NAME_PORTFOLIO_MANAGER,
    }
)
