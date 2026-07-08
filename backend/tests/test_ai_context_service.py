from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.ai.json_utils import stable_json_dumps
from app.ai.llm_engine.context.capital_flow import _build_money_flow_trend_summary
from app.ai.llm_engine.context.capital_flow import _build_margin_trend_summary_payload
from app.ai.llm_engine.context.canonical_metrics import _build_percentile_payload
from app.ai.llm_engine.context.providers import (
    _build_price_position_summary,
    _compact_series_payload,
    _technical_signal_summary_from_raw,
)
from app.ai.llm_engine.context.service import AIContextService
from app.ai.llm_engine.context.technical import TechnicalSource
from app.ai.llm_engine.context.technical import _build_price_volume_summary_payload
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


def test_stable_json_dumps_defaults_to_compact_output():
    result = stable_json_dumps({"b": 2, "a": {"c": 3}})

    assert result == '{"a":{"c":3},"b":2}'


def test_compact_series_payload_uses_csv_rows():
    payload = _compact_series_payload(
        [
            {"date": "2026-06-10", "close": "10.2元", "note": "plain"},
            {"date": "2026-06-11", "close": "10.5元", "note": "has,comma"},
        ],
        columns=["date", "close", "note"],
        window_days=30,
    )

    assert payload == {
        "status": "available",
        "format": "csv_rows",
        "columns": ["date", "close", "note"],
        "rows": ["2026-06-10,10.2元,plain", '2026-06-11,10.5元,"has,comma"'],
        "record_count": 2,
        "window_days": 30,
    }


def test_money_flow_trend_summary_converts_cumulative_wan_to_yi():
    """资金流趋势汇总应给出确定性亿元口径，避免 Agent 将万元误读为亿元。"""
    amounts_in_10k_cny = [
        -150773.99,
        135415.02,
        -470132.34,
        -210842.59,
        462879.95,
        -247735.87,
        -383886.75,
        369898.27,
        -30387.64,
        -397182.85,
        -141094.07,
        146051.92,
        -16680.34,
        59631.62,
        268039.5,
        -365095.96,
        -447625.34,
        -764.09,
        219304.8,
        -51119.51,
    ]
    flows = [
        SimpleNamespace(
            trade_date=date(2026, 6, 8 + index),
            net_inflow_main=amount * 10_000,
        )
        for index, amount in enumerate(amounts_in_10k_cny)
    ]

    summary = _build_money_flow_trend_summary(flows)

    assert summary["data_sources"] == ["data.stock_money_flow"]
    assert summary["start_date"] == "2026-06-08"
    assert summary["end_date"] == "2026-06-27"
    assert summary["change_bases"]["net_inflow_main_total"] == "sum from 2026-06-08 to 2026-06-27"
    assert summary["window_records"] == "20笔"
    assert summary["net_inflow_main_total"] == "-125.21亿元"
    assert summary["net_inflow_main_daily_average"] == "-6.26亿元"
    assert summary["inflow_days"] == "7天"
    assert summary["outflow_days"] == "13天"
    assert summary["inflow_day_ratio"] == "35%"
    assert summary["outflow_day_ratio"] == "65%"
    assert summary["net_flow_bias"] == "negative"
    assert summary["latest_inflow_streak_days"] == "0天"
    assert summary["latest_outflow_streak_days"] == "1天"
    assert summary["max_inflow_streak_days"] == "2天"
    assert summary["max_outflow_streak_days"] == "3天"
    assert summary["net_inflow_main_3d"] == "16.74亿元"
    assert summary["net_inflow_main_5d"] == "-64.53亿元"
    assert summary["net_inflow_main_10d"] == "-32.94亿元"


def test_price_volume_summary_precomputes_debate_price_math():
    """价格量能摘要应提前计算 LLM 高频手算的区间与量能指标。"""
    klines = [
        SimpleNamespace(date=date(2026, 6, 1), close=10, high=11, low=9, volume=100, turnover=1_000_000),
        SimpleNamespace(date=date(2026, 6, 2), close=12, high=13, low=11, volume=200, turnover=2_000_000),
        SimpleNamespace(date=date(2026, 6, 3), close=9, high=10, low=8, volume=300, turnover=3_000_000),
        SimpleNamespace(date=date(2026, 6, 4), close=11, high=12, low=10, volume=400, turnover=4_000_000),
        SimpleNamespace(date=date(2026, 6, 5), close=12, high=12.5, low=11, volume=500, turnover=5_000_000),
    ]

    summary = _build_price_volume_summary_payload(klines, SimpleNamespace(atr=1.5))

    assert summary["data_sources"] == ["data.kline_data", "data.stock_indicators"]
    assert summary["start_date"] == "2026-06-01"
    assert summary["end_date"] == "2026-06-05"
    assert summary["change_bases"]["window_return_pct"] == "latest_close(2026-06-05) vs start_close(2026-06-01)"
    assert summary["window_records"] == "5笔"
    assert summary["start_close"] == "10元"
    assert summary["latest_close"] == "12元"
    assert summary["window_return_pct"] == "20%"
    assert summary["drawdown_from_window_high_pct"] == "-7.69%"
    assert summary["max_drawdown_pct"] == "-38.46%"
    assert summary["latest_volume"] == "500手"
    assert summary["avg_volume_5d"] == "300手"
    assert summary["volume_vs_5d_avg_pct"] == "66.67%"
    assert summary["latest_turnover"] == "0.05亿元"
    assert summary["atr"] == "1.5元"
    assert summary["atr_pct"] == "12.5%"
    assert summary["one_atr_stop_price"] == "10.5元"


