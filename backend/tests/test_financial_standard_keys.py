import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("TUSHARE_TOKEN", "test-token")
os.environ.setdefault("TUSHARE_API", "http://test.invalid")

from app.core.config import settings
from app.data.metadata.field_units import format_payload_values
from app.ai.llm_engine.context.readers import FinancialReader, FundamentalReader
from app.ai.llm_engine.context.capital_flow import CapitalFlowSource
from app.models.data_storage import (
    DragonTigerData,
    NorthboundData,
    StockFundHolding,
    StockBasic,
    StockBlockTrade,
    StockMoneyFlow,
    IndustryData,
    StockInsider,
    StockMargin,
    StockRelease,
    StockValuationHistory,
    StockSEO,
    StockTopHolders,
)


class FakeQuery:
    def __init__(self, records):
        self.records = records
        self._limit = None

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def distinct(self, *args, **kwargs):
        return self

    def limit(self, value):
        self._limit = value
        return self

    def first(self):
        return self.records[0] if self.records else None

    def all(self):
        if self._limit is None:
            return self.records
        return self.records[:self._limit]


class FakeSession:
    def __init__(self, records):
        self.records = records

    def query(self, model):
        return FakeQuery(self.records)


class ModelMapSession:
    def __init__(self, records_by_model):
        self.records_by_model = records_by_model

    def query(self, model):
        return FakeQuery(self.records_by_model.get(model, []))


def test_format_payload_values_uses_i18n_unit_suffix(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")
    assert format_payload_values("common_units", {"cny": 8.5})["cny"] == "8.5元"

    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "en")
    assert format_payload_values("common_units", {"cny": 8.5})["cny"] == "8.5 CNY"
    assert (
        format_payload_values("common_units", {"ten_thousand_cny": 4455.6})["ten_thousand_cny"]
        == "4455.6 10k CNY"
    )


def test_format_payload_values_uses_table_unit_config(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    financial = format_payload_values(
        "data.financial_indicator",
        {"net_profit_dedt_yoy": -0.27, "gross_margin": 27.4158, "gross_profit": 11779497981},
    )
    position = format_payload_values("portfolio.position", {"current_position": 0.19})
    unknown = format_payload_values("unknown.table", {"unknown_field": 8.5})

    assert financial["net_profit_dedt_yoy"] == "-0.27%"
    assert financial["gross_margin"] == 27.4158
    assert financial["gross_profit"] == 11779497981
    assert position["current_position"] == "19%"
    assert unknown["unknown_field"] == 8.5


def test_format_payload_values_recursively_uses_table_unit_config(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    payload = {
        "data": {
            "gross_margin": 27.4158,
            "q_gsprofit_margin": 27.4158,
            "gross_profit": 11779497981,
            "nested": {"net_profit_dedt_yoy": -0.27},
            "items": [{"roe": 4.0826}],
        },
        "meta": {"report_date": "2026-03-31"},
    }

    result = format_payload_values("data.financial_indicator", payload)

    assert result["data"]["gross_margin"] == 27.4158
    assert result["data"]["q_gsprofit_margin"] == 27.4158
    assert result["data"]["gross_profit"] == 11779497981
    assert result["data"]["nested"]["net_profit_dedt_yoy"] == "-0.27%"
    assert result["data"]["items"][0]["roe"] == 4.0826
    assert result["meta"]["report_date"] == "2026-03-31"


class FundHoldingFakeSession:
    def __init__(self, holdings):
        self.holdings = holdings
        self._holding_query_count = 0

    def query(self, model):
        if model is StockFundHolding.report_date:
            unique_dates = []
            seen = set()
            for holding in sorted(self.holdings, key=lambda item: item.report_date, reverse=True):
                if holding.report_date not in seen:
                    seen.add(holding.report_date)
                    unique_dates.append((holding.report_date,))
            return FakeQuery(unique_dates)
        if model is StockFundHolding:
            self._holding_query_count += 1
            sorted_holdings = sorted(self.holdings, key=lambda item: item.report_date, reverse=True)
            latest_date = sorted_holdings[0].report_date
            prev_date = next(
                (item.report_date for item in sorted_holdings if item.report_date != latest_date),
                None,
            )
            if self._holding_query_count == 1:
                return FakeQuery([item for item in self.holdings if item.report_date == latest_date])
            if self._holding_query_count == 2 and prev_date is not None:
                return FakeQuery([item for item in self.holdings if item.report_date == prev_date])
        return FakeQuery(self.holdings)


class TopHoldersFakeSession:
    def __init__(self, holders):
        self.holders = holders
        self._holder_query_count = 0

    def query(self, model):
        if model is StockTopHolders.report_date:
            unique_dates = []
            seen = set()
            for holder in sorted(self.holders, key=lambda item: item.report_date, reverse=True):
                if holder.report_date not in seen:
                    seen.add(holder.report_date)
                    unique_dates.append((holder.report_date,))
            return FakeQuery(unique_dates)
        if model is StockTopHolders:
            self._holder_query_count += 1
            latest_date = max(holder.report_date for holder in self.holders)
            if self._holder_query_count >= 1:
                return FakeQuery([holder for holder in self.holders if holder.report_date == latest_date])
        return FakeQuery(self.holders)


def test_fundamental_financial_calculation_uses_standard_keys_for_600519():
    record = SimpleNamespace(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 1, 15),
        data={
            "total_revenue": 1000.0,
            "total_revenue_yoy": 12.5,
            "net_profit_yoy": 18.8,
            "roe": 31.2,
            "gross_margin": 91.5,
            "net_profit_margin": 52.4,
            "debt_to_assets_ratio": 18.6,
            "eps": 67.8,
        },
    )

    builder = FundamentalReader()
    result = builder.financials(FakeSession([record]), "600519.SH")

    assert result["report_date"] == "2025-12-31"
    assert result["total_revenue_yoy"] == "12.5%"
    assert result["net_profit_yoy"] == "18.8%"
    assert result["roe"] == 31.2
    assert result["gross_margin"] == 91.5
    assert result["net_margin"] == 52.4
    assert result["debt_to_asset"] == 18.6
    assert result["eps"] == 67.8


def test_fundamental_financials_returns_none_for_missing_values_instead_of_zero():
    record = SimpleNamespace(
        report_date=date(2025, 9, 30),
        announcement_date=date(2025, 10, 30),
        data={"unrelated_metric": 1.0},
    )

    builder = FundamentalReader()
    result = builder.financials(FakeSession([record]), "688111.SH")

    assert result["total_revenue_yoy"] is None
    assert result["net_profit_yoy"] is None
    assert result["roe"] is None
    assert result["gross_margin"] is None
    assert result["net_margin"] is None
    assert result["debt_to_asset"] is None
    assert result["eps"] is None


def test_fundamental_financials_falls_back_to_cogs_ratio_for_gross_margin():
    record = SimpleNamespace(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 1, 15),
        data={
            "total_revenue": 1000.0,
            "cogs_ratio": 8.7066,
        },
    )

    builder = FundamentalReader()
    result = builder.financials(FakeSession([record]), "600519.SH")

    assert result["gross_margin"] == 91.29


