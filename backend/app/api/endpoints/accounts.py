from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any, Optional
from uuid import UUID
from decimal import Decimal, InvalidOperation

from app.core.database import get_async_db
from app.crud.account import DEFAULT_ACCOUNT_CAPITAL
from app.models.account import Account
from app.models.position import Position
from app.models.session import Session as AnalysisSession
from app.models.user import User
from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.trading.trading_engine import TradingEngine
from app.core.security import get_current_user
from app.api.ownership import ensure_user_account

router = APIRouter()
trading_engine = TradingEngine()


def _extract_position_stop_loss(position: Position) -> Optional[Decimal]:
    purchase_details = position.purchase_details or {}
    if not isinstance(purchase_details, dict):
        return None

    stop_loss = purchase_details.get("stop_loss")
    if stop_loss in (None, ""):
        return None

    try:
        return Decimal(str(stop_loss))
    except (ValueError, TypeError, InvalidOperation):
        return None


def _resolve_position_share_fields(position: Position) -> tuple[int, int]:
    snapshot = trading_engine.build_position_snapshot(position)
    return snapshot["available_shares"], snapshot["frozen_shares"]


async def _get_owned_position(db: AsyncSession, position_id: UUID, current_user: User) -> Position:
    """读取当前用户拥有的持仓；不存在则返回 404。"""
    account = await ensure_user_account(db, current_user)
    result = await db.execute(
        select(Position).where(
            Position.position_id == position_id,
            Position.account_id == account.account_id,
        )
    )
    position = result.scalar_one_or_none()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


async def _build_portfolio_valuation(db: AsyncSession, account: Account) -> dict[str, Any]:
    """按账户有效持仓和最近有效行情构建端点所需估值数据。"""
    latest_market = (
        select(
            StockRealtimeMarket.stock_code,
            StockRealtimeMarket.current_price,
            func.row_number().over(
                partition_by=StockRealtimeMarket.stock_code,
                order_by=StockRealtimeMarket.timestamp.desc(),
            ).label("rn"),
        )
        .where(StockRealtimeMarket.current_price.isnot(None), StockRealtimeMarket.current_price > 0)
        .subquery()
    )
    result = await db.execute(
        select(Position, StockBasic.name, latest_market.c.current_price)
        .outerjoin(StockBasic, Position.stock_code == StockBasic.stock_code)
        .outerjoin(
            latest_market,
            (Position.stock_code == latest_market.c.stock_code) & (latest_market.c.rn == 1),
        )
        .where(Position.account_id == account.account_id, Position.total_shares > 0)
    )

    positions = []
    market_value = Decimal("0.0000")
    for position, stock_name, market_price in result.all():
        shares = int(position.total_shares or 0)
        current_price = Decimal("0")
        for candidate in (market_price, position.current_price, position.avg_cost):
            try:
                candidate_price = Decimal(str(candidate)) if candidate is not None else Decimal("0")
            except (ValueError, TypeError, InvalidOperation):
                candidate_price = Decimal("0")
            if candidate_price > 0:
                current_price = candidate_price
                break
        current_market_value = current_price * Decimal(shares)
        unrealized_pnl = current_market_value - (position.avg_cost * Decimal(shares))
        available_shares, frozen_shares = _resolve_position_share_fields(position)
        market_value += current_market_value
        positions.append({
            "position_id": str(position.position_id),
            "session_id": str(position.session_id) if position.session_id else None,
            "stock_code": position.stock_code,
            "stock_name": stock_name or "Unknown",
            "total_shares": shares,
            "available_shares": available_shares,
            "frozen_shares": frozen_shares,
            "avg_cost": float(position.avg_cost),
            "current_price": float(current_price),
            "market_value": float(current_market_value),
            "market_value_decimal": current_market_value,
            "unrealized_pnl": float(unrealized_pnl),
            "unrealized_pnl_decimal": unrealized_pnl,
            "stop_loss": _extract_position_stop_loss(position),
            "updated_at": position.updated_at.isoformat() if position.updated_at else None,
        })

    available_cash = account.available_cash or Decimal("0.00")
    frozen_cash = account.frozen_cash or Decimal("0.00")
    total_assets = available_cash + frozen_cash + market_value
    return {
        "summary": {
            "market_value_decimal": market_value,
            "total_assets_decimal": total_assets,
            "available_cash_decimal": available_cash,
            "frozen_cash_decimal": frozen_cash,
        },
        "positions": positions,
    }

# ==================== 基于用户的账户 API（推荐使用）====================