def test_margin_trend_summary_compares_leverage_and_price_drawdown():
    """两融趋势摘要应提前计算融资余额变化和相对价格回撤。"""
    margins = [
        SimpleNamespace(
            trade_date=date(2026, 6, 1 + index),
            margin_balance=balance,
            margin_buy_amount=10_000_000,
            margin_repay_amount=8_000_000,
            short_balance=1_000_000,
            margin_short_balance=balance + 1_000_000,
        )
        for index, balance in enumerate(
            [100_000_000, 110_000_000, 120_000_000, 130_000_000, 140_000_000, 150_000_000, 135_000_000]
        )
    ]
    klines = [
        SimpleNamespace(date=date(2026, 6, 1 + index), close=close)
        for index, close in enumerate([10, 11, 12, 13, 14, 15, 12])
    ]

    summary = _build_margin_trend_summary_payload(margins, klines)

    assert summary["data_sources"] == ["data.stock_margin_data", "data.kline_data"]
    assert summary["start_date"] == "2026-06-01"
    assert summary["end_date"] == "2026-06-07"
    assert summary["change_bases"]["margin_balance_change_5d_pct"] == (
        "latest_margin_balance(2026-06-07) vs margin_balance(2026-06-03)"
    )
    assert summary["window_records"] == "7笔"
    assert summary["latest_margin_balance"] == "1.35亿元"
    assert summary["margin_balance_change_5d_pct"] == "12.5%"
    assert summary["peak_margin_balance"] == "1.5亿元"
    assert summary["margin_balance_drawdown_from_peak_pct"] == "-10%"
    assert summary["price_change_since_margin_peak_pct"] == "-20%"
    assert summary["latest_price"] == "12元"
    assert summary["price_at_margin_peak"] == "15元"
    assert summary["leverage_pressure_bias"] == "crowded_not_cleared"


def test_margin_trend_summary_uses_price_window_passed_by_reader():
    """两融摘要应清楚暴露传入价格窗口，并用窗口末日收盘价对照。"""
    margins = [
        SimpleNamespace(
            trade_date=date(2026, 6, 1 + index),
            margin_balance=balance,
            margin_buy_amount=0,
            margin_repay_amount=0,
            short_balance=0,
            margin_short_balance=balance,
        )
        for index, balance in enumerate([100_000_000, 150_000_000, 120_000_000])
    ]
    klines = [
        SimpleNamespace(date=date(2026, 6, 1), close=10),
        SimpleNamespace(date=date(2026, 6, 2), close=15),
        SimpleNamespace(date=date(2026, 6, 3), close=12),
    ]

    summary = _build_margin_trend_summary_payload(margins, klines)

    assert summary["scope"] == "3 margin records from 2026-06-01 to 2026-06-03; daily closes from 2026-06-01 to 2026-06-03"
    assert summary["price_change_since_margin_peak_pct"] == "-20%"


def test_canonical_metrics_percentiles_use_raw_history_values():
    """估值分位应基于估值历史原始数值计算。"""
    records = [
        SimpleNamespace(data_date=date(2026, 7, 6), pe_ttm=20, pb=5, dividend_yield=1),
        SimpleNamespace(data_date=date(2026, 7, 5), pe_ttm=10, pb=3, dividend_yield=2),
        SimpleNamespace(data_date=date(2025, 7, 6), pe_ttm=30, pb=7, dividend_yield=0.5),
    ]

    payload = _build_percentile_payload(records, date(2026, 7, 6))

    assert payload["data_sources"] == ["data.stock_valuation_history"]
    assert payload["window_start_1y"] == "2025-07-06"
    assert payload["window_end_1y"] == "2026-07-06"
    assert payload["sample_count_1y"] == "3笔"
    assert payload["pe_ttm_percentile_1y"] == "33.33%"
    assert payload["pb_percentile_1y"] == "33.33%"
    assert payload["dividend_yield_percentile_1y"] == "33.33%"


class _FakeScalarResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _FakeExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _FakeScalarResult(self.value)


