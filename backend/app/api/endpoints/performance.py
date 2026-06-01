from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.performance.service import DEFAULT_BENCHMARK_CODE, get_equity_curve, get_latest_performance_summary

router = APIRouter()


@router.get("/summary")
def read_performance_summary(
    benchmark_code: str = Query(DEFAULT_BENCHMARK_CODE),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询当前用户模拟账户绩效摘要。

    Args:
        benchmark_code: 基准指数代码。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        当前用户最新绩效摘要。
    """
    return get_latest_performance_summary(db, user_id=current_user.id, benchmark_code=benchmark_code)


@router.get("/equity-curve")
def read_equity_curve(
    benchmark_code: str = Query(DEFAULT_BENCHMARK_CODE),
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """查询当前用户模拟账户净值曲线。

    Args:
        benchmark_code: 基准指数代码。
        start_date: 查询开始日期。
        end_date: 查询结束日期。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        当前用户净值曲线和基准曲线。
    """
    return get_equity_curve(
        db,
        user_id=current_user.id,
        benchmark_code=benchmark_code,
        start_date=start_date,
        end_date=end_date,
    )