@router.get("/my-assets", response_model=Dict[str, Any])
async def get_my_account_assets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取当前用户的账户资产（全局资金池）
    推荐使用此端点，而不是基于 session_id 的端点
    """
    try:
        account = await ensure_user_account(db, current_user)

        valuation = await _build_portfolio_valuation(db, account)
        total_market_value = valuation["summary"]["market_value_decimal"]
        total_assets = valuation["summary"]["total_assets_decimal"]
        floating_pnl = sum(
            (position["unrealized_pnl_decimal"] for position in valuation["positions"]),
            Decimal("0.00"),
        )
        # Use persisted initial_capital as starting capital
        starting_capital = account.initial_capital or account.total_assets
        
        # Calculate total profit loss percentage based on starting capital
        # Formula: ((realized_pnl + floating_pnl) / starting_capital) * 100
        if starting_capital and starting_capital > 0:
            total_pl = (account.total_profit_loss or Decimal("0.00")) + floating_pnl
            profit_loss_pct = (total_pl / starting_capital) * 100
        else:
            profit_loss_pct = Decimal("0.00")

        account_assets = {
            "id": str(account.account_id),
            "user_id": current_user.id,
            "cash_balance": account.available_cash,
            "market_value": total_market_value,
            "total_assets": total_assets,
            "frozen_cash": account.frozen_cash,
            "total_profit_loss": account.total_profit_loss,
            "floating_pnl": floating_pnl,
            "starting_capital": starting_capital,
            "profit_loss_pct": profit_loss_pct,
            "total_trades": account.total_trades,
            "win_rate": account.win_rate,
            "created_at": account.updated_at.isoformat(),
            "updated_at": account.updated_at.isoformat()
        }

        return account_assets
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-total-funds", response_model=Dict[str, Any])
async def get_my_total_funds(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取当前用户的总资金（全局资金池）
    推荐使用此端点
    """
    try:
        account = await ensure_user_account(db, current_user)
        valuation = await _build_portfolio_valuation(db, account)
        summary = valuation["summary"]

        return {
            "user_id": current_user.id,
            "total_funds": summary["total_assets_decimal"],
            "cash_balance": summary["available_cash_decimal"],
            "frozen_cash": summary["frozen_cash_decimal"],
            "market_value": summary["market_value_decimal"],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/my-total-funds", response_model=Dict[str, Any])
async def set_my_total_funds(
    total_funds: float = Body(..., embed=True, description="Total funds amount"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    设置当前用户的总资金（全局资金池）
    推荐使用此端点
    """
    try:
        account = await ensure_user_account(db, current_user)
        total_funds_decimal = Decimal(str(total_funds))
        valuation = await _build_portfolio_valuation(db, account)
        market_value = valuation["summary"]["market_value_decimal"]
        frozen_cash = account.frozen_cash or Decimal("0.00")
        available_cash = total_funds_decimal - market_value - frozen_cash
        if available_cash < 0:
            raise HTTPException(
                status_code=400,
                detail="Total funds cannot be less than market value plus frozen cash",
            )

        # Calculate funds change
        funds_change = float(total_funds_decimal - (account.total_assets or Decimal("0.00")))

        # 更新账户总额和初始资金 (Sync initial_capital)
        account.total_assets = total_funds_decimal
        account.initial_capital = account.total_assets
        account.available_cash = available_cash
        account.market_value = market_value

        await db.commit()
        await db.refresh(account)

        return {
            "user_id": current_user.id,
            "total_funds": account.total_assets,
            "cash_balance": account.available_cash,
            "frozen_cash": account.frozen_cash,
            "market_value": market_value,
            "funds_change": funds_change
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/my-positions", response_model=List[Dict[str, Any]])
async def get_my_positions(
    stock_code: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    获取当前用户的全局持仓（不区分 session_id）
    """
    try:
        account = await ensure_user_account(db, current_user)

        valuation = await _build_portfolio_valuation(db, account)
        positions = [
            position
            for position in valuation["positions"]
            if stock_code is None or position["stock_code"] == stock_code
        ]
        positions_list = []
        for position in positions:
            positions_list.append({
                "id": position["position_id"],
                "session_id": position["session_id"],
                "stock_code": position["stock_code"],
                "stock_name": position["stock_name"],
                "current_shares": position["total_shares"],
                "available_shares": position["available_shares"],
                "frozen_shares": position["frozen_shares"],
                "avg_cost": position["avg_cost"],
                "current_price": position["current_price"],
                "stop_loss": position["stop_loss"],
                "market_value": position["market_value"],
                "unrealized_pnl": round(position["unrealized_pnl"], 2),
                "created_at": position["updated_at"],
                "updated_at": position["updated_at"]
            })

        return positions_list
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/reset-account", response_model=Dict[str, Any])
async def reset_my_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    """
    重置当前用户的账户状态：清空持仓、订单、交易记录，并恢复初始资金。
    """
    try:
        account_result = await db.execute(select(Account).where(Account.user_id == current_user.id))
        account = account_result.scalar_one_or_none()

        # 利用级联删除 (CASCADE) 清理所有关联表 (Position, Order, TradeRecord)
        if account:
            await db.delete(account)
            await db.commit()

        # 重新创建初始账户 (100万)
        initial_funds = DEFAULT_ACCOUNT_CAPITAL
        new_account = Account(
            user_id=current_user.id,
            total_assets=initial_funds,
            initial_capital=initial_funds,
            available_cash=initial_funds,
            frozen_cash=Decimal("0.00"),
            market_value=Decimal("0.00"),
            total_profit_loss=Decimal("0.00"),
            profit_loss_pct=Decimal("0.00"),
            total_trades=0,
            win_rate=Decimal("0.00")
        )
        db.add(new_account)
        await db.commit()

        return {
            "success": True,
            "message": "Account has been reset successfully using cascade delete",
            "cash_balance": float(initial_funds)
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions/{session_id}", response_model=List[Dict[str, Any]])
async def get_positions(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    获取指定会话下的有效持仓列表，并使用最近有效行情价动态估值。

    Args:
        session_id: 交易会话 ID。
        current_user: 当前登录用户。
        db: 数据库会话。

    Returns:
        持仓列表，每条包含股份数量、可用股份、最新价、市值和浮动盈亏。

    Raises:
        HTTPException: 会话不属于当前用户，或读取持仓失败。
    """
    try:
        session_result = await db.execute(
            select(AnalysisSession).where(
                AnalysisSession.session_id == session_id,
                AnalysisSession.user_id == current_user.id,
            )
        )
        if not session_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Session not found")
        account = await ensure_user_account(db, current_user)
        # 使用 Position.session_id 过滤
        # 获取最新价格的子查询
        latest_market_subquery = select(
            StockRealtimeMarket.stock_code,
            StockRealtimeMarket.current_price,
            func.row_number().over(
                partition_by=StockRealtimeMarket.stock_code,
                order_by=StockRealtimeMarket.timestamp.desc()
            ).label("rn")
        ).where(
            StockRealtimeMarket.current_price.isnot(None),
            StockRealtimeMarket.current_price > 0,
        ).subquery()

        result = await db.execute(select(Position, StockBasic.name, latest_market_subquery.c.current_price).outerjoin(
            StockBasic, Position.stock_code == StockBasic.stock_code
        ).outerjoin(
            latest_market_subquery,
            (Position.stock_code == latest_market_subquery.c.stock_code) & (latest_market_subquery.c.rn == 1)
        ).where(
            Position.session_id == session_id,
            Position.account_id == account.account_id,
            Position.total_shares > 0
        ))
        positions = result.all()

        positions_list = []
        for position, stock_name, current_market_price in positions:
            # 动态计算最新浮盈：确保类型一致 (Decimal)
            try:
                if current_market_price is not None:
                    live_price = Decimal(str(current_market_price))
                else:
                    live_price = position.avg_cost
            except (ValueError, TypeError, InvalidOperation):
                live_price = position.avg_cost

            live_unrealized_pnl = (live_price - position.avg_cost) * Decimal(str(position.total_shares))
            stop_loss = _extract_position_stop_loss(position)
            available_shares, frozen_shares = _resolve_position_share_fields(position)

            positions_list.append({
                "id": str(position.position_id),
                "session_id": str(position.session_id),
                "stock_code": position.stock_code,
                "stock_name": stock_name or "Unknown",
                "current_shares": position.total_shares,
                "available_shares": available_shares,
                "frozen_shares": frozen_shares,
                "avg_cost": position.avg_cost,
                "current_price": live_price,
                "stop_loss": stop_loss,
                "market_value": live_price * Decimal(str(position.total_shares)),
                "unrealized_pnl": round(live_unrealized_pnl, 2),
                "created_at": position.updated_at.isoformat(),
                "updated_at": position.updated_at.isoformat()
            })

        return positions_list
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions/single/{position_id}", response_model=Dict[str, Any])
async def get_position(
    position_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """Get single position"""
    try:
        position = await _get_owned_position(db, position_id, current_user)
        stock_name_result = await db.execute(select(StockBasic.name).where(StockBasic.stock_code == position.stock_code))
        stock_name = stock_name_result.scalar_one_or_none()

        available_shares, frozen_shares = _resolve_position_share_fields(position)
        position_info = {
            "id": str(position.position_id),
            "session_id": str(position.session_id),
            "stock_code": position.stock_code,
            "stock_name": stock_name or "Unknown",
            "current_shares": position.total_shares,
            "available_shares": available_shares,
            "frozen_shares": frozen_shares,
            "avg_cost": position.avg_cost,
            "stop_loss": _extract_position_stop_loss(position),
            "market_value": position.market_value,
            "unrealized_pnl": position.profit_loss,
            "created_at": position.updated_at.isoformat(),
            "updated_at": position.updated_at.isoformat()
        }

        return position_info
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
