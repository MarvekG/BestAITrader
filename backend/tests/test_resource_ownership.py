from datetime import datetime
from decimal import Decimal

import pytest

from app.crud.account import ensure_user_account
from app.crud.user import create_user
from app.models.debate_message import DebateMessage
from app.models.order import Order
from app.models.position import Position
from app.models.session import Session as AnalysisSession
from app.models.trade_record import TradeRecord
from app.schemas.user import UserCreate


PASSWORD = "password123"


def _create_user_and_headers(client, db_session, username: str):
    user = create_user(
        db_session,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password=PASSWORD,
        ),
    )
    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": username,
            "password": PASSWORD,
        },
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return user, {"Authorization": f"Bearer {token}"}


def _create_session(db_session, user, *, stock_code: str, status: str = "completed"):
    session = AnalysisSession(
        user_id=user.id,
        stock_code=stock_code,
        trading_frequency="swing",
        trading_strategy="trend",
        status=status,
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session


def _create_debate_message(db_session, session):
    message = DebateMessage(
        session_id=session.session_id,
        stage="portfolio_management",
        round_number=1,
        agent_name="Portfolio Manager",
        agent_role="portfolio_manager",
        decision="hold",
        confidence=0.7,
        reasoning="private reasoning",
        prompt_input="private prompt",
        analysis={"action": "hold", "target_position": 0.0},
    )
    db_session.add(message)
    db_session.commit()
    db_session.refresh(message)
    return message


def _create_account_resources(db_session, user, session):
    account = ensure_user_account(db_session, user)
    position = Position(
        account_id=account.account_id,
        session_id=session.session_id,
        stock_code=session.stock_code,
        total_shares=100,
        available_shares=100,
        frozen_shares=0,
        avg_cost=Decimal("10.00"),
        current_price=Decimal("11.00"),
        market_value=Decimal("1100.00"),
        profit_loss=Decimal("100.00"),
        profit_loss_pct=Decimal("0.1000"),
        purchase_details={},
    )
    order = Order(
        session_id=session.session_id,
        account_id=account.account_id,
        stock_code=session.stock_code,
        action="buy",
        order_type="limit",
        price=Decimal("10.00"),
        shares=100,
        status="pending",
        filled_shares=0,
        avg_fill_price=None,
        realized_pnl=Decimal("0.00"),
    )
    trade = TradeRecord(
        session_id=session.session_id,
        account_id=account.account_id,
        order_id=order.order_id,
        stock_code=session.stock_code,
        action="buy",
        quantity=100,
        fill_price=Decimal("10.00"),
        commission=Decimal("1.00"),
        stamp_duty=Decimal("0.00"),
        transfer_fee=Decimal("0.00"),
        total_fees=Decimal("1.00"),
        net_amount=Decimal("1001.00"),
        trade_time=datetime.now(),
    )
    db_session.add_all([position, order, trade])
    db_session.commit()
    db_session.refresh(position)
    db_session.refresh(order)
    db_session.refresh(trade)
    return position, order, trade


@pytest.fixture
def ownership_context(client, db_session):
    owner, owner_headers = _create_user_and_headers(client, db_session, "owner_user")
    intruder, intruder_headers = _create_user_and_headers(client, db_session, "intruder_user")
    owner_session = _create_session(db_session, owner, stock_code="000001.SZ")
    intruder_session = _create_session(db_session, intruder, stock_code="600000.SH")
    _create_debate_message(db_session, owner_session)
    owner_position, owner_order, owner_trade = _create_account_resources(db_session, owner, owner_session)

    return {
        "owner": owner,
        "owner_headers": owner_headers,
        "owner_session": owner_session,
        "owner_position": owner_position,
        "owner_order": owner_order,
        "owner_trade": owner_trade,
        "intruder_headers": intruder_headers,
        "intruder_session": intruder_session,
    }


def test_session_list_only_returns_current_user_sessions(client, ownership_context):
    response = client.get("/api/v1/sessions/", headers=ownership_context["intruder_headers"])

    assert response.status_code == 200
    session_ids = {item["session_id"] for item in response.json()}
    assert str(ownership_context["owner_session"].session_id) not in session_ids
    assert str(ownership_context["intruder_session"].session_id) in session_ids


@pytest.mark.parametrize(
    ("method", "path_template", "kwargs"),
    [
        ("get", "/api/v1/sessions/{session_id}", {}),
        ("put", "/api/v1/sessions/{session_id}", {"json": {"status": "archived"}}),
        ("post", "/api/v1/sessions/{session_id}/archive", {}),
        ("get", "/api/v1/debate/history/{session_id}", {}),
        ("get", "/api/v1/debate/threads/{session_id}", {}),
        ("get", "/api/v1/debate/decisions/{session_id}", {}),
        ("get", "/api/v1/trading/orders/history/{session_id}", {}),
        ("get", "/api/v1/trading/trades/{session_id}", {}),
        ("get", "/api/v1/accounts/positions/{session_id}", {}),
    ],
)
def test_session_scoped_endpoints_hide_other_users_session(
    client,
    ownership_context,
    method,
    path_template,
    kwargs,
):
    path = path_template.format(session_id=ownership_context["owner_session"].session_id)
    response = getattr(client, method)(
        path,
        headers=ownership_context["intruder_headers"],
        **kwargs,
    )

    assert response.status_code == 404


def test_delete_session_hides_other_users_session(client, ownership_context):
    response = client.delete(
        f"/api/v1/sessions/{ownership_context['owner_session'].session_id}",
        headers=ownership_context["intruder_headers"],
    )

    assert response.status_code == 404


def test_batch_session_operations_ignore_other_users_sessions(client, ownership_context):
    owner_session_id = str(ownership_context["owner_session"].session_id)

    archive_response = client.post(
        "/api/v1/sessions/batch-archive",
        headers=ownership_context["intruder_headers"],
        json={"session_ids": [owner_session_id]},
    )
    delete_response = client.post(
        "/api/v1/sessions/batch-delete",
        headers=ownership_context["intruder_headers"],
        json={"session_ids": [owner_session_id]},
    )

    assert archive_response.status_code == 200
    assert archive_response.json()["updated_count"] == 0
    assert delete_response.status_code == 404


@pytest.mark.parametrize(
    ("method", "path_template", "resource_key", "kwargs"),
    [
        ("get", "/api/v1/trading/orders/{resource_id}", "owner_order", {}),
        ("put", "/api/v1/trading/orders/{resource_id}", "owner_order", {"json": {"shares": 10}}),
        ("post", "/api/v1/trading/orders/{resource_id}/cancel", "owner_order", {}),
        ("get", "/api/v1/accounts/positions/single/{resource_id}", "owner_position", {}),
        ("get", "/api/v1/trading/trades/single/{resource_id}", "owner_trade", {}),
    ],
)
def test_id_scoped_trading_resources_hide_other_users_records(
    client,
    ownership_context,
    method,
    path_template,
    resource_key,
    kwargs,
):
    resource = ownership_context[resource_key]
    resource_id = getattr(resource, "order_id", None) or getattr(resource, "position_id", None) or resource.trade_id
    response = getattr(client, method)(
        path_template.format(resource_id=resource_id),
        headers=ownership_context["intruder_headers"],
        **kwargs,
    )

    assert response.status_code == 404


def test_run_debate_hides_other_users_session(client, ownership_context):
    response = client.post(
        "/api/v1/debate/run",
        headers=ownership_context["intruder_headers"],
        json={
            "session_id": str(ownership_context["owner_session"].session_id),
            "stock_code": ownership_context["owner_session"].stock_code,
            "trading_frequency": "swing",
            "trading_strategy": "trend",
        },
    )

    assert response.status_code == 404
