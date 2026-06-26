from datetime import datetime, timedelta
from decimal import Decimal

from app.crud.account import ensure_user_account
from app.crud.user import create_user
from app.models.position import Position
from app.schemas.user import UserCreate
from app.trading.pm_rules import evaluate_position_disciplines, sync_pm_discipline_to_position


def _create_user(db_session):
    user = create_user(
        db_session,
        UserCreate(username="pmrules", email="pmrules@example.com", password="password123"),
    )
    account = ensure_user_account(db_session, user)
    return user, account


def _create_position(db_session, account, *, stock_code="000001.SZ", current_price=Decimal("10.00")):
    position = Position(
        account_id=account.account_id,
        stock_code=stock_code,
        total_shares=100,
        available_shares=100,
        frozen_shares=0,
        avg_cost=Decimal("9.00"),
        current_price=current_price,
        market_value=Decimal("1000.00"),
        profit_loss=Decimal("100.00"),
        profit_loss_pct=Decimal("0.1000"),
        purchase_details={},
    )
    db_session.add(position)
    db_session.commit()
    db_session.refresh(position)
    return position


def test_sync_pm_discipline_to_position_writes_structured_fields(db_session, test_db, monkeypatch):
    user, account = _create_user(db_session)
    position = _create_position(db_session, account)
    monkeypatch.setattr("app.trading.pm_rules.SessionLocal", test_db)

    synced = sync_pm_discipline_to_position(
        session_id=None,
        user_id=user.id,
        stock_code=position.stock_code,
        decision={
            "decision": "hold",
            "stop_loss": 8.5,
            "take_profit": 12.0,
            "holding_horizon_days": 5,
        },
    )

    db_session.refresh(position)
    assert synced is True
    assert position.stop_loss == Decimal("8.5")
    assert position.take_profit == Decimal("12.0")
    assert position.horizon_deadline is not None


def test_sync_pm_discipline_to_position_ignores_non_positive_trigger_prices(db_session, test_db, monkeypatch):
    """PM 输出空仓目标时不把 0 写成持仓止损止盈触发线。"""
    user, account = _create_user(db_session)
    position = _create_position(db_session, account)
    monkeypatch.setattr("app.trading.pm_rules.SessionLocal", test_db)

    synced = sync_pm_discipline_to_position(
        session_id=None,
        user_id=user.id,
        stock_code=position.stock_code,
        decision={
            "decision": "hold",
            "stop_loss": 0,
            "take_profit": 0,
            "holding_horizon_days": 5,
        },
    )

    db_session.refresh(position)
    assert synced is True
    assert position.stop_loss is None
    assert position.take_profit is None
    assert position.horizon_deadline is not None


def test_evaluate_position_disciplines_detects_stop_loss(db_session, monkeypatch):
    user, account = _create_user(db_session)
    position = _create_position(db_session, account)
    position.stop_loss = Decimal("9.50")
    db_session.commit()
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        lambda stock_code: {"success": True, "latest_price": "9.40"},
    )

    triggered = evaluate_position_disciplines(db_session, user_id=user.id)

    assert triggered[0]["stock_code"] == "000001.SZ"
    assert triggered[0]["trigger"] == "stop_loss"


def test_evaluate_position_disciplines_detects_take_profit(db_session, monkeypatch):
    user, account = _create_user(db_session)
    position = _create_position(db_session, account)
    position.take_profit = Decimal("11.00")
    db_session.commit()
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        lambda stock_code: {"success": True, "latest_price": "11.10"},
    )

    triggered = evaluate_position_disciplines(db_session, user_id=user.id)

    assert triggered[0]["trigger"] == "take_profit"


def test_evaluate_position_disciplines_detects_horizon_expired(db_session, monkeypatch):
    user, account = _create_user(db_session)
    position = _create_position(db_session, account)
    position.horizon_deadline = datetime.now() - timedelta(days=1)
    db_session.commit()
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        lambda stock_code: {"success": False},
    )

    triggered = evaluate_position_disciplines(db_session, user_id=user.id)

    assert triggered[0]["trigger"] == "horizon_expired"
