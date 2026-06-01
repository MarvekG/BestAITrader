from datetime import datetime
from typing import Any, ClassVar, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StockWarehouseBase(BaseModel):
    stock_code: str = Field(..., min_length=1, max_length=20)
    stock_name: Optional[str] = None
    is_active: Optional[bool] = True
    is_default: Optional[bool] = False
    auto_analysis_enabled: Optional[bool] = False
    auto_analysis_frequency: Optional[str] = Field("daily", pattern="^(daily|weekly|monthly)$")
    auto_analysis_time: Optional[str] = Field("09:35", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    auto_analysis_trading_frequency: Optional[str] = Field("中长线持有 (Position Trading)", max_length=50)
    auto_analysis_trading_strategy: Optional[str] = Field("价值投资 (Value Investing)", max_length=50)
    auto_analysis_run_immediately: Optional[bool] = False


class StockWarehouseCreate(StockWarehouseBase):
    pass


class StockWarehouseUpdate(BaseModel):
    non_nullable_auto_analysis_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "auto_analysis_enabled",
            "auto_analysis_frequency",
            "auto_analysis_time",
            "auto_analysis_trading_frequency",
            "auto_analysis_trading_strategy",
            "auto_analysis_run_immediately",
        }
    )

    is_active: Optional[bool] = None
    is_default: Optional[bool] = None
    auto_analysis_enabled: Optional[bool] = None
    auto_analysis_frequency: Optional[str] = Field(None, pattern="^(daily|weekly|monthly)$")
    auto_analysis_time: Optional[str] = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    auto_analysis_trading_frequency: Optional[str] = Field(None, min_length=1, max_length=50)
    auto_analysis_trading_strategy: Optional[str] = Field(None, min_length=1, max_length=50)
    auto_analysis_run_immediately: Optional[bool] = None

    @model_validator(mode="before")
    @classmethod
    def _reject_null_auto_analysis_values(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        null_fields = sorted(
            field
            for field in cls.non_nullable_auto_analysis_fields
            if field in data and data[field] is None
        )
        if null_fields:
            raise ValueError(f"auto-analysis fields cannot be null: {', '.join(null_fields)}")
        return data


class StockWarehouse(StockWarehouseBase):
    id: int
    added_at: datetime
    last_auto_analysis_at: Optional[datetime] = None
    last_auto_analysis_session_id: Optional[str] = None
    last_auto_analysis_task_id: Optional[str] = None
    last_auto_analysis_error: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class StockWarehouseResponse(StockWarehouse):
    pass
