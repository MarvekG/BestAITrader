from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.crud.account import ensure_user_account
from app.models.user import User
from app.risk_control.service import portfolio_risk_control_service, serialize_config
from app.schemas.risk_control import RiskControlConfigUpdate, RiskControlOrderRequest


router = APIRouter()


@router.get("/config", response_model=dict[str, Any])
async def get_risk_control_config(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    获取当前用户账户的组合风控配置。

    Args:
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        当前账户组合风控配置。
    """
    account = ensure_user_account(db, current_user)
    config = portfolio_risk_control_service.get_or_create_config(db, account)
    return serialize_config(config)


@router.put("/config", response_model=dict[str, Any])
async def update_risk_control_config(
    payload: RiskControlConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    更新当前用户账户的组合风控配置。

    Args:
        payload: 组合风控配置字段。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        更新后的账户组合风控配置。
    """
    account = ensure_user_account(db, current_user)
    config = portfolio_risk_control_service.update_config(
        db,
        account,
        payload.model_dump(exclude_unset=True),
    )
    return serialize_config(config)


@router.post("/evaluate-order", response_model=dict[str, Any])
async def evaluate_risk_control_order(
    payload: RiskControlOrderRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """
    评估订单执行前的组合风控结果。

    Args:
        payload: 订单字段。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        风控评估结果。
    """
    account = ensure_user_account(db, current_user)
    return portfolio_risk_control_service.evaluate_order(
        db,
        account=account,
        stock_code=payload.stock_code,
        action=payload.action,
        shares=payload.shares,
        price=payload.price,
        order_type=payload.order_type,
        stop_loss=payload.stop_loss,
    )
