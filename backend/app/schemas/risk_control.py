from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.risk_control.service import DEFAULT_RULE_POLICIES


class RiskControlRulePolicy(str, Enum):
    """组合风控规则执行策略。"""

    off = "off"
    block = "block"


class RiskControlConfigUpdate(BaseModel):
    """组合风控配置更新请求。"""

    enabled: bool | None = None
    max_single_position_pct: float | None = Field(default=None, ge=0, le=1)
    max_industry_position_pct: float | None = Field(default=None, ge=0, le=1)
    min_cash_pct: float | None = Field(default=None, ge=0, le=1)
    require_stop_loss: bool | None = None
    stop_loss_warning_pct: float | None = Field(default=None, ge=0, le=1)
    rule_policies: dict[str, RiskControlRulePolicy] | None = None

    @field_validator("rule_policies")
    @classmethod
    def _validate_rule_policy_keys(
        cls,
        value: dict[str, RiskControlRulePolicy] | None,
    ) -> dict[str, RiskControlRulePolicy] | None:
        """校验规则策略只包含系统支持的规则。

        Args:
            value: 规则策略映射。

        Returns:
            原始规则策略映射。

        Raises:
            ValueError: 传入未知规则编码时抛出。
        """
        if value is None:
            return value
        invalid_rules = sorted(set(value) - set(DEFAULT_RULE_POLICIES))
        if invalid_rules:
            raise ValueError(f"unsupported risk-control rules: {', '.join(invalid_rules)}")
        return value


class RiskControlOrderRequest(BaseModel):
    """组合风控订单评估请求。"""

    stock_code: str = Field(..., min_length=1, max_length=20)
    action: Literal["buy", "sell"]
    shares: int = Field(..., gt=0)
    price: float = Field(default=0, ge=0)
    order_type: Literal["market", "limit"] = "market"
    stop_loss: float | None = Field(default=None, gt=0)
