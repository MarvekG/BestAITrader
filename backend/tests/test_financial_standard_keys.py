import os
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
    assert format_payload_values("common_units", {"ten_thousand_cny": 4455.6})["ten_thousand_cny"] == "4455.6万元"
    assert format_payload_values("common_units", {"hundred_million_cny": 12.3})["hundred_million_cny"] == "12.3亿元"

    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "en")
    assert format_payload_values("common_units", {"cny": 8.5})["cny"] == "8.5 CNY"
    assert (
        format_payload_values("common_units", {"ten_thousand_cny": 4455.6})["ten_thousand_cny"]
        == "44.56 million CNY"
    )
    assert (
        format_payload_values("common_units", {"hundred_million_cny": 12.3})["hundred_million_cny"]
        == "1.23 billion CNY"
    )


def test_format_payload_values_uses_table_unit_config(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    financial = format_payload_values(
        "data.financial_indicator",
        {
            "net_profit_dedt_yoy": -0.27,
            "gross_margin": 11779497981,
            "grossprofit_margin": 27.4158,
            "eps": 1.09,
            "asset_turnover": 0.1115,
        },
    )
    position = format_payload_values("portfolio.position", {"current_position": 0.19})
    unknown = format_payload_values("unknown.table", {"unknown_field": 8.5})

    assert financial["net_profit_dedt_yoy"] == "-0.27%"
    assert financial["gross_margin"] == "117.79亿元"
    assert financial["grossprofit_margin"] == "27.42%"
    assert financial["eps"] == "1.09元"
    assert financial["asset_turnover"] == "0.11次"
    assert position["current_position"] == "19%"
    assert unknown["unknown_field"] == 8.5


def test_format_payload_values_uses_default_ref_for_financial_statements(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    income = format_payload_values(
        "data.stock_income_statement",
        {"total_revenue": 12_300_000_000, "basic_eps": 1.23456, "report_date": "2025-12-31"},
    )
    balance = format_payload_values(
        "data.stock_balance_sheet",
        {"total_assets": 67_890_000_000, "total_share": 100_000_000},
    )
    cashflow = format_payload_values(
        "data.stock_cashflow_statement",
        {"n_cashflow_act": 3_210_000_000},
    )

    assert income["total_revenue"] == "123亿元"
    assert income["basic_eps"] == "1.2346元"
    assert income["report_date"] == "2025-12-31"
    assert balance["total_assets"] == "678.9亿元"
    assert balance["total_share"] == "1亿元"
    assert cashflow["n_cashflow_act"] == "32.1亿元"


def test_format_payload_values_recursively_uses_table_unit_config(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    payload = {
        "data": {
            "gross_margin": 11779497981,
            "grossprofit_margin": 27.4158,
            "q_gsprofit_margin": 27.4158,
            "gross_profit": 11779497981,
            "nested": {"net_profit_dedt_yoy": -0.27},
            "items": [{"roe": 4.0826}],
        },
        "meta": {"report_date": "2026-03-31"},
    }

    result = format_payload_values("data.financial_indicator", payload)

    assert result["data"]["gross_margin"] == "117.79亿元"
    assert result["data"]["grossprofit_margin"] == "27.42%"
    assert result["data"]["q_gsprofit_margin"] == "27.42%"
    assert result["data"]["gross_profit"] == 11779497981
    assert result["data"]["nested"]["net_profit_dedt_yoy"] == "-0.27%"
    assert result["data"]["items"][0]["roe"] == "4.08%"
    assert result["meta"]["report_date"] == "2026-03-31"


def test_format_payload_values_uses_language_specific_unit_and_scale(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    payload = {"ten_thousand_cny": 10000, "hundred_million_cny": 10, "roe": 4.0826}

    zh_common = format_payload_values("common_units", payload)
    en_common = format_payload_values("common_units", payload, language="en")
    en_financial = format_payload_values("data.financial_indicator", payload, language="en")

    assert zh_common["ten_thousand_cny"] == "10000万元"
    assert zh_common["hundred_million_cny"] == "10亿元"
    assert en_common["ten_thousand_cny"] == "100 million CNY"
    assert en_common["hundred_million_cny"] == "1 billion CNY"
    assert en_financial["roe"] == "4.08%"


def test_format_payload_values_covers_ai_context_unit_audit_fields(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    valuation = format_payload_values(
        "fundamental.valuation",
        {"total_mv": 1_602_492_105_138, "float_mv": 215_430_064_799},
    )
    northbound = format_payload_values(
        "capital_flow.northbound",
        {"net_buy_amount": 5_342_126_250, "hold_ratio": 0.0469},
    )
    northbound_snapshot = format_payload_values(
        "fundamental.northbound_flow",
        {"net_buy_amount_10k_cny": 534_212.625, "hold_ratio_pct": 0.0469},
    )
    dragon_tiger_activity = format_payload_values(
        "fundamental.dragon_tiger_activity",
        {"cumulative_net_buy_10k_cny": 5400, "price_change_percent": 9.8},
    )
    financial = format_payload_values(
        "data.financial_indicator",
        {
            "operating_income": 44_029_000_000,
            "eps": 1.09,
            "roe": 4.0826,
            "asset_turnover": 0.1115,
            "gross_margin": 7_954_015_105,
            "grossprofit_margin": 15.5957,
        },
    )

    assert valuation["total_mv"] == "16024.92亿元"
    assert valuation["float_mv"] == "2154.3亿元"
    assert northbound["net_buy_amount"] == "53.42亿元"
    assert northbound["hold_ratio"] == "4.69%"
    assert northbound_snapshot["net_buy_amount_10k_cny"] == "534212.62万元"
    assert northbound_snapshot["hold_ratio_pct"] == "4.69%"
    assert dragon_tiger_activity["cumulative_net_buy_10k_cny"] == "5400万元"
    assert dragon_tiger_activity["price_change_percent"] == "9.8%"
    assert financial["operating_income"] == "440.29亿元"
    assert financial["eps"] == "1.09元"
    assert financial["roe"] == "4.08%"
    assert financial["asset_turnover"] == "0.11次"
    assert financial["gross_margin"] == "79.54亿元"
    assert financial["grossprofit_margin"] == "15.6%"


def test_format_payload_values_formats_realtime_market_units(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    result = format_payload_values(
        "technical.realtime_market",
        {
            "price": 1262.98,
            "pct_chg": -0.7762,
            "turnover_rate": 1.23,
            "volume_ratio": 0.88,
            "pb": 5.89,
            "pe": 19.08,
            "amount": 3_898_027_116,
            "volume": 3_082_836,
            "turnover": 3_898_027_116,
            "total_market_cap": 1_578_828_060_431,
            "circulating_market_cap": 1_578_828_060_431,
        },
    )

    assert result["price"] == "1262.98元"
    assert result["pct_chg"] == "-0.78%"
    assert result["turnover_rate"] == "1.23%"
    assert result["volume_ratio"] == "0.88倍"
    assert result["pb"] == "5.89倍"
    assert result["amount"] == "38.98亿元"
    assert result["volume"] == "3082836股"
    assert result["turnover"] == "38.98亿元"
    assert result["total_market_cap"] == "15788.28亿元"


def test_format_payload_values_formats_stock_picker_units(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")

    quant_inputs = format_payload_values(
        "stock_picker.quant_inputs",
        {
            "pe": 12.5,
            "pb": 1.8,
            "dividend_yield": 2.3,
            "volume": 2032355.46,
            "turnover_amount": 1200000000,
            "atr_pct": 3.2,
        },
    )
    quant_support = format_payload_values(
        "stock_picker.quant_support",
        {
            "trend_quality_score": 26.75,
            "final_quant_score": 66.0,
        },
    )

    assert quant_inputs["pe"] == "12.5倍"
    assert quant_inputs["pb"] == "1.8倍"
    assert quant_inputs["dividend_yield"] == "2.3%"
    assert quant_inputs["volume"] == "2032355手"
    assert quant_inputs["turnover_amount"] == "12亿元"
    assert quant_inputs["atr_pct"] == "3.2%"
    assert quant_support["trend_quality_score"] == "26.75点"
    assert quant_support["final_quant_score"] == "66点"


def test_table_field_units_use_language_specific_schema():
    config_path = Path(__file__).resolve().parents[1] / "app" / "data" / "metadata" / "table_field_units.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    total_assets = config["portfolio.overview"]["total_assets"]
    ten_thousand_cny = config["common_units"]["ten_thousand_cny"]
    hundred_million_cny = config["common_units"]["hundred_million_cny"]
    unit_values = []

    def collect_units(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "unit":
                    unit_values.append(item)
                else:
                    collect_units(item)
        elif isinstance(value, list):
            for item in value:
                collect_units(item)

    collect_units(config)

    assert "display_scale" not in json.dumps(config, ensure_ascii=False)
    assert "units.million_cny" not in unit_values
    assert "units.wanyuan" not in unit_values
    assert total_assets == {
        "zh": {"unit": "units.cny", "scale": 1},
        "en": {"unit": "units.cny", "scale": 1},
        "precision": 2,
    }
    assert ten_thousand_cny == {
        "zh": {"unit": ["units.ten_thousand", "units.cny"], "scale": 1},
        "en": {"unit": ["units.million", "units.cny"], "scale": "0.01"},
        "precision": 2,
    }
    assert hundred_million_cny == {
        "zh": {"unit": ["units.yi", "units.cny"], "scale": 1},
        "en": {"unit": ["units.billion", "units.cny"], "scale": "0.1"},
        "precision": 2,
    }


def test_format_payload_values_does_not_fallback_to_other_language(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "fr")

    result = format_payload_values("common_units", {"cny": 8.5})

    assert result["cny"] == 8.5


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
        hold_ratio=0.0185,
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
        hold_ratio=0.0162,
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
        hold_ratio=0.0135,
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
    assert result["quarter_change"]["net_buy_amount_10k_cny"] == "6800万元"
    assert result["signal"]["flow_label"] == "accumulating"
    assert result["signal"]["foreign_sentiment_label"] == "positive"
    assert result["recent_records"][0]["date"] == str(latest.date)
    assert result["recent_records"][2]["hold_ratio_pct"] == "1.35%"


def test_capital_flow_northbound_formats_decimal_holding_ratio_as_percent():
    latest = NorthboundData(
        stock_code="600519.SH",
        date=date.today() - timedelta(days=20),
        hold_shares=12_500_000.0,
        hold_ratio=0.0438,
        net_buy_amount=68_000_000.0,
        net_buy_volume=350_000.0,
    )
    previous = NorthboundData(
        stock_code="600519.SH",
        date=date.today() - timedelta(days=120),
        hold_shares=12_000_000.0,
        hold_ratio=0.0428,
        net_buy_amount=8_000_000.0,
        net_buy_volume=50_000.0,
    )

    source = CapitalFlowSource()
    session = ModelMapSession({NorthboundData: [latest, previous]})

    latest_result = source._get_northbound(session, "600519.SH")
    trend_result = source._get_northbound_trend(session, "600519.SH")

    assert latest_result["hold_ratio"] == "4.38%"
    assert latest_result["net_buy_amount"] == "0.68亿元"
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
    assert result["overview"]["cumulative_net_buy_10k_cny"] == "5400万元"
    assert result["signal"]["activity_label"] == "sporadic"
    assert result["signal"]["market_sentiment_label"] == "market_buying_bias"
    assert result["all_records"][0]["stock_code"] == "600519.SH"
    assert result["all_records"][0]["net_buy_amount_10k_cny"] == "4200万元"
    assert result["all_records"][0]["buy_amount_10k_cny"] == "12000万元"
    assert result["all_records"][0]["sell_amount_10k_cny"] == "7800万元"
    assert result["all_records"][0]["price_change_percent"] == "9.8%"
    assert result["all_records"][0]["net_buy_ratio_pct"] == "4.6%"
    assert result["all_records"][0]["post_1_day_price_change_percent"] == "1.2%"
    assert result["all_records"][0]["post_5_day_price_change_percent"] == "3.4%"
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

    assert result["total_amount"] == "4455.6万元"
    assert result["avg_premium"] == "-3.25%"
    assert result["recent_trades"][0]["price"] == "37.13元"
    assert result["recent_trades"][0]["volume"] == "120万股"
    assert result["recent_trades"][0]["amount"] == "4455.6万元"
    assert result["recent_trades"][0]["premium_rate"] == "-3.25%"
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


def test_financial_context_localizes_raw_data_keys_by_system_language(monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM_LANGUAGE", "zh")

    localized = FinancialReader().localize_raw_data(
        {
            "eps": 1.74,
            "roe": 11.5953,
            "gross_margin": 7_954_015_105,
            "grossprofit_margin": 21.9753,
            "capital_reserve_ps": 1.7286,
        },
        "data.financial_indicator",
    )

    assert localized["每股收益"] == 1.74
    assert localized["净资产收益率"] == 11.5953
    assert localized["毛利"] == 7_954_015_105
    assert localized["销售毛利率"] == 21.9753
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


def test_financial_context_localizes_tushare_optional_indicator_fields(monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM_LANGUAGE", "zh")

    raw_payload = {
        "data": {
            "q_eps": 0.38,
            "inv_turn": 0.72,
            "q_gr_qoq": -6.64,
            "ocf_to_or": 0.953,
            "op_to_ebt": 102.5,
            "q_dtprofit": 123456789.0,
            "q_opincome": 234567890.0,
            "roic_yearly": 8.91,
            "update_flag": "1",
        },
        "meta": {"report_date": "2025-09-30"},
    }

    formatted = format_payload_values("data.financial_indicator", raw_payload)
    localized = FinancialReader().localize_raw_data(formatted, "data.financial_indicator")

    assert localized["data"]["单季度每股收益"] == "0.38元"
    assert localized["data"]["存货周转率"] == "0.72次"
    assert localized["data"]["单季度营业总收入环比增长率"] == "-6.64%"
    assert localized["data"]["经营活动现金流净额/营业收入"] == "0.95倍"
    assert localized["data"]["营业利润/利润总额"] == "102.5%"
    assert localized["data"]["单季度扣非净利润"] == "1.23亿元"
    assert localized["data"]["单季度经营活动净收益"] == "2.35亿元"
    assert localized["data"]["年化投入资本回报率"] == "8.91%"
    assert localized["data"]["更新标识"] == "1"
