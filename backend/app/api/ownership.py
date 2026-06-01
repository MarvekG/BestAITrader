from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.crud.account import ensure_user_account
from app.models.account import Account
from app.models.order import Order
from app.models.position import Position
from app.models.session import Session as AnalysisSession
from app.models.trade_record import TradeRecord
from app.models.user import User


def get_current_user_account(db: Session, current_user: User) -> Account:
    """Return the current user's trading account, creating it if missing."""
    return ensure_user_account(db, current_user)


def get_owned_session(db: Session, session_id: UUID, current_user: User) -> AnalysisSession:
    """Return a session owned by the current user, or raise 404."""
    session = db.query(AnalysisSession).filter(
        AnalysisSession.session_id == session_id,
        AnalysisSession.user_id == current_user.id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


def get_owned_order(db: Session, order_id: UUID, current_user: User) -> Order:
    """Return an order owned by the current user, or raise 404."""
    account = get_current_user_account(db, current_user)
    order = db.query(Order).filter(
        Order.order_id == order_id,
        Order.account_id == account.account_id,
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def get_owned_position(db: Session, position_id: UUID, current_user: User) -> Position:
    """Return a position owned by the current user, or raise 404."""
    account = get_current_user_account(db, current_user)
    position = db.query(Position).filter(
        Position.position_id == position_id,
        Position.account_id == account.account_id,
    ).first()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    return position


def get_owned_trade_record(db: Session, trade_id: UUID, current_user: User) -> TradeRecord:
    """Return a trade record owned by the current user, or raise 404."""
    account = get_current_user_account(db, current_user)
    record = db.query(TradeRecord).filter(
        TradeRecord.trade_id == trade_id,
        TradeRecord.account_id == account.account_id,
    ).first()
    if not record:
        raise HTTPException(status_code=404, detail="Trade record not found")
    return record
