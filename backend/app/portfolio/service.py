from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crud.account import ensure_user_account
from app.models.user import User
from app.portfolio.valuation import build_portfolio_overview_payload, build_portfolio_valuation


def get_portfolio_overview(db: Session, *, user: User) -> dict[str, Any]:
    """聚合当前用户模拟账户的组合概览。

    Args:
        db: 数据库会话。
        user: 当前登录用户。

    Returns:
        包含账户摘要、单股权重、行业分布、风险指标和盈亏排行的组合概览。
    """
    account = ensure_user_account(db, user)
    valuation = build_portfolio_valuation(db, account)
    return build_portfolio_overview_payload(valuation)
