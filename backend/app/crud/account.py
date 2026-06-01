from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.user import User


DEFAULT_ACCOUNT_CAPITAL = Decimal("1000000.00")


def ensure_user_account(
    db: Session,
    user: User,
    initial_capital: Decimal = DEFAULT_ACCOUNT_CAPITAL,
    commit: bool = True,
) -> Account:
    """Return the user's account, creating a default simulated trading account if missing.

    Args:
        db: Database session.
        user: Current authenticated user.
        initial_capital: Starting capital for a newly created account.
        commit: Whether to commit the new account immediately.

    Returns:
        The existing or newly created account.
    """
    if user.account:
        return user.account

    existing_account = db.query(Account).filter(Account.user_id == user.id).first()
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
            db.commit()
        else:
            db.flush()
        db.refresh(account)
    except IntegrityError:
        db.rollback()
        existing_account = db.query(Account).filter(Account.user_id == user.id).first()
        if not existing_account:
            raise
        return existing_account

    return account
