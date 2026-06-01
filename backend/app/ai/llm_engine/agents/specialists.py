from typing import Type
from app.ai.llm_engine.agents.base import BaseAgent
from app.ai.llm_engine.prompts import templates
from app.ai.llm_engine.roles import (
    AGENT_NAME_CAPITAL_FLOW_ANALYST,
    AGENT_NAME_FUNDAMENTAL_ANALYST,
    AGENT_NAME_NEWS_ANALYST,
    AGENT_NAME_POLICY_ANALYST,
    AGENT_NAME_RISK_CONTROL_ANALYST,
    AGENT_NAME_SENTIMENT_ANALYST,
    AGENT_NAME_TECHNICAL_ANALYST,
)
from app.core.logger import get_logger

logger = get_logger(__name__)


class FundamentalAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_FUNDAMENTAL_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "FUNDAMENTAL",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[str]:
        return str


class TechnicalAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_TECHNICAL_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "TECHNICAL",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[str]:
        return str


class CapitalFlowAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_CAPITAL_FLOW_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "CAPITAL_FLOW",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[str]:
        return str


class SentimentAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_SENTIMENT_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "SENTIMENT",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[str]:
        return str


class RiskAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_RISK_CONTROL_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "RISK_CONTROL",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[str]:
        return str


class NewsAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_NEWS_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "NEWS_ANALYST",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[str]:
        return str


class PolicyAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_POLICY_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "POLICY_ANALYST",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )

    def get_output_model(self) -> Type[str]:
        return str
