from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.crud.system_setting import system_setting


AI_DEBATE_MAX_CONCURRENT_SETTING_KEY = "ai_debate.max_concurrent"
AI_DEBATE_MAX_CONCURRENT_DEFAULT = 5
AI_DEBATE_MAX_CONCURRENT_DESCRIPTION = "AI research debate max concurrency"


class RuntimeSettings(BaseModel):
    """系统运行参数。"""

    ai_debate_max_concurrent: int = Field(default=AI_DEBATE_MAX_CONCURRENT_DEFAULT, ge=1, le=100)

    @field_validator("ai_debate_max_concurrent", mode="before")
    @classmethod
    def normalize_ai_debate_max_concurrent(cls, value: Any) -> int:
        """将外部输入规范化为可保存的并发上限。

        Args:
            value: API 请求或 system_settings 中读取到的原始值。

        Returns:
            规范化后的正整数并发上限。

        Raises:
            ValueError: 输入无法转换为正整数时抛出。
        """
        if value is None or value == "":
            return AI_DEBATE_MAX_CONCURRENT_DEFAULT
        return int(value)


async def get_runtime_settings() -> RuntimeSettings:
    """读取系统运行参数并补齐默认值。

    Args:
    Returns:
        当前系统运行参数。
    """
    return RuntimeSettings(
        ai_debate_max_concurrent=await system_setting.get_value(
            AI_DEBATE_MAX_CONCURRENT_SETTING_KEY,
            default=AI_DEBATE_MAX_CONCURRENT_DEFAULT,
            user_id=None,
        )
    )


async def update_runtime_settings(payload: RuntimeSettings) -> RuntimeSettings:
    """保存系统运行参数。

    Args:
        payload: 已通过校验的运行参数。

    Returns:
        保存后的系统运行参数。
    """
    await system_setting.set_value(
        AI_DEBATE_MAX_CONCURRENT_SETTING_KEY,
        payload.ai_debate_max_concurrent,
        description=AI_DEBATE_MAX_CONCURRENT_DESCRIPTION,
        user_id=None,
    )
    return await get_runtime_settings()