def test_fundamental_financials_falls_back_to_operating_cost_for_gross_margin():
    record = SimpleNamespace(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 1, 15),
        data={
            "operating_income": 1000.0,
            "operating_cost": 320.0,
        },
    )

    builder = FundamentalReader()
    result = builder.financials(FakeSession([record]), "600519.SH")

    assert result["gross_margin"] == 68


def test_fundamental_financials_keeps_small_percentage_as_reported():
    record = SimpleNamespace(
        report_date=date(2026, 3, 31),
        announcement_date=date(2026, 4, 29),
        data={"net_profit_dedt_yoy": -0.27, "net_profit_yoy": 3.0096},
    )

    builder = FundamentalReader()
    result = builder.source._get_financial_trend(FakeSession([record, record]), "000651.SZ")

    assert result["growth_trend"]["series"]["net_profit_dedt_yoy"][0]["net_profit_dedt_yoy"] == "-0.27%"


def test_financial_history_and_dupont_use_standard_keys_for_601988():
    latest_record = SimpleNamespace(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 1, 10),
        data_source="tushare",
        data={
            "total_revenue": 2000.0,
            "net_profit": 500.0,
            "total_assets": 10000.0,
            "total_equity": 1200.0,
            "total_revenue_yoy": 5.6,
            "net_profit_yoy": 4.2,
            "gross_margin": 48.3,
            "roe": 9.7,
            "net_profit_margin": 25.0,
            "debt_to_assets_ratio": 88.0,
            "asset_turnover": 0.2,
            "equity_multiplier": 8.3,
            "tr_yoy": 999.0,
            "np_yoy": 999.0,
            "n_income": 999.0,
            "gpm": 999.0,
            "al_ratio": 999.0,
        },
    )
    prev_record = SimpleNamespace(
        report_date=date(2025, 9, 30),
        announcement_date=date(2025, 10, 30),
        data_source="tushare",
        data={
            "total_revenue": 1900.0,
            "net_profit": 460.0,
            "total_revenue_yoy": 4.9,
            "net_profit_yoy": 3.8,
            "gross_margin": 47.5,
            "roe": 9.2,
        },
    )

    financial_builder = FinancialReader()
    history = financial_builder.historical_summary(
        FakeSession([latest_record, prev_record]),
        "601988.SH",
    )

    assert history[0]["meta"]["report_date"] == "2025-12-31"
    assert history[0]["data"]["net_profit"] == 500.0
    assert history[0]["data"]["total_revenue_yoy"] == 5.6
    assert history[0]["data"]["net_profit_yoy"] == 4.2
    assert history[0]["data"]["gross_margin"] == 48.3

def test_top_holders_use_latest_period_only_and_mark_staleness():
    latest_date = date.today() - timedelta(days=220)
    older_date = latest_date - timedelta(days=90)
    records = [
        SimpleNamespace(
            stock_code="600519.SH",
            report_date=latest_date,
            holder_name="香港中央结算有限公司",
            holder_type="QFII",
            hold_ratio=8.2,
            change="增加(1200股)",
            holder_rank=1,
            hold_amount=1200.0,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            report_date=latest_date,
            holder_name="张三",
            holder_type="个人",
            hold_ratio=5.1,
            change="减少(300股)",
            holder_rank=2,
            hold_amount=800.0,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            report_date=older_date,
            holder_name="旧季度机构股东",
            holder_type="机构",
            hold_ratio=20.0,
            change="增加(100股)",
            holder_rank=1,
            hold_amount=3000.0,
        ),
    ]

    builder = FundamentalReader()
    result = builder.top_holders(TopHoldersFakeSession(records), "600519.SH")

    assert result["overview"]["report_date"] == str(latest_date)
    assert result["overview"]["reference_status"] == "stale"
    assert result["overview"]["age_days"].endswith("天")
    assert result["overview"]["institutional_count"] == 1
    assert result["concentration"]["total_hold_ratio_pct"] == "13.3%"
    assert result["change"]["increasing_holder_count"] == 1
    assert result["change"]["decreasing_holder_count"] == 1
    assert len(result["holders_latest"]) == 2


