from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.account import Account
from app.risk_control.service import portfolio_risk_control_service, serialize_config


def format_portfolio_risk_control_summary(config: dict[str, Any]) -> dict[str, Any]:
    """构建供 AI 上下文使用的组合风控摘要。

    Args:
        config: 已序列化的组合风控配置。

    Returns:
        包含结构化摘要和自然语言说明的风控上下文。
    """
    enabled = bool(config.get("enabled", False))
    max_single_position_pct = float(config.get("max_single_position_pct", 0))
    max_industry_position_pct = float(config.get("max_industry_position_pct", 0))
    min_cash_pct = float(config.get("min_cash_pct", 0))
    require_stop_loss = bool(config.get("require_stop_loss", False))
    stop_loss_warning_pct = float(config.get("stop_loss_warning_pct", 0))
    rule_policies = dict(config.get("rule_policies") or {})

    text = (
        "Portfolio risk control: "
        f"{'enabled' if enabled else 'disabled'}; "
        f"max single-stock weight {max_single_position_pct:.2%}; "
        f"max industry weight {max_industry_position_pct:.2%}; "
        f"minimum cash ratio {min_cash_pct:.2%}; "
        f"{'buy orders require stop loss' if require_stop_loss else 'buy orders do not require stop loss'}; "
        f"stop-loss warning threshold {stop_loss_warning_pct:.2%}; "
        f"rule policies {rule_policies}."
    )

    return {
        "summary": {
            "enabled": enabled,
            "max_single_position_pct": max_single_position_pct,
            "max_industry_position_pct": max_industry_position_pct,
            "min_cash_pct": min_cash_pct,
            "require_stop_loss": require_stop_loss,
            "stop_loss_warning_pct": stop_loss_warning_pct,
            "rule_policies": rule_policies,
        },
        "text": text,
    }


def build_portfolio_risk_control_context(db: Session, account: Account) -> dict[str, Any]:
    """读取账户风控配置并转换为 AI 上下文。

    Args:
        db: 数据库会话。
        account: 当前用户账户。

    Returns:
        可直接放入 AI 静态上下文的组合风控摘要。
    """
    risk_config = portfolio_risk_control_service.get_or_create_config(db, account)
    return format_portfolio_risk_control_summary(serialize_config(risk_config))