class _FakeRawMarketDB:
    def __init__(self, market, indicators):
        self.results = [_FakeExecuteResult(market), _FakeExecuteResult(indicators)]

    async def execute(self, _statement):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_price_position_summary_computes_realtime_technical_derivatives():
    """实时层应只基于数据库原始行情和技术指标派生价格位置。"""
    summary = await _build_price_position_summary(
        _FakeRawMarketDB(
            SimpleNamespace(current_price=110),
            SimpleNamespace(
                ma5=100,
                ma20=80,
                ma60=120,
                boll_upper=120,
                boll_mid=100,
                boll_lower=80,
            ),
        ),
        "000001.SZ",
    )

    assert summary["price_vs_ma5_pct"] == "10%"
    assert summary["price_vs_ma20_pct"] == "37.5%"
    assert summary["price_vs_ma60_pct"] == "-8.33%"
    assert summary["price_vs_boll_mid_pct"] == "10%"
    assert summary["price_position_in_boll_pct"] == "75%"


def test_technical_signal_summary_uses_raw_values():
    """技术状态枚举应使用原始行情和指标值。"""
    summary = _technical_signal_summary_from_raw(
        SimpleNamespace(current_price=Decimal("110")),
        SimpleNamespace(
            ma5=Decimal("100"),
            ma10="90",
            ma20=80,
            macd=Decimal("1.2"),
            macd_signal="1.0",
            rsi_6="75",
            boll_upper=Decimal("120"),
            boll_lower="80",
        ),
    )

    assert summary["status"] == "available"
    assert summary["data_sources"] == ["data.stock_realtime_market", "data.stock_indicators"]
    assert summary["ma_alignment"] == "bullish"
    assert summary["macd_relation"] == "dif_above_dea"
    assert summary["rsi6_zone"] == "overbought"
    assert summary["boll_zone"] == "inside_band"


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
    async_db_session,
    monkeypatch,
    test_db,
):
    """AI 静态上下文应包含当前用户的组合概览和绩效表现。

    Args:
        async_db_session: 异步测试数据库会话。
        monkeypatch: pytest monkeypatch 工具。
        test_db: 测试数据库会话工厂。
    """
    from app.ai.llm_engine.context.providers import PortfolioProvider
    from app.core import database as database_module

    user = User(
        username="portfolio_context_user",
        email="portfolio_context_user@example.com",
        password_hash="hashed",
    )
    async_db_session.add(user)
    await async_db_session.flush()
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
    async_db_session.add(account)
    await async_db_session.flush()
    async_db_session.add_all(
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
    await async_db_session.commit()
    monkeypatch.setattr(database_module, "AsyncSessionLocal", test_db)

    token = set_current_user_id(user.id)
    try:
        context = await AIContextService(providers=[PortfolioProvider()]).build("000001.SZ")
    finally:
        clear_current_user_id(token)

    portfolio_context = context["portfolio"]
    assert portfolio_context["status"] == "available"
    assert portfolio_context["overview"]["summary"]["total_assets"] == "430000元"
    assert portfolio_context["overview"]["summary"]["position_count"] == 1
    assert portfolio_context["overview"]["positions"][0]["stock_code"] == "000001.SZ"
    assert portfolio_context["overview"]["positions"][0]["weight"] == "27.91%"
    assert portfolio_context["performance"]["snapshot_date"] == "2026-05-22"
    assert portfolio_context["performance"]["cumulative_return"] == "2%"
    assert portfolio_context["risk_control"]["summary"] == {
        "enabled": True,
        "max_single_position_pct": "20%",
        "max_industry_position_pct": "35%",
        "min_cash_pct": "10%",
        "require_stop_loss": True,
        "stop_loss_warning_pct": "10%",
        "rule_policies": {
            "require_stop_loss": "block",
            "max_single_position_pct": "block",
            "max_industry_position_pct": "block",
            "min_cash_pct": "block",
            "stop_loss_warning_pct": "block",
        },
    }
    assert portfolio_context["risk_control"]["text"] == (
        "Portfolio risk control: enabled; max single-stock weight 20.00%; "
        "max industry weight 35.00%; minimum cash ratio 10.00%; "
        "buy orders require stop loss; stop-loss warning threshold 10.00%; "
        "rule policies {'require_stop_loss': 'block', 'max_single_position_pct': 'block', "
        "'max_industry_position_pct': 'block', 'min_cash_pct': 'block', "
        "'stop_loss_warning_pct': 'block'}."
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
        raise AssertionError("Async DB tests must use execute(), not query().")

    async def execute(self, _statement):
        return _FakeRealtimeResult(sorted(self.rows, key=lambda row: row.timestamp, reverse=True))


class _FakeRealtimeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None


class _FakeRealtimeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return _FakeRealtimeScalarResult(self.rows)


@pytest.mark.asyncio
async def test_realtime_market_prefers_latest_timestamp_row():
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

    market = await source._get_realtime_market(db, "600519.SH")

    assert market["price"] == "1401.3元"
    assert market["pct_chg"] == "-3.02%"
    assert market["volume"] == "4114309股"
    assert market["turnover"] == "58.12亿元"