def test_top_holder_change_labels_support_numeric_and_text_values():
    builder = FundamentalReader()

    assert builder.normalize_holder_change_label(1500) == "增加"
    assert builder.normalize_holder_change_label(-22.5) == "减少"
    assert builder.normalize_holder_change_label(0) == "不变"
    assert builder.normalize_holder_change_label("增加(10股)") == "增加(10股)"


def test_forecast_returns_structured_latest_guidance():
    ann_date = date.today() - timedelta(days=90)
    report_date = date(2025, 12, 31)
    record = SimpleNamespace(
        report_date=report_date,
        ann_date=ann_date,
        forecast_type="预增",
        net_profit_min=120000.0,
        net_profit_max=150000.0,
        prev_year_profit=90000.0,
        growth_min=20.0,
        growth_max=45.0,
        forecast_content="业绩增长主要来自产品结构升级和费用控制改善。",
    )

    builder = FundamentalReader()
    result = builder.forecast(FakeSession([record]), "600519.SH")

    assert result["overview"]["window"] == "latest"
    assert result["overview"]["forecast_type"] == "预增"
    assert result["overview"]["reference_status"] == "active"
    assert "unit" not in result["profit_guidance_latest"]
    assert result["profit_guidance_latest"]["midpoint"] == "135000万元"
    assert result["growth_guidance_latest"]["window"] == "latest"
    assert "unit" not in result["growth_guidance_latest"]
    assert result["growth_guidance_latest"]["midpoint_pct"] == "32.5%"
    assert result["growth_guidance_latest"]["direction_label"] == "positive"
    assert result["signal"]["momentum_label"] == "positive"
    assert result["risk_flags"] == []
    assert "产品结构升级" in result["content_summary"]


def test_northbound_flow_returns_structured_foreign_sentiment():
    latest = SimpleNamespace(
        stock_code="600519.SH",
        date=date.today() - timedelta(days=20),
        hold_shares=12_500_000.0,
        hold_value=18_750_000_000.0,
        hold_ratio=1.85,
        close_price=1500.0,
        change_percent=1.2,
        net_buy_volume=350_000.0,
        net_buy_amount=68_000_000.0,
        hold_value_change=125_000_000.0,
    )
    previous = SimpleNamespace(
        stock_code="600519.SH",
        date=date.today() - timedelta(days=120),
        hold_shares=11_800_000.0,
        hold_value=17_900_000_000.0,
        hold_ratio=1.62,
        close_price=1480.0,
        change_percent=-0.5,
        net_buy_volume=-50_000.0,
        net_buy_amount=-8_000_000.0,
        hold_value_change=-12_000_000.0,
    )
    older = SimpleNamespace(
        stock_code="600519.SH",
        date=date.today() - timedelta(days=400),
        hold_shares=10_400_000.0,
        hold_value=15_300_000_000.0,
        hold_ratio=1.35,
        close_price=1350.0,
        change_percent=0.4,
        net_buy_volume=80_000.0,
        net_buy_amount=12_000_000.0,
        hold_value_change=18_000_000.0,
    )

    builder = FundamentalReader()
    result = builder.northbound_flow(
        ModelMapSession({NorthboundData: [latest, previous, older]}),
        "600519.SH",
    )

    assert result["overview"]["window"] == "latest_12_records"
    assert result["overview"]["record_count"] == 3
    assert result["overview"]["reference_status"] == "active"
    assert result["latest_position"]["hold_ratio_pct"] == "1.85%"
    assert result["quarter_change"]["hold_ratio_change_pct"] == "0.23%"
    assert result["quarter_change"]["net_buy_amount_10k_cny"] == 6800.0
    assert result["signal"]["flow_label"] == "accumulating"
    assert result["signal"]["foreign_sentiment_label"] == "positive"
    assert result["recent_records"][0]["date"] == str(latest.date)
    assert result["recent_records"][2]["hold_ratio_pct"] == "1.35%"


def test_capital_flow_northbound_formats_decimal_holding_ratio_as_percent():
    latest = NorthboundData(
        stock_code="600519.SH",
        date=date.today() - timedelta(days=20),
        hold_shares=12_500_000.0,
        hold_ratio=4.38,
        net_buy_amount=68_000_000.0,
        net_buy_volume=350_000.0,
    )
    previous = NorthboundData(
        stock_code="600519.SH",
        date=date.today() - timedelta(days=120),
        hold_shares=12_000_000.0,
        hold_ratio=4.28,
        net_buy_amount=8_000_000.0,
        net_buy_volume=50_000.0,
    )

    source = CapitalFlowSource()
    session = ModelMapSession({NorthboundData: [latest, previous]})

    latest_result = source._get_northbound(session, "600519.SH")
    trend_result = source._get_northbound_trend(session, "600519.SH")

    assert latest_result["hold_ratio"] == "4.38%"
    assert trend_result["latest_hold_ratio"] == "4.38%"
    assert trend_result["prev_hold_ratio"] == "4.28%"
    assert trend_result["ratio_change"] == "0.1%"


