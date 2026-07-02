from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select

from app.core import database as database_module
from app.crud.account import DEFAULT_ACCOUNT_CAPITAL, ensure_user_account
from app.models.account import Account
from app.models.order import Order
from app.models.position import Position
from app.models.session import Session as AnalysisSession
from app.models.trade_record import TradeRecord
from app.models.user import User
from app.models.data_storage import StockBasic


async def get_current_user_account(current_user: User) -> Account:
    """返回当前用户交易账户；不存在时创建默认模拟账户。"""
    async with database_module.AsyncSessionLocal() as db:
        return await ensure_user_account(db, current_user)


async def ensure_user_account_by_user_id(
    user_id: int,
    initial_capital: Decimal = DEFAULT_ACCOUNT_CAPITAL,
) -> Account:
    """按用户 ID 获取账户；缺失时创建默认模拟交易账户。"""
    async with database_module.AsyncSessionLocal() as db:
        user = User(id=user_id)
        return await ensure_user_account(db, user, initial_capital)


async def get_owned_session(session_id: UUID, current_user: User) -> AnalysisSession:
    """返回当前用户拥有的投研会话；不存在时抛出 404。"""
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(AnalysisSession).where(
                AnalysisSession.session_id == session_id,
                AnalysisSession.user_id == current_user.id,
            )
        )
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        return session


async def get_owned_order(order_id: UUID, current_user: User) -> Order:
    """返回当前用户拥有的订单；不存在时抛出 404。"""
    async with database_module.AsyncSessionLocal() as db:
        account_result = await db.execute(select(Account).where(Account.user_id == current_user.id))
        account = account_result.scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Order not found")
        order_result = await db.execute(
            select(Order).where(
                Order.order_id == order_id,
                Order.account_id == account.account_id,
            )
        )
        order = order_result.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        return order


async def get_owned_position(position_id: UUID, current_user: User) -> Position:
    """返回当前用户拥有的持仓；不存在时抛出 404。"""
    async with database_module.AsyncSessionLocal() as db:
        account_result = await db.execute(select(Account).where(Account.user_id == current_user.id))
        account = account_result.scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Position not found")
        position_result = await db.execute(
            select(Position).where(
                Position.position_id == position_id,
                Position.account_id == account.account_id,
            )
        )
        position = position_result.scalar_one_or_none()
        if not position:
            raise HTTPException(status_code=404, detail="Position not found")
        return position


async def get_owned_trade_record(trade_id: UUID, current_user: User) -> TradeRecord:
    """返回当前用户拥有的交易记录；不存在时抛出 404。"""
    async with database_module.AsyncSessionLocal() as db:
        account_result = await db.execute(select(Account).where(Account.user_id == current_user.id))
        account = account_result.scalar_one_or_none()
        if not account:
            raise HTTPException(status_code=404, detail="Trade record not found")
        record_result = await db.execute(
            select(TradeRecord).where(
                TradeRecord.trade_id == trade_id,
                TradeRecord.account_id == account.account_id,
            )
        )
        record = record_result.scalar_one_or_none()
        if not record:
            raise HTTPException(status_code=404, detail="Trade record not found")
        return record


async def get_stock_name(stock_code: str) -> str:
    """返回股票名称，缺失时返回 Unknown。"""
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(select(StockBasic.name).where(StockBasic.stock_code == stock_code))
        return result.scalar_one_or_none() or "Unknown"


async def list_owned_session_orders(
    session_id: UUID,
    account_id: UUID,
    *,
    skip: int,
    limit: int,
    status: str | None = None,
) -> list[tuple[Order, str | None]]:
    """返回指定会话和账户下的订单及股票名称。"""
    async with database_module.AsyncSessionLocal() as db:
        stmt = (
            select(Order, StockBasic.name)
            .outerjoin(StockBasic, Order.stock_code == StockBasic.stock_code)
            .where(Order.session_id == session_id, Order.account_id == account_id)
        )
        if status:
            stmt = stmt.where(Order.status == status)
        return list((await db.execute(stmt.order_by(Order.created_at.desc()).offset(skip).limit(limit))).all())


async def list_owned_session_trades(
    session_id: UUID,
    account_id: UUID,
    *,
    skip: int,
    limit: int,
) -> list[tuple[TradeRecord, str | None]]:
    """返回指定会话和账户下的交易记录及股票名称。"""
    async with database_module.AsyncSessionLocal() as db:
        results = await db.execute(
            select(TradeRecord, StockBasic.name)
            .outerjoin(StockBasic, TradeRecord.stock_code == StockBasic.stock_code)
            .where(TradeRecord.session_id == session_id, TradeRecord.account_id == account_id)
            .order_by(TradeRecord.trade_time.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(results.all())


async def list_owned_orders(
    account_id: UUID,
    *,
    skip: int,
    limit: int,
    status: str | None = None,
    stock_code: str | None = None,
) -> list[tuple[Order, str | None]]:
    """返回账户全局订单及股票名称。"""
    async with database_module.AsyncSessionLocal() as db:
        stmt = (
            select(Order, StockBasic.name)
            .outerjoin(StockBasic, Order.stock_code == StockBasic.stock_code)
            .where(Order.account_id == account_id)
        )
        if status:
            stmt = stmt.where(Order.status == status)
        if stock_code:
            stmt = stmt.where(Order.stock_code == stock_code)
        return list((await db.execute(stmt.order_by(Order.created_at.desc()).offset(skip).limit(limit))).all())


async def list_owned_trades(
    account_id: UUID,
    *,
    skip: int,
    limit: int,
    stock_code: str | None = None,
) -> list[tuple[TradeRecord, str | None]]:
    """返回账户全局交易记录及股票名称。"""
    async with database_module.AsyncSessionLocal() as db:
        stmt = (
            select(TradeRecord, StockBasic.name)
            .outerjoin(StockBasic, TradeRecord.stock_code == StockBasic.stock_code)
            .where(TradeRecord.account_id == account_id)
        )
        if stock_code:
            stmt = stmt.where(TradeRecord.stock_code == stock_code)
        return list((await db.execute(stmt.order_by(TradeRecord.trade_time.desc()).offset(skip).limit(limit))).all())
