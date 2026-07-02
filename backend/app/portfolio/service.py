from __future__ import annotations

from typing import Any

from app.core import database as database_module
from app.api.ownership import ensure_user_account
from app.models.user import User
from app.portfolio.valuation import build_portfolio_overview_payload, build_portfolio_valuation


async def get_portfolio_overview(*, user: User) -> dict[str, Any]:
    """聚合当前用户模拟账户的组合概览。

    Args:
        user: 当前登录用户。

    Returns:
        包含账户摘要、单股权重、行业分布、风险指标和盈亏排行的组合概览。
    """
    async with database_module.AsyncSessionLocal() as db:
        account = await ensure_user_account(db, user)
        valuation = await build_portfolio_valuation(account)
        return build_portfolio_overview_payload(valuation)