def test_dragon_tiger_activity_returns_structured_trading_signal():
    records = [
        SimpleNamespace(
            stock_code="600519.SH",
            trade_date=date.today() - timedelta(days=1),
            stock_name="贵州茅台",
            net_buy_amount=42_000_000.0,
            buy_amount=120_000_000.0,
            sell_amount=78_000_000.0,
            price_change_percent=9.8,
            listing_reason="日涨幅偏离值达7%",
            net_buy_ratio=4.6,
            post_1_day_price_change_percent=1.2,
            post_5_day_price_change_percent=3.4,
        ),
        SimpleNamespace(
            stock_code="002594.SZ",
            stock_name="比亚迪",
            trade_date=date.today() - timedelta(days=2),
            net_buy_amount=18_000_000.0,
            buy_amount=95_000_000.0,
            sell_amount=77_000_000.0,
            price_change_percent=5.1,
            listing_reason="换手率异常",
            net_buy_ratio=2.1,
            post_1_day_price_change_percent=-0.3,
            post_5_day_price_change_percent=1.1,
        ),
        SimpleNamespace(
            stock_code="300750.SZ",
            stock_name="宁德时代",
            trade_date=date.today() - timedelta(days=3),
            net_buy_amount=-6_000_000.0,
            buy_amount=66_000_000.0,
            sell_amount=72_000_000.0,
            price_change_percent=-2.4,
            listing_reason="振幅值达15%",
            net_buy_ratio=-0.9,
            post_1_day_price_change_percent=0.5,
            post_5_day_price_change_percent=-1.6,
        ),
    ]

    builder = FundamentalReader()
    result = builder.dragon_tiger_activity(
        ModelMapSession({DragonTigerData: records}),
        "600519.SH",
    )

    assert result["overview"]["window"] == "3day"
    assert result["overview"]["scope"] == "market_wide"
    assert result["overview"]["event_count"] == 3
    assert result["overview"]["unique_stock_count"] == 3
    assert result["overview"]["unique_trade_date_count"] == 3
    assert result["overview"]["cumulative_net_buy_10k_cny"] == 5400.0
    assert result["signal"]["activity_label"] == "sporadic"
    assert result["signal"]["market_sentiment_label"] == "market_buying_bias"
    assert result["all_records"][0]["stock_code"] == "600519.SH"
    assert result["all_records"][1]["stock_code"] == "002594.SZ"
    assert result["aggregates"]["positive_event_count"] == 2
    assert result["aggregates"]["negative_event_count"] == 1
    assert "Repeated Dragon Tiger appearances indicate elevated short-term trading intensity" in result["risk_flags"]


def test_block_trade_values_append_units_without_extra_unit_fields():
    trades = [
        SimpleNamespace(
            stock_code="000651.SZ",
            trade_date=date(2026, 6, 4),
            price=37.13,
            volume=120.0,
            amount=4455.6,
            premium_rate=-3.25,
            buyer="机构专用席位",
            seller="卖方营业部",
        )
    ]

    result = CapitalFlowSource()._get_block_trade(
        ModelMapSession({StockBlockTrade: trades}),
        "000651.SZ",
    )

    assert result["total_amount"] == 4455.6
    assert result["avg_premium"] == -3.25
    assert result["recent_trades"][0]["price"] == 37.13
    assert result["recent_trades"][0]["volume"] == "120万股"
    assert result["recent_trades"][0]["amount"] == 4455.6
    assert result["recent_trades"][0]["premium_rate"] == -3.25
    assert "amount_unit" not in result["recent_trades"][0]


def test_stock_money_flow_converts_yuan_amounts_to_10k_cny_unit():
    record = StockMoneyFlow(
        stock_code="000001.SZ",
        trade_date=date(2026, 6, 5),
        net_inflow_main=12_000_000,
        net_inflow_small=-1_000_000,
        net_inflow_medium=2_500_000,
        net_inflow_huge=5_000_000,
        net_inflow_main_3d=36_000_000,
        net_inflow_main_5d=60_000_000,
        net_inflow_main_10d=120_000_000,
        net_inflow_ratio_main=3.5,
        close_price=12.34,
        change_pct=-1.2,
    )

    source = CapitalFlowSource()
    result = source._get_money_flow(ModelMapSession({StockMoneyFlow: [record]}), "000001.SZ")
    trend = source._get_money_flow_trend(ModelMapSession({StockMoneyFlow: [record]}), "000001.SZ")

    assert result["net_inflow_main"] == "1200万元"
    assert result["net_inflow_retail"] == "150万元"
    assert result["net_inflow_huge"] == "500万元"
    assert result["net_inflow_main_3d"] == "3600万元"
    assert result["net_inflow_main_5d"] == "6000万元"
    assert result["net_inflow_main_10d"] == "12000万元"
    assert trend[0]["net_inflow_main"] == "1200万元"


def test_fund_holding_sorts_top_funds_and_keeps_tushare_unsupported_units_raw():
    latest_date = date(2025, 12, 31)
    prev_date = date(2025, 9, 30)
    records = [
        SimpleNamespace(
            stock_code="600519.SH",
            report_date=latest_date,
            fund_code="F003",
            fund_name="Gamma Fund",
            hold_market_value=1_000_000.0,
            hold_ratio_stock=0.10,
            hold_ratio_fund=9.5,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            report_date=latest_date,
            fund_code="F001",
            fund_name="Alpha Fund",
            hold_market_value=9_000_000.0,
            hold_ratio_stock=0.90,
            hold_ratio_fund=3.0,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            report_date=latest_date,
            fund_code="F002",
            fund_name="Beta Fund",
            hold_market_value=4_000_000.0,
            hold_ratio_stock=0.40,
            hold_ratio_fund=6.2,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            report_date=prev_date,
            fund_code="F010",
            fund_name="Prev Fund",
            hold_market_value=10_000_000.0,
            hold_ratio_stock=1.00,
            hold_ratio_fund=5.5,
        ),
    ]

    builder = FundamentalReader()
    result = builder.fund_holding(FundHoldingFakeSession(records), "600519.SH")

    assert result["overview"]["fund_count"] == 3
    assert result["overview"]["total_hold_value_10k_cny"] == 1400.0
    assert result["overview"]["total_hold_ratio_pct"] == 1.4
    assert result["concentration"]["top5_hold_ratio_pct"] == 1.4
    assert result["concentration"]["top5_concentration_pct"] == 100.0
    assert result["conviction"]["conviction_level"] != ""
    assert result["conviction"]["high_conviction_fund_count"] == 1
    assert result["top_funds_latest"][0]["fund_name"] == "Alpha Fund"
    assert result["top_funds_latest"][0]["hold_value_10k_cny"] == 900.0
    assert result["top_funds_latest"][1]["fund_name"] == "Beta Fund"
    assert result["top_funds_latest"][2]["fund_name"] == "Gamma Fund"
    assert result["previous_report_delta"]["hold_ratio_change_pct"] == 0.4
    assert result["previous_report_delta"]["market_value_change_10k_cny"] == 400.0
    assert "price move" in result["previous_report_delta"]["market_value_change_note"]


