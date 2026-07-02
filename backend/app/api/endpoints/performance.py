from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.security import get_current_user
from app.models.user import User
from app.performance.service import DEFAULT_BENCHMARK_CODE, get_equity_curve, get_latest_performance_summary

router = APIRouter()


def _to_float(value: object) -> float | None:
    """将可空数值转换为 float。"""
    if value is None:
        return None
    return float(value)


@router.get("/summary")
async def read_performance_summary(
    benchmark_code: str = Query(DEFAULT_BENCHMARK_CODE),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """查询当前用户模拟账户绩效摘要。

    Args:
        benchmark_code: 基准指数代码。
        current_user: 当前登录用户。

    Returns:
        当前用户最新绩效摘要。
    """
    return await get_latest_performance_summary(user_id=current_user.id, benchmark_code=benchmark_code)


@router.get("/equity-curve")
async def read_equity_curve(
    benchmark_code: str = Query(DEFAULT_BENCHMARK_CODE),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """查询当前用户模拟账户净值曲线。

    Args:
        benchmark_code: 基准指数代码。
        start_date: 查询开始日期。
        end_date: 查询结束日期。
        current_user: 当前登录用户。

    Returns:
        当前用户净值曲线和基准曲线。
    """
    return await get_equity_curve(
        user_id=current_user.id,
        benchmark_code=benchmark_code,
        start_date=start_date,
        end_date=end_date,
    )
