from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.account import Account
from app.models.position import Position
from app.models.user import User


def test_position_rejects_duplicate_account_stock_code(db_session):
    user = User(
        username="position_unique_user",
        email="position_unique_user@example.com",
        password_hash="hashed",
    )
    db_session.add(user)
    db_session.flush()

    account = Account(
        account_id=uuid4(),
        user_id=user.id,
        available_cash=Decimal("100000"),
        total_assets=Decimal("100000"),
        market_value=Decimal("0"),
        total_profit_loss=Decimal("0"),
    )
    db_session.add(account)
    db_session.flush()

    first_position = Position(
        account_id=account.account_id,
        stock_code="000001.SZ",
        total_shares=100,
        available_shares=100,
        frozen_shares=0,
        avg_cost=Decimal("10.0000"),
        current_price=Decimal("10.0000"),
        market_value=Decimal("1000.0000"),
        profit_loss=Decimal("0"),
        profit_loss_pct=Decimal("0"),
        purchase_details={"ledger": []},
    )
    duplicate_position = Position(
        account_id=account.account_id,
        stock_code="000001.SZ",
        total_shares=200,
        available_shares=200,
        frozen_shares=0,
        avg_cost=Decimal("11.0000"),
        current_price=Decimal("11.0000"),
        market_value=Decimal("2200.0000"),
        profit_loss=Decimal("0"),
        profit_loss_pct=Decimal("0"),
        purchase_details={"ledger": []},
    )

    db_session.add_all([first_position, duplicate_position])

    with pytest.raises(IntegrityError):
        db_session.commit()