def test_industry_rank_uses_latest_snapshot_and_structured_signal():
    stock = SimpleNamespace(stock_code="600519.SH", industry="白酒")
    older = SimpleNamespace(
        board_name="白酒",
        rank=12,
        latest_price=1200.0,
        change_percent=0.8,
        total_market_cap=5_000_000_000.0,
        rising_stocks_count=6,
        falling_stocks_count=5,
        leading_stock_name="旧龙头",
        leading_stock_change_percent=1.2,
        timestamp=datetime(2026, 3, 20, 10, 0, 0),
        updated_at=datetime(2026, 3, 20, 10, 0, 0),
        created_at=datetime(2026, 3, 20, 10, 0, 0),
    )
    latest = SimpleNamespace(
        board_name="白酒",
        rank=3,
        latest_price=1250.0,
        change_percent=2.6,
        total_market_cap=5_600_000_000.0,
        rising_stocks_count=12,
        falling_stocks_count=3,
        leading_stock_name="贵州茅台",
        leading_stock_change_percent=4.5,
        timestamp=datetime(2026, 3, 22, 15, 0, 0),
        updated_at=datetime(2026, 3, 22, 15, 0, 0),
        created_at=datetime(2026, 3, 22, 15, 0, 0),
    )

    builder = FundamentalReader()
    result = builder.industry_rank(
        ModelMapSession({StockBasic: [stock], IndustryData: [latest, older]}),
        "600519.SH",
    )

    assert result["overview"]["industry"] == "白酒"
    assert result["overview"]["window"] == "latest"
    assert result["overview"]["board_rank"] == 3
    assert result["overview"]["change_pct"] == 2.6
    assert result["breadth"]["rising_stocks_count"] == 12
    assert result["breadth"]["falling_stocks_count"] == 3
    assert result["breadth"]["advance_decline_ratio"] == 4.0
    assert result["signal"]["strength_label"] == "leading"
    assert result["leader"]["stock_name"] == "贵州茅台"
    assert result["market_cap"]["total_market_cap_cny"] == 5600000000.0


def test_insider_activity_returns_structured_signal_and_role_breakdown():
    today = date.today()
    records = [
        SimpleNamespace(
            stock_code="600519.SH",
            trade_date=today - timedelta(days=10),
            ann_date=today - timedelta(days=8),
            insider_name="张三",
            relationship="董事",
            change_type="减持",
            change_shares=200000,
            change_avg_price=1500.0,
            change_ratio=0.015,
            shares_after_change=800000,
            ratio_after_change=0.06,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            trade_date=today - timedelta(days=25),
            ann_date=today - timedelta(days=24),
            insider_name="李四",
            relationship="控股股东",
            change_type="增持",
            change_shares=50000,
            change_avg_price=1480.0,
            change_ratio=0.004,
            shares_after_change=1200000,
            ratio_after_change=0.09,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            trade_date=today - timedelta(days=40),
            ann_date=today - timedelta(days=38),
            insider_name="王五",
            relationship="董事",
            change_type="减持",
            change_shares=100000,
            change_avg_price=1490.0,
            change_ratio=0.008,
            shares_after_change=500000,
            ratio_after_change=0.04,
        ),
    ]

    builder = FundamentalReader()
    result = builder.insider_activity(
        ModelMapSession({StockInsider: records}),
        "600519.SH",
        months=6,
    )

    assert result["overview"]["window"] == "6month"
    assert result["overview"]["record_count"] == 3
    assert result["overview"]["net_change_shares"] == -250000
    assert result["overview"]["net_change_value_cny"] == -375000000.0
    assert result["overview"]["buy_count"] == 1
    assert result["overview"]["sell_count"] == 2
    assert result["signal"]["direction_label"] == "net_selling"
    assert result["signal"]["intensity_label"] == "heavy_selling"
    assert result["role_breakdown"][0]["relationship"] == "董事"
    assert result["role_breakdown"][0]["net_change_shares"] == -300000
    assert result["recent_events"][0]["insider_name"] == "张三"
    assert result["recent_events"][0]["transaction_value_cny"] == 300000000.0
    assert "Repeated insider selling within the window" in result["risk_flags"]
    assert "Net insider flow is negative in both shares and cash value" in result["risk_flags"]


