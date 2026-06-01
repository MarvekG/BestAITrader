from datetime import datetime
from decimal import Decimal

from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.models.position import Position
from app.risk_control.service import portfolio_risk_control_service


def _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking", name=None):
    """创建测试股票基础信息。"""
    record = StockBasic(
        stock_code=stock_code,
        name=name or stock_code,
        industry=industry,
        market="SZSE",
        data_source="test",
    )
    db_session.add(record)
    db_session.commit()
    return record


def _get_auth_account(client, auth_headers, db_session):
    """初始化并读取当前测试用户账户。"""
    response = client.get("/api/v1/accounts/my-assets", headers=auth_headers)
    assert response.status_code == 200

    from app.models.account import Account

    return db_session.query(Account).first()


def _add_position(db_session, account, stock_code, shares, price, industry="Banking"):
    """创建测试持仓记录。"""
    _seed_stock_basic(db_session, stock_code=stock_code, industry=industry)
    position = Position(
        account_id=account.account_id,
        stock_code=stock_code,
        total_shares=shares,
        available_shares=shares,
        frozen_shares=0,
        avg_cost=Decimal(str(price)),
        current_price=Decimal(str(price)),
        market_value=Decimal(str(price)) * Decimal(str(shares)),
        profit_loss=Decimal("0"),
        profit_loss_pct=Decimal("0"),
        purchase_details={},
    )
    db_session.add(position)
    db_session.commit()
    return position


def test_get_risk_control_config_creates_default(client, auth_headers):
    response = client.get("/api/v1/risk-control/config", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["max_single_position_pct"] == 0.2
    assert payload["max_industry_position_pct"] == 0.35
    assert payload["min_cash_pct"] == 0.1
    assert payload["require_stop_loss"] is True
    assert payload["stop_loss_warning_pct"] == 0.1
    assert payload["rule_policies"] == {
        "require_stop_loss": "block",
        "max_single_position_pct": "block",
        "max_industry_position_pct": "block",
        "min_cash_pct": "block",
        "stop_loss_warning_pct": "block",
    }


def test_update_risk_control_config(client, auth_headers):
    response = client.put(
        "/api/v1/risk-control/config",
        headers=auth_headers,
        json={
            "enabled": False,
            "max_single_position_pct": 0.15,
            "max_industry_position_pct": 0.3,
            "min_cash_pct": 0.2,
            "require_stop_loss": False,
            "stop_loss_warning_pct": 0.08,
            "rule_policies": {
                "max_single_position_pct": "block",
                "max_industry_position_pct": "off",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["max_single_position_pct"] == 0.15
    assert payload["max_industry_position_pct"] == 0.3
    assert payload["min_cash_pct"] == 0.2
    assert payload["require_stop_loss"] is False
    assert payload["stop_loss_warning_pct"] == 0.08
    assert payload["rule_policies"]["max_single_position_pct"] == "block"
    assert payload["rule_policies"]["max_industry_position_pct"] == "off"
    assert payload["rule_policies"]["require_stop_loss"] == "block"


def test_update_risk_control_config_rejects_invalid_percent(client, auth_headers):
    response = client.put(
        "/api/v1/risk-control/config",
        headers=auth_headers,
        json={"max_single_position_pct": 1.2},
    )

    assert response.status_code == 422


def test_update_risk_control_config_rejects_accept_policy(client, auth_headers):
    response = client.put(
        "/api/v1/risk-control/config",
        headers=auth_headers,
        json={"rule_policies": {"min_cash_pct": "accept"}},
    )

    assert response.status_code == 422


def test_evaluate_order_rejects_invalid_action(client, auth_headers):
    response = client.post(
        "/api/v1/risk-control/evaluate-order",
        headers=auth_headers,
        json={
            "stock_code": "000001.SZ",
            "action": "hold",
            "shares": 100,
            "price": 10,
            "order_type": "market",
        },
    )

    assert response.status_code == 422


def test_evaluate_order_skips_rules_when_disabled(client, auth_headers, db_session):
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    account = _get_auth_account(client, auth_headers, db_session)
    portfolio_risk_control_service.update_config(db_session, account, {"enabled": False})

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=5000,
        price=100,
        order_type="market",
        stop_loss=None,
    )

    assert result["enabled"] is False
    assert result["passed"] is True
    assert result["severity"] == "none"
    assert result["accepted"] == []
    assert result["blocks"] == []


def test_evaluate_order_blocks_single_position_exceed_by_default(client, auth_headers, db_session):
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    account = _get_auth_account(client, auth_headers, db_session)
    portfolio_risk_control_service.update_config(db_session, account, {"max_single_position_pct": 0.2})

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=3000,
        price=100,
        order_type="market",
        stop_loss=90,
    )

    assert result["passed"] is False
    assert result["severity"] == "block"
    assert result["accepted"] == []
    assert result["blocks"][0]["rule"] == "max_single_position_pct"
    assert result["blocks"][0]["message_key"] == "trading_center.risk_control.messages.max_single_position_pct"
    assert result["blocks"][0]["params"] == {
        "current": "30.00%",
        "limit": "20.00%",
        "stock_code": "000001.SZ",
        "industry": "Banking",
    }
    assert "warnings" not in result
    assert result["metrics"]["post_single_position_pct"] == 0.3


def test_evaluate_order_blocks_when_cash_falls_below_floor(client, auth_headers, db_session):
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    account = _get_auth_account(client, auth_headers, db_session)
    portfolio_risk_control_service.update_config(
        db_session,
        account,
        {
            "min_cash_pct": 0.1,
            "rule_policies": {
                "max_single_position_pct": "off",
                "max_industry_position_pct": "off",
            },
        },
    )

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=9500,
        price=100,
        order_type="market",
        stop_loss=90,
    )

    assert result["severity"] == "block"
    assert result["accepted"] == []
    assert any(item["rule"] == "min_cash_pct" for item in result["blocks"])
    assert result["metrics"]["post_cash_pct"] == 0.05


def test_evaluate_order_blocks_missing_required_stop_loss(client, auth_headers, db_session):
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    account = _get_auth_account(client, auth_headers, db_session)
    portfolio_risk_control_service.update_config(db_session, account, {"require_stop_loss": True})

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=100,
        order_type="market",
        stop_loss=None,
    )

    assert result["passed"] is False
    assert result["severity"] == "block"
    assert result["accepted"] == []
    assert result["blocks"][0]["rule"] == "require_stop_loss"
    assert result["blocks"][0]["message_key"] == "trading_center.risk_control.messages.require_stop_loss"
    assert result["blocks"][0]["params"] == {
        "current": "not_set",
        "limit": "required",
        "stock_code": "000001.SZ",
        "industry": "Banking",
    }


