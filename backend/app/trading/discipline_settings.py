from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.ai.market_watch.schemas import MARKET_WATCH_TIME_PATTERN, parse_market_watch_time
from app.crud.system_setting import system_setting
from app.models.system_setting import SystemSetting


MIN_DISCIPLINE_SCAN_INTERVAL_SECONDS = 60
DEFAULT_DISCIPLINE_SCAN_INTERVAL_SECONDS = 300
DEFAULT_DISCIPLINE_SCAN_START_TIME = "09:30"
DEFAULT_DISCIPLINE_SCAN_END_TIME = "15:00"
POSITION_DISCIPLINE_SETTINGS_KEY = "position_discipline.settings"
POSITION_DISCIPLINE_SETTINGS_DESCRIPTION = "Per-user stop-loss/take-profit discipline scan settings"


class PositionDisciplineSettingsResponse(BaseModel):
    """用户止损止盈扫描任务设置。"""

    user_id: int
    enabled: bool = True
    scan_interval_seconds: int = Field(
        DEFAULT_DISCIPLINE_SCAN_INTERVAL_SECONDS,
        ge=MIN_DISCIPLINE_SCAN_INTERVAL_SECONDS,
        le=3600,
    )
    scan_non_trading_days: bool = False
    scan_start_time: str = Field(DEFAULT_DISCIPLINE_SCAN_START_TIME, pattern=MARKET_WATCH_TIME_PATTERN)
    scan_end_time: str = Field(DEFAULT_DISCIPLINE_SCAN_END_TIME, pattern=MARKET_WATCH_TIME_PATTERN)
    auto_launch_debate: bool = True
    cooldown_minutes: int = Field(60, ge=0, le=1440)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_scan_time_window(self) -> "PositionDisciplineSettingsResponse":
        if parse_market_watch_time(self.scan_start_time) >= parse_market_watch_time(self.scan_end_time):
            raise ValueError("scan_start_time must be earlier than scan_end_time")
        return self


class PositionDisciplineSettingsUpdate(BaseModel):
    """用户止损止盈扫描任务设置更新。"""

    enabled: bool | None = None
    scan_interval_seconds: int | None = Field(None, ge=MIN_DISCIPLINE_SCAN_INTERVAL_SECONDS, le=3600)
    scan_non_trading_days: bool | None = None
    scan_start_time: str | None = Field(None, pattern=MARKET_WATCH_TIME_PATTERN)
    scan_end_time: str | None = Field(None, pattern=MARKET_WATCH_TIME_PATTERN)
    auto_launch_debate: bool | None = None
    cooldown_minutes: int | None = Field(None, ge=0, le=1440)

    @field_validator("scan_start_time", "scan_end_time", mode="before")
    @classmethod
    def _strip_time(cls, value: str | None) -> str | None:
        """清理时间字段空白字符。

        Args:
            value: 表单提交的时间字符串。

        Returns:
            去除空白后的时间字符串。
        """
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_scan_time_window(self) -> "PositionDisciplineSettingsUpdate":
        if self.scan_start_time is None or self.scan_end_time is None:
            return self
        if parse_market_watch_time(self.scan_start_time) >= parse_market_watch_time(self.scan_end_time):
            raise ValueError("scan_start_time must be earlier than scan_end_time")
        return self


def _materialize_position_discipline_settings(
    user_id: int,
    row: SystemSetting | None,
) -> PositionDisciplineSettingsResponse:
    """读取持久化设置并补齐默认值。

    Args:
        user_id: 当前用户 ID。
        row: system_settings 记录。

    Returns:
        完整的止损止盈扫描设置。
    """
    data = PositionDisciplineSettingsResponse(user_id=user_id).model_dump()
    if row is not None and isinstance(row.value, dict):
        data.update(row.value)
        data["created_at"] = row.created_at
        data["updated_at"] = row.updated_at
    data["user_id"] = user_id
    return PositionDisciplineSettingsResponse(**data)


async def get_position_discipline_settings(user_id: int) -> PositionDisciplineSettingsResponse:
    """获取用户止损止盈扫描设置。

    Args:
        user_id: 当前用户 ID。

    Returns:
        完整设置；未配置时返回默认值。
    """
    row = await system_setting.get_by_key(POSITION_DISCIPLINE_SETTINGS_KEY, user_id=user_id)
    return _materialize_position_discipline_settings(user_id, row)


async def upsert_position_discipline_settings(
    user_id: int,
    update: PositionDisciplineSettingsUpdate,
) -> PositionDisciplineSettingsResponse:
    """保存用户止损止盈扫描设置。

    Args:
        user_id: 当前用户 ID。
        update: 部分设置更新。

    Returns:
        合并后的完整设置。
    """
    existing_row = await system_setting.get_by_key(POSITION_DISCIPLINE_SETTINGS_KEY, user_id=user_id)
    existing = _materialize_position_discipline_settings(user_id, existing_row)
    data = existing.model_dump()
    data.update(update.model_dump(exclude_none=True, exclude_unset=True))
    merged = PositionDisciplineSettingsResponse(**data)
    value = merged.model_dump(mode="json", exclude={"created_at", "updated_at"})
    row = await system_setting.set_value(
        key=POSITION_DISCIPLINE_SETTINGS_KEY,
        value=value,
        description=POSITION_DISCIPLINE_SETTINGS_DESCRIPTION,
        user_id=user_id,
    )
    return PositionDisciplineSettingsResponse(**(value | {"created_at": row.created_at, "updated_at": row.updated_at}))