def test_lockup_release_returns_structured_upcoming_pressure():
    today = date.today()
    records = [
        SimpleNamespace(
            stock_code="600519.SH",
            release_date=today - timedelta(days=20),
            release_type="定增",
            release_shares=1_000_000,
            release_market_value=30_000.0,
            ratio_to_total=0.8,
            ratio_to_float=1.2,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            release_date=today + timedelta(days=15),
            release_type="首发原股东",
            release_shares=5_000_000,
            release_market_value=120_000.0,
            ratio_to_total=2.5,
            ratio_to_float=3.2,
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            release_date=today + timedelta(days=90),
            release_type="股权激励",
            release_shares=2_000_000,
            release_market_value=40_000.0,
            ratio_to_total=1.0,
            ratio_to_float=2.0,
        ),
    ]

    builder = FundamentalReader()
    result = builder.lockup_release(
        ModelMapSession({StockRelease: records}),
        "600519.SH",
    )

    assert result["overview"]["window"] == "past90day_to_next12month"
    assert result["overview"]["recent_release_count"] == 1
    assert result["overview"]["upcoming_release_count"] == 2
    assert result["overview"]["next_release_date"] == str(today + timedelta(days=15))
    assert result["overview"]["total_upcoming_ratio_to_float_pct"] == 5.2
    assert result["overview"]["total_upcoming_market_value_10k_cny"] == 160000.0
    assert result["signal"]["pressure_label"] == "elevated"
    assert result["upcoming_releases"][0]["days_until_release"] == "15天"
    assert result["recent_releases"][0]["days_since_release"] == "20天"
    assert "Near-term lockup release may pressure float supply" in result["risk_flags"]
    assert "Upcoming lockup ratio is meaningful relative to float" in result["risk_flags"]


def test_seo_history_returns_structured_dilution_signal():
    today = date.today()
    records = [
        SimpleNamespace(
            stock_code="600519.SH",
            announce_date=today - timedelta(days=120),
            issue_date=today - timedelta(days=90),
            issue_price=1320.5,
            issue_volume=5_000_000.0,
            raise_amount=6_602_500_000.0,
            issue_object="战略投资者A, 战略投资者B",
            lock_period="12个月",
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            announce_date=today - timedelta(days=800),
            issue_date=today - timedelta(days=760),
            issue_price=1180.0,
            issue_volume=3_000_000.0,
            raise_amount=3_540_000_000.0,
            issue_object="财务投资者",
            lock_period="6个月",
        ),
        SimpleNamespace(
            stock_code="600519.SH",
            announce_date=today - timedelta(days=1400),
            issue_date=today - timedelta(days=1360),
            issue_price=980.0,
            issue_volume=2_000_000.0,
            raise_amount=1_960_000_000.0,
            issue_object="历史对象",
            lock_period="12个月",
        ),
    ]

    builder = FundamentalReader()
    result = builder.seo_history(
        ModelMapSession({StockSEO: records}),
        "600519.SH",
    )

    assert result["overview"]["window"] == "latest5_alltime"
    assert result["overview"]["record_count"] == 3
    assert result["overview"]["recent_3year_count"] == 2
    assert result["overview"]["latest_reference_date"] == str(today - timedelta(days=120))
    assert result["signal"]["dilution_label"] == "repeated_equity_financing"
    assert result["signal"]["recency_label"] == "recent"
    assert result["recent_offerings"][0]["issue_price_cny"] == 1320.5
    assert result["recent_offerings"][0]["issue_object_summary"] == "战略投资者A, 战略投资者B"
    assert "Repeated SEO activity within the last 3 years" in result["risk_flags"]
    assert "Recent equity financing may still weigh on dilution expectations" in result["risk_flags"]


def test_margin_analysis_returns_structured_leverage_signal():
    records = [
        SimpleNamespace(
            stock_code="600519.SH",
            trade_date=date(2026, 3, 20),
            margin_balance=120_000_000.0,
            short_balance=4_000_000.0,
            margin_buy_amount=15_000_000.0,
            margin_repay_amount=9_000_000.0,
        ),
        SimpleNamespace(stock_code="600519.SH", trade_date=date(2026, 3, 19), margin_balance=118_000_000.0, short_balance=4_200_000.0, margin_buy_amount=12_000_000.0, margin_repay_amount=8_000_000.0),
        SimpleNamespace(stock_code="600519.SH", trade_date=date(2026, 3, 18), margin_balance=112_000_000.0, short_balance=4_500_000.0, margin_buy_amount=11_000_000.0, margin_repay_amount=9_500_000.0),
        SimpleNamespace(stock_code="600519.SH", trade_date=date(2026, 3, 17), margin_balance=108_000_000.0, short_balance=4_800_000.0, margin_buy_amount=10_000_000.0, margin_repay_amount=9_000_000.0),
        SimpleNamespace(stock_code="600519.SH", trade_date=date(2026, 3, 14), margin_balance=100_000_000.0, short_balance=5_000_000.0, margin_buy_amount=9_000_000.0, margin_repay_amount=8_500_000.0),
    ]
    valuation = SimpleNamespace(stock_code="600519.SH", data_date=date(2026, 3, 20), total_market_value=1_000_000.0)

    builder = FundamentalReader()
    result = builder.margin_analysis(
        ModelMapSession({StockMargin: records, StockValuationHistory: [valuation]}),
        "600519.SH",
    )

    assert result["overview"]["trade_date"] == "2026-03-20"
    assert result["overview"]["market_cap_cny"] == "1000000元"
    assert result["overview"]["margin_balance_cny"] == "120000000元"
    assert result["overview"]["short_balance_cny"] == "4000000元"
    assert result["overview"]["margin_ratio_to_market_cap_pct"] == "12000%"
    assert result["signal"]["margin_short_ratio"] == 30.0
    assert result["signal"]["leverage_label"] == "high_leverage"
    assert result["signal"]["positioning_label"] == "long_crowded"
    assert result["signal"]["flow_label"] == "rising_fast"
    assert result["trend"]["window"] == "5tradingday"
    assert result["trend"]["margin_balance_change_5d_pct"] == "20%"
    assert "Margin balance is high relative to market cap" in result["risk_flags"]
    assert "Margin positioning is skewed heavily to the long side" in result["risk_flags"]