def test_evaluate_order_blocks_when_industry_exceeds_limit(client, auth_headers, db_session):
    account = _get_auth_account(client, auth_headers, db_session)
    _add_position(db_session, account, "000002.SZ", 2000, 100, industry="Banking")
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    portfolio_risk_control_service.update_config(
        db_session,
        account,
        {
            "max_industry_position_pct": 0.35,
            "rule_policies": {"max_single_position_pct": "off"},
        },
    )

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=3000,
        price=100,
        order_type="market",
        stop_loss=90,
    )

    assert result["severity"] == "block"
    assert result["accepted"] == []
    assert any(item["rule"] == "max_industry_position_pct" for item in result["blocks"])
    assert result["metrics"]["post_industry_position_pct"] == 0.416667


def test_evaluate_order_blocks_single_position_when_policy_is_block(client, auth_headers, db_session):
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    account = _get_auth_account(client, auth_headers, db_session)
    portfolio_risk_control_service.update_config(
        db_session,
        account,
        {
            "max_single_position_pct": 0.2,
            "rule_policies": {"max_single_position_pct": "block"},
        },
    )

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=3000,
        price=100,
        order_type="market",
        stop_loss=90,
    )

    assert result["passed"] is False
    assert result["severity"] == "block"
    assert result["accepted"] == []
    assert result["blocks"][0]["rule"] == "max_single_position_pct"


def test_evaluate_order_skips_rule_when_policy_is_off(client, auth_headers, db_session):
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    account = _get_auth_account(client, auth_headers, db_session)
    portfolio_risk_control_service.update_config(
        db_session,
        account,
        {
            "max_single_position_pct": 0.2,
            "rule_policies": {"max_single_position_pct": "off"},
        },
    )

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=3000,
        price=100,
        order_type="market",
        stop_loss=90,
    )

    assert result["passed"] is True
    assert result["severity"] == "none"
    assert result["accepted"] == []
    assert result["blocks"] == []


def test_evaluate_order_uses_dynamic_portfolio_valuation(client, auth_headers, db_session):
    account = _get_auth_account(client, auth_headers, db_session)
    account.total_assets = Decimal("1000000.0000")
    account.available_cash = Decimal("300000.0000")
    _seed_stock_basic(db_session, stock_code="000001.SZ", industry="Banking")
    db_session.add_all(
        [
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=50.0,
                timestamp=datetime(2026, 5, 23, 10, 0, 0),
            ),
            Position(
                account_id=account.account_id,
                stock_code="000001.SZ",
                total_shares=10000,
                available_shares=10000,
                frozen_shares=0,
                avg_cost=Decimal("10.0000"),
                current_price=Decimal("10.0000"),
                market_value=Decimal("100000.0000"),
                profit_loss=Decimal("0"),
                profit_loss_pct=Decimal("0"),
                purchase_details={},
            ),
        ]
    )
    db_session.commit()
    portfolio_risk_control_service.update_config(
        db_session,
        account,
        {"require_stop_loss": False, "max_single_position_pct": 0.2},
    )

    result = portfolio_risk_control_service.evaluate_order(
        db_session,
        account=account,
        stock_code="000001.SZ",
        action="buy",
        shares=100,
        price=50,
        order_type="market",
        stop_loss=None,
    )

    assert result["metrics"]["total_assets"] == 800000.0
    assert result["metrics"]["current_single_position_value"] == 500000.0
    assert result["metrics"]["post_single_position_pct"] == 0.63125
