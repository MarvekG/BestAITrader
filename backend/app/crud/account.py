from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.user import User


DEFAULT_ACCOUNT_CAPITAL = Decimal("1000000.00")


async def ensure_user_account(
    db: AsyncSession,
    user: User,
    initial_capital: Decimal = DEFAULT_ACCOUNT_CAPITAL,
    commit: bool = True,
) -> Account:
    """返回用户交易账户；不存在时创建默认模拟账户。

    Args:
        db: 异步数据库会话。
        user: 当前认证用户。
        initial_capital: 新建账户的初始资金。
        commit: 是否立即提交新账户。

    Returns:
        已存在或新建的账户。
    """
    result = await db.execute(select(Account).where(Account.user_id == user.id))
    existing_account = result.scalar_one_or_none()
    if existing_account:
        return existing_account

    account = Account(
        user_id=user.id,
        total_assets=initial_capital,
        initial_capital=initial_capital,
        available_cash=initial_capital,
        frozen_cash=Decimal("0.00"),
        market_value=Decimal("0.00"),
        total_profit_loss=Decimal("0.00"),
        profit_loss_pct=Decimal("0.00"),
        total_trades=0,
        win_rate=Decimal("0.00"),
    )
    db.add(account)
    try:
        if commit:
            await db.commit()
        else:
            await db.flush()
        await db.refresh(account)
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(Account).where(Account.user_id == user.id))
        existing_account = result.scalar_one_or_none()
        if not existing_account:
            raise
        return existing_account

    return account