def test_financial_trend_uses_gross_margin_fallbacks():
    records = [
        SimpleNamespace(
            report_date=date(2025, 12, 31),
            announcement_date=date(2026, 1, 10),
            updated_at=date(2026, 1, 10),
            data={"cogs_ratio": 8.0, "roe": 20.0, "total_revenue_yoy": 18.0, "net_profit_yoy": 20.0, "net_profit_dedt_yoy": 19.0, "debt_to_assets_ratio": 30.0},
        ),
        SimpleNamespace(
            report_date=date(2025, 9, 30),
            announcement_date=date(2025, 10, 30),
            updated_at=date(2025, 10, 30),
            data={"cogs_ratio": 10.0, "roe": 18.0, "total_revenue_yoy": 15.0, "net_profit_yoy": 16.0, "net_profit_dedt_yoy": 15.0, "debt_to_assets_ratio": 32.0},
        ),
        SimpleNamespace(
            report_date=date(2025, 6, 30),
            announcement_date=date(2025, 8, 30),
            updated_at=date(2025, 8, 30),
            data={"operating_income": 1000.0, "operating_cost": 150.0, "roe": 16.0, "total_revenue_yoy": 12.0, "net_profit_yoy": 13.0, "net_profit_dedt_yoy": 12.0, "debt_to_assets_ratio": 34.0},
        ),
        SimpleNamespace(
            report_date=date(2025, 3, 31),
            announcement_date=date(2025, 4, 30),
            updated_at=date(2025, 4, 30),
            data={"gross_margin": 80.0, "roe": 15.0, "total_revenue_yoy": 10.0, "net_profit_yoy": 9.0, "net_profit_dedt_yoy": 8.0, "debt_to_assets_ratio": 36.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 12, 31),
            announcement_date=date(2025, 1, 30),
            updated_at=date(2025, 1, 30),
            data={"gross_margin": 78.0, "roe": 14.0, "total_revenue_yoy": 8.0, "net_profit_yoy": 7.0, "net_profit_dedt_yoy": 7.0, "debt_to_assets_ratio": 38.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 9, 30),
            announcement_date=date(2024, 10, 30),
            updated_at=date(2024, 10, 30),
            data={"gross_margin": 76.0, "roe": 13.0, "total_revenue_yoy": 7.0, "net_profit_yoy": 6.0, "net_profit_dedt_yoy": 6.0, "debt_to_assets_ratio": 40.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 6, 30),
            announcement_date=date(2024, 8, 30),
            updated_at=date(2024, 8, 30),
            data={"gross_margin": 74.0, "roe": 12.0, "total_revenue_yoy": 6.0, "net_profit_yoy": 5.0, "net_profit_dedt_yoy": 5.0, "debt_to_assets_ratio": 42.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 3, 31),
            announcement_date=date(2024, 4, 30),
            updated_at=date(2024, 4, 30),
            data={"gross_margin": 72.0, "roe": 11.0, "total_revenue_yoy": 5.0, "net_profit_yoy": 4.0, "net_profit_dedt_yoy": 4.0, "debt_to_assets_ratio": 44.0},
        ),
    ]

    builder = FundamentalReader()
    result = builder.financial_trend(FakeSession(records), "600519.SH")

    assert result["overview"]["level"] == "Strong"
    assert result["overview"]["quarters_analyzed"] == 8
    assert result["overview"]["recent_quarters_used"] == 8
    assert result["profitability_trend"]["direction"] == "improving"
    assert result["profitability_trend"]["series"]["gross_margin"][0]["gross_margin"] == 92.0
    assert result["profitability_trend"]["series"]["gross_margin"][1]["gross_margin"] == 90.0
    assert result["profitability_trend"]["series"]["gross_margin"][2]["gross_margin"] == 85.0
    assert result["profitability_trend"]["series"]["gross_margin"][3]["gross_margin"] == 80.0
    assert result["profitability_trend"]["series"]["gross_margin"][7]["gross_margin"] == 72.0
    assert result["growth_trend"]["direction"] == "improving"
    assert result["leverage_trend"]["direction"] == "improving"
    assert result["recent_quarters"][0]["roe"] == 20.0
    assert result["recent_quarters"][0]["gross_margin"] == 92.0
    assert result["recent_quarters"][7]["debt_to_asset"] == 44.0


