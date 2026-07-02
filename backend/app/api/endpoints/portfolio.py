from typing import Any

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.user import User
from app.portfolio.service import get_portfolio_overview

router = APIRouter()


@router.get("/overview")
async def read_portfolio_overview(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """查询当前用户模拟账户组合概览。

    Args:
        current_user: 当前登录用户。
    Returns:
        当前用户模拟账户的组合结构、行业分布和盈亏排行。
    """
    return await get_portfolio_overview(user=current_user)
