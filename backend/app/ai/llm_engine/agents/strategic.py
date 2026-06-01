from typing import Type
from pydantic import BaseModel
from app.ai.llm_engine.agents.base import BaseAgent
from app.ai.llm_engine.prompts import templates
from app.ai.llm_engine.roles import (
    AGENT_NAME_AGGRESSIVE_ANALYST,
    AGENT_NAME_BEARISH_RESEARCHER,
    AGENT_NAME_BULLISH_RESEARCHER,
    AGENT_NAME_CONSERVATIVE_ANALYST,
    AGENT_NAME_NEUTRAL_ANALYST,
)

class BullAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_BULLISH_RESEARCHER, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "BULL",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )
    
    def get_output_model(self) -> Type[str]:
        return str

class BearAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_BEARISH_RESEARCHER, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "BEAR",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )
    
    def get_output_model(self) -> Type[str]:
        return str

class AggressiveAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_AGGRESSIVE_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "AGGRESSIVE",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )
    
    def get_output_model(self) -> Type[str]:
        return str

class ConservativeAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_CONSERVATIVE_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "CONSERVATIVE",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )
    
    def get_output_model(self) -> Type[str]:
        return str

class NeutralAgent(BaseAgent):
    def __init__(self, model_name: str = None, **kwargs):
        super().__init__(role_name=AGENT_NAME_NEUTRAL_ANALYST, model_name=model_name, **kwargs)

    async def get_system_prompt(self, trading_frequency: str, trading_strategy: str) -> str:
        return templates.get_prompt(
            "NEUTRAL",
            trading_frequency=trading_frequency,
            trading_strategy=trading_strategy,
        )
    
    def get_output_model(self) -> Type[str]:
        return str