def test_financial_trend_keeps_extreme_growth_rates_and_uses_non_null_latest():
    records = [
        SimpleNamespace(
            report_date=date(2025, 12, 31),
            announcement_date=date(2026, 1, 10),
            updated_at=date(2026, 1, 10),
            data={"roe": 18.0, "gross_margin": 60.0, "total_revenue_yoy": 1500.0, "net_profit_dedt_yoy": 1400.0, "debt_to_assets_ratio": 28.0},
        ),
        SimpleNamespace(
            report_date=date(2025, 9, 30),
            announcement_date=date(2025, 10, 30),
            updated_at=date(2025, 10, 30),
            data={"roe": 17.0, "gross_margin": 59.0, "total_revenue_yoy": 1300.0, "net_profit_yoy": 1200.0, "net_profit_dedt_yoy": 1100.0, "debt_to_assets_ratio": 30.0},
        ),
        SimpleNamespace(
            report_date=date(2025, 6, 30),
            announcement_date=date(2025, 8, 30),
            updated_at=date(2025, 8, 30),
            data={"roe": 16.0, "gross_margin": 58.0, "total_revenue_yoy": 900.0, "net_profit_yoy": 800.0, "net_profit_dedt_yoy": 780.0, "debt_to_assets_ratio": 32.0},
        ),
        SimpleNamespace(
            report_date=date(2025, 3, 31),
            announcement_date=date(2025, 4, 30),
            updated_at=date(2025, 4, 30),
            data={"roe": 15.0, "gross_margin": 57.0, "total_revenue_yoy": 700.0, "net_profit_yoy": 600.0, "net_profit_dedt_yoy": 580.0, "debt_to_assets_ratio": 34.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 12, 31),
            announcement_date=date(2025, 1, 30),
            updated_at=date(2025, 1, 30),
            data={"roe": 14.0, "gross_margin": 56.0, "total_revenue_yoy": 600.0, "net_profit_yoy": 500.0, "net_profit_dedt_yoy": 480.0, "debt_to_assets_ratio": 36.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 9, 30),
            announcement_date=date(2024, 10, 30),
            updated_at=date(2024, 10, 30),
            data={"roe": 13.0, "gross_margin": 55.0, "total_revenue_yoy": 500.0, "net_profit_yoy": 400.0, "net_profit_dedt_yoy": 380.0, "debt_to_assets_ratio": 38.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 6, 30),
            announcement_date=date(2024, 8, 30),
            updated_at=date(2024, 8, 30),
            data={"roe": 12.0, "gross_margin": 54.0, "total_revenue_yoy": 400.0, "net_profit_yoy": 300.0, "net_profit_dedt_yoy": 280.0, "debt_to_assets_ratio": 40.0},
        ),
        SimpleNamespace(
            report_date=date(2024, 3, 31),
            announcement_date=date(2024, 4, 30),
            updated_at=date(2024, 4, 30),
            data={"roe": 11.0, "gross_margin": 53.0, "total_revenue_yoy": 200.0, "net_profit_yoy": 100.0, "net_profit_dedt_yoy": 90.0, "debt_to_assets_ratio": 42.0},
        ),
    ]

    builder = FundamentalReader()
    result = builder.financial_trend(FakeSession(records), "300750.SZ")

    assert result["growth_trend"]["direction"] == "improving"
    assert result["growth_trend"]["series"]["total_revenue_yoy"][0]["total_revenue_yoy"] == "1500%"
    assert result["growth_trend"]["latest"]["net_profit_yoy"] == "1200%"
    assert result["growth_trend"]["change_vs_oldest"] == 1100.0
    assert result["growth_trend"]["quarters_used"] == 7


def test_financial_context_localizes_raw_data_keys_by_system_language(monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM_LANGUAGE", "zh")

    localized = FinancialReader().localize_raw_data(
        {
            "eps": 1.74,
            "roe": 11.5953,
            "gross_margin": 21.9753,
            "capital_reserve_ps": 1.7286,
        },
        "data.financial_indicator",
    )

    assert localized["每股收益"] == 1.74
    assert localized["净资产收益率"] == 11.5953
    assert localized["毛利率"] == 21.9753
    assert localized["每股资本公积金"] == 1.7286


def test_financial_context_recursively_localizes_data_and_meta(monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM_LANGUAGE", "zh")

    localized = FinancialReader().localize_raw_data(
        {
            "data": {
                "eps": 0.38,
                "bps": 3.4323,
                "roe": 11.5594,
            },
            "meta": {
                "report_date": "2025-09-30",
                "announcement_date": "2025-10-28",
                "data_source": "tushare",
            },
        },
        "data.financial_indicator",
    )

    assert localized["data"]["每股收益"] == 0.38
    assert localized["data"]["每股净资产"] == 3.4323
    assert localized["data"]["净资产收益率"] == 11.5594
    assert localized["meta"]["报告期"] == "2025-09-30"
    assert localized["meta"]["公告日期"] == "2025-10-28"
    assert localized["meta"]["数据源"] == "tushare"


def test_financial_context_includes_localized_balance_sheet_data(monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM_LANGUAGE", "zh")

    income_record = SimpleNamespace(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 1, 10),
        updated_at=date(2026, 1, 10),
        report_type="合并报表",
        currency="CNY",
        is_audit=True,
        data_source="tushare",
        data={
            "total_revenue": 1000.0,
            "net_profit": 500.0,
        },
    )
    balance_record = SimpleNamespace(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 1, 10),
        updated_at=date(2026, 1, 10),
        report_type="合并报表",
        currency="CNY",
        is_audit=True,
        data_source="tushare",
        data={
            "money_cap": 123.45,
            "total_assets": 678.9,
        },
    )
    cashflow_record = SimpleNamespace(
        report_date=date(2025, 12, 31),
        announcement_date=date(2026, 1, 10),
        updated_at=date(2026, 1, 10),
        report_type="合并报表",
        currency="CNY",
        is_audit=True,
        data_source="tushare",
        data={
            "n_cashflow_act": 321.0,
            "n_cashflow_inv_act": -45.6,
        },
    )

    builder = FinancialReader()
    _ = builder.latest_income_statement(FakeSession([income_record]), "600519.SH")
    _ = builder.income_statement_summary(FakeSession([income_record]), "600519.SH")
    latest_balance = builder.latest_balance_sheet(FakeSession([balance_record]), "600519.SH")
    balance_history = builder.balance_sheet_history(FakeSession([balance_record]), "600519.SH")
    latest_cashflow = builder.latest_cashflow_statement(FakeSession([cashflow_record]), "600519.SH")
    cashflow_history = builder.cashflow_statement_history(FakeSession([cashflow_record]), "600519.SH")

    assert latest_balance["data"]["货币资金"] == 123.45
    assert latest_balance["data"]["资产总计"] == 678.9
    assert latest_balance["meta"]["report_date"] == "2025-12-31"
    assert balance_history[0]["data"]["货币资金"] == 123.45
    assert latest_cashflow["data"]["经营活动产生的现金流量净额"] == 321.0
    assert cashflow_history[0]["data"]["投资活动产生的现金流量净额"] == -45.6
