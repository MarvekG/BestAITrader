from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session as DbSession

from app.crud.account import ensure_user_account
from app.crud.base import CRUDBase
from app.models.session import Session
from app.models.position import Position
from app.models.user import User
from app.schemas.session import SessionCreate, SessionUpdate


class CRUDSession(CRUDBase[Session, SessionCreate, SessionUpdate]):
    def get(self, db: DbSession, *, id: UUID) -> Optional[Session]:
        return db.query(Session).filter(Session.session_id == id).first()

    def create(self, db: DbSession, *, obj_in: SessionCreate) -> Session:
        session = super().create(db, obj_in=obj_in)

        # 1. Check if user has account (Global Fund Pool)
        account = None
        if session.user_id:
            user = db.query(User).filter(User.id == session.user_id).first()
            if user:
                account = ensure_user_account(db, user, initial_capital=Decimal("100000.00"), commit=False)

        # If no user logged in (should not happen with new API), create orphan account?
        # For now assuming user exists as enforced by API

        if account:
            # Search for an existing position for this stock in the global account pool
            existing_position = db.query(Position).filter(
                Position.account_id == account.account_id,
                Position.stock_code == session.stock_code
            ).first()

            if existing_position:
                # If found, just link the session id logically if empty
                if not existing_position.session_id:
                    existing_position.session_id = session.session_id
            else:
                # Create initialized position linked to Global Account AND this Session
                position = Position(
                    account_id=account.account_id,
                    session_id=session.session_id,  # Link to this specific session
                    stock_code=session.stock_code,
                    total_shares=0,
                    available_shares=0,
                    frozen_shares=0,
                    avg_cost=0.0,
                    current_price=0.0,
                    market_value=0.0,
                    profit_loss=0.0,
                    profit_loss_pct=0.0,
                    purchase_details={}
                )
                db.add(position)

        db.commit()
        db.refresh(session)
        return session

    def remove(self, db: DbSession, *, id: UUID) -> Session:
        obj = db.query(Session).filter(Session.session_id == id).first()
        db.delete(obj)
        db.commit()
        return obj


crud_session = CRUDSession(Session)
