from datetime import date, datetime
from decimal import Decimal

from app.models.account import Account
from app.models.data_storage import KlineData, StockBasic, StockRealtimeMarket
from app.models.position import Position
from app.models.user import User


def _get_test_user(db_session) -> User:
    """读取当前测试登录用户。"""
    return db_session.query(User).filter(User.username.like("test_%")).first()


def test_portfolio_overview_returns_weights_and_industries(client, db_session, auth_headers) -> None:
    """组合概览接口应返回动态估值、单股权重和行业分布。"""
    user = _get_test_user(db_session)
    account = Account(
        user_id=user.id,
        total_assets=Decimal("1000000.0000"),
        available_cash=Decimal("300000.0000"),
        frozen_cash=Decimal("10000.0000"),
        market_value=Decimal("690000.0000"),
        initial_capital=Decimal("1000000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=0,
    )
    db_session.add(account)
    db_session.flush()
    db_session.add_all(
        [
            StockBasic(stock_code="000001.SZ", name="平安银行", industry="银行"),
            StockBasic(stock_code="600519.SH", name="贵州茅台", industry="白酒"),
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=12.0,
                timestamp=datetime(2026, 5, 23, 10, 0, 0),
            ),
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=11.0,
                timestamp=datetime(2026, 5, 22, 10, 0, 0),
            ),
            StockRealtimeMarket(
                stock_code="600519.SH",
                current_price=1500.0,
                timestamp=datetime(2026, 5, 23, 10, 0, 0),
            ),
            Position(
                account_id=account.account_id,
                stock_code="000001.SZ",
                total_shares=10000,
                available_shares=8000,
                frozen_shares=2000,
                avg_cost=Decimal("10.0000"),
                current_price=Decimal("10.5000"),
                market_value=Decimal("105000.0000"),
                profit_loss=Decimal("5000.0000"),
                profit_loss_pct=Decimal("0.0500"),
            ),
            Position(
                account_id=account.account_id,
                stock_code="600519.SH",
                total_shares=200,
                available_shares=200,
                frozen_shares=0,
                avg_cost=Decimal("1600.0000"),
                current_price=Decimal("1550.0000"),
                market_value=Decimal("310000.0000"),
                profit_loss=Decimal("-10000.0000"),
                profit_loss_pct=Decimal("-0.0313"),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/portfolio/overview", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total_assets"] == 730000.0
    assert data["summary"]["available_cash"] == 300000.0
    assert data["summary"]["frozen_cash"] == 10000.0
    assert data["summary"]["market_value"] == 420000.0
    assert data["summary"]["cash_ratio"] == 0.42465753
    assert data["summary"]["position_ratio"] == 0.57534247
    assert data["summary"]["position_count"] == 2
    assert [item["stock_code"] for item in data["positions"]] == ["600519.SH", "000001.SZ"]
    assert data["positions"][0]["weight"] == 0.4109589
    assert data["positions"][0]["unrealized_pnl"] == -20000.0
    assert data["positions"][1]["weight"] == 0.16438356
    assert data["positions"][1]["unrealized_pnl"] == 20000.0
    assert [item["industry"] for item in data["industry_allocations"]] == ["白酒", "银行"]
    assert data["industry_allocations"][0]["weight"] == 0.4109589
    assert data["risk_metrics"]["top_single_position_pct"] == 0.4109589
    assert data["risk_metrics"]["top_industry_position_pct"] == 0.4109589
    assert data["risk_metrics"]["position_hhi"] == 0.19590917
    assert data["risk_metrics"]["industry_hhi"] == 0.19590917
    assert data["risk_metrics"]["max_unrealized_loss_pct"] == -0.0625
    assert data["risk_metrics"]["max_unrealized_loss_stock_code"] == "600519.SH"
    assert data["risk_metrics"]["stop_loss_coverage_pct"] == 0.0
    assert data["top_weights"][0]["stock_code"] == "600519.SH"
    assert data["top_gainers"][0]["stock_code"] == "000001.SZ"
    assert data["top_losers"][0]["stock_code"] == "600519.SH"


def test_portfolio_overview_falls_back_to_position_price_and_unknown_industry(
    client,
    db_session,
    auth_headers,
) -> None:
    """缺少行情和基础信息时应使用持仓价并归入未知行业。"""
    user = _get_test_user(db_session)
    account = Account(
        user_id=user.id,
        total_assets=Decimal("1000000.0000"),
        available_cash=Decimal("50000.0000"),
        frozen_cash=Decimal("0.0000"),
        market_value=Decimal("950000.0000"),
        initial_capital=Decimal("1000000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=0,
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(
        Position(
            account_id=account.account_id,
            stock_code="300001.SZ",
            total_shares=1000,
            available_shares=1000,
            frozen_shares=0,
            avg_cost=Decimal("20.0000"),
            current_price=Decimal("25.0000"),
            market_value=Decimal("25000.0000"),
            profit_loss=Decimal("5000.0000"),
            profit_loss_pct=Decimal("0.2500"),
        )
    )
    db_session.commit()

    response = client.get("/api/v1/portfolio/overview", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total_assets"] == 75000.0
    assert data["positions"][0]["stock_name"] == "Unknown"
    assert data["positions"][0]["industry"] == "未知行业"
    assert data["positions"][0]["current_price"] == 25.0
    assert data["positions"][0]["market_value"] == 25000.0
    assert data["positions"][0]["weight"] == 0.33333333
    assert data["industry_allocations"][0]["industry"] == "未知行业"


def test_portfolio_overview_returns_weighted_volatility_metrics(client, db_session, auth_headers) -> None:
    """组合概览应基于持仓权重和历史 K 线返回近 20 日组合波动估计。"""
    user = _get_test_user(db_session)
    account = Account(
        user_id=user.id,
        total_assets=Decimal("100000.0000"),
        available_cash=Decimal("0.0000"),
        frozen_cash=Decimal("0.0000"),
        market_value=Decimal("100000.0000"),
        initial_capital=Decimal("100000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=0,
    )
    db_session.add(account)
    db_session.flush()
    db_session.add_all(
        [
            StockBasic(stock_code="000001.SZ", name="平安银行", industry="银行"),
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=10.0,
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
                profit_loss=Decimal("0.0000"),
                profit_loss_pct=Decimal("0.0000"),
                purchase_details={"stop_loss": 9.0},
            ),
        ]
    )
    db_session.commit()
    closes = [10, 11, 10, 11, 10, 11]
    for index, close_price in enumerate(closes, start=1):
        db_session.add(
            KlineData(
                stock_code="000001.SZ",
                date=date(2026, 5, index),
                close=close_price,
                freq="D",
                data_source="test",
            )
        )
    db_session.commit()

    response = client.get("/api/v1/portfolio/overview", headers=auth_headers)

    assert response.status_code == 200
    risk_metrics = response.json()["risk_metrics"]
    assert risk_metrics["estimated_volatility_20d"] == 1.65992134
    assert risk_metrics["estimated_volatility_60d"] == 1.65992134
    assert risk_metrics["stop_loss_coverage_pct"] == 1.0


def test_portfolio_overview_empty_account_returns_cash_only(client, auth_headers) -> None:
    """空仓账户应返回现金占比 100% 和空持仓列表。"""
    response = client.get("/api/v1/portfolio/overview", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["total_assets"] == 1000000.0
    assert data["summary"]["available_cash"] == 1000000.0
    assert data["summary"]["market_value"] == 0.0
    assert data["summary"]["cash_ratio"] == 1.0
    assert data["summary"]["position_ratio"] == 0.0
    assert data["summary"]["position_count"] == 0
    assert data["positions"] == []
    assert data["industry_allocations"] == []
    assert data["risk_metrics"]["top_single_position_pct"] == 0.0
    assert data["risk_metrics"]["top_industry_position_pct"] == 0.0
    assert data["risk_metrics"]["position_hhi"] == 0.0
    assert data["top_weights"] == []
    assert data["top_gainers"] == []
    assert data["top_losers"] == []
