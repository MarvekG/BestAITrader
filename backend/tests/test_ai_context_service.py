from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.ai.llm_engine.context.service import AIContextService
from app.ai.llm_engine.context.technical import TechnicalSource
from app.ai.llm_engine.context.types import AI_CONTEXT_SECTION_ORDER, AIContextLayer
from app.core.request_context import clear_current_user_id, set_current_user_id
from app.models.account import Account
from app.models.account_equity_snapshot import AccountEquitySnapshot
from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.models.position import Position
from app.models.user import User


class _FakeProvider:
    def __init__(self, name, payload):
        self.name = name
        self.payload = payload

    async def build(self, runtime, sections):
        return AIContextLayer(self.name, self.payload)


@pytest.mark.asyncio
async def test_ai_context_service_assembles_time_layer_context():
    service = AIContextService()
    service.providers = [
        _FakeProvider("metadata", {"status": "available", "stock_code": "000001.SZ", "stock_name": "Ping An"}),
        _FakeProvider("realtime", {"status": "available", "market": {"price": 10.2}}),
        _FakeProvider("snapshot", {"status": "partial"}),
        _FakeProvider("history", {"status": "missing"}),
        _FakeProvider("signals", {"status": "available"}),
        _FakeProvider("events", {"status": "missing"}),
    ]

    context = await service.build("000001.SZ")

    assert list(context.keys()) == list(AI_CONTEXT_SECTION_ORDER)
    assert context["metadata"]["coverage"]["status"] == "partial"
    assert context["metadata"]["coverage"]["layers"]["realtime"] == "available"
    assert context["metadata"]["coverage"]["layers"]["history"] == "missing"
    assert context["metadata"]["_target_stock_code"] == "000001.SZ"
    assert context["metadata"]["_target_stock_name"] == "Ping An"
    assert context["realtime"]["_target_stock_code"] == "000001.SZ"
    assert context["snapshot"]["_target_stock_name"] == "Ping An"
    assert context["events"]["_target_stock_code"] == "000001.SZ"


@pytest.mark.asyncio
async def test_ai_context_service_includes_portfolio_static_context_for_current_user(
    db_session,
    monkeypatch,
    test_db,
):
    """AI 静态上下文应包含当前用户的组合概览和绩效表现。

    Args:
        db_session: 测试数据库会话。
        monkeypatch: pytest monkeypatch 工具。
        test_db: 测试数据库会话工厂。
    """
    from app.ai.llm_engine.context import runtime as runtime_module
    from app.ai.llm_engine.context.providers import PortfolioProvider

    user = User(
        username="portfolio_context_user",
        email="portfolio_context_user@example.com",
        password_hash="hashed",
    )
    db_session.add(user)
    db_session.flush()
    account = Account(
        user_id=user.id,
        total_assets=Decimal("1000000.0000"),
        available_cash=Decimal("300000.0000"),
        frozen_cash=Decimal("10000.0000"),
        market_value=Decimal("690000.0000"),
        initial_capital=Decimal("1000000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=3,
    )
    db_session.add(account)
    db_session.flush()
    db_session.add_all(
        [
            StockBasic(stock_code="000001.SZ", name="平安银行", industry="银行"),
            StockRealtimeMarket(
                stock_code="000001.SZ",
                current_price=Decimal("12.0000"),
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
            AccountEquitySnapshot(
                user_id=user.id,
                account_id=account.account_id,
                snapshot_date=date(2026, 5, 22),
                total_assets=Decimal("1020000.0000"),
                available_cash=Decimal("500000.0000"),
                market_value=Decimal("520000.0000"),
                position_count=2,
                daily_return=Decimal("0.01000000"),
                cumulative_return=Decimal("0.02000000"),
                benchmark_code="000300.SH",
                benchmark_close=Decimal("4040.000000"),
                benchmark_daily_return=Decimal("0.00500000"),
                benchmark_cumulative_return=Decimal("0.01000000"),
                excess_return=Decimal("0.01000000"),
                max_drawdown=Decimal("-0.03000000"),
            ),
        ]
    )
    db_session.commit()
    monkeypatch.setattr(runtime_module, "SessionLocal", test_db)

    token = set_current_user_id(user.id)
    try:
        context = await AIContextService(providers=[PortfolioProvider()]).build("000001.SZ")
    finally:
        clear_current_user_id(token)

    portfolio_context = context["portfolio"]
    assert portfolio_context["status"] == "available"
    assert portfolio_context["overview"]["summary"]["total_assets"] == 430000.0
    assert portfolio_context["overview"]["summary"]["position_count"] == 1
    assert portfolio_context["overview"]["positions"][0]["stock_code"] == "000001.SZ"
    assert portfolio_context["overview"]["positions"][0]["weight"] == 0.27906977
    assert portfolio_context["performance"]["snapshot_date"] == "2026-05-22"
    assert portfolio_context["performance"]["cumulative_return"] == 0.02
    assert portfolio_context["risk_control"]["summary"] == {
        "enabled": True,
        "max_single_position_pct": 0.2,
        "max_industry_position_pct": 0.35,
        "min_cash_pct": 0.1,
        "require_stop_loss": True,
        "stop_loss_warning_pct": 0.1,
    }
    assert portfolio_context["risk_control"]["text"] == (
        "Portfolio risk control: enabled; max single-stock weight 20.00%; "
        "max industry weight 35.00%; minimum cash ratio 10.00%; "
        "buy orders require stop loss; stop-loss warning threshold 10.00%."
    )
    assert context["metadata"]["coverage"]["layers"]["portfolio"] == "available"


class _FakeRealtimeQuery:
    def __init__(self, rows):
        self.rows = list(rows)

    def filter(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        self.rows.sort(key=lambda row: row.timestamp, reverse=True)
        return self

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeRealtimeDB:
    def __init__(self, rows):
        self.rows = rows

    def query(self, _model):
        return _FakeRealtimeQuery(self.rows)


def test_realtime_market_prefers_latest_timestamp_row():
    source = TechnicalSource()
    db = _FakeRealtimeDB(
        [
            SimpleNamespace(
                timestamp=datetime(2026, 2, 26, 10, 10, 4),
                current_price=1474.0,
                change_percent=-1.18,
                turnover_rate=None,
                volume_ratio=None,
                amplitude=None,
                pb_ratio=None,
                pe_dynamic=None,
                turnover=1614584135.0,
                volume=1091758.0,
                total_market_cap=None,
                circulating_market_cap=None,
            ),
            SimpleNamespace(
                timestamp=datetime(2026, 3, 23, 14, 35, 43),
                current_price=1401.3,
                change_percent=-3.02,
                turnover_rate=None,
                volume_ratio=None,
                amplitude=None,
                pb_ratio=None,
                pe_dynamic=None,
                turnover=5811960008.0,
                volume=4114309.0,
                total_market_cap=None,
                circulating_market_cap=None,
            ),
        ]
    )

    market = source._get_realtime_market(db, "600519.SH")

    assert market["price"] == 1401.3
    assert market["pct_chg"] == -3.02
    assert market["volume"] == 4114309.0
