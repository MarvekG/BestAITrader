import pytest

from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
from app.data.metadata.financial_report_localizer import localize_financial_report_payload
from app.data.metadata.field_units import format_payload_values


def test_tushare_balance_sheet_derives_net_cash_before_formatting():
    """资产负债表源头应在展示格式化前计算净现金与每股净现金。"""
    data = {
        "money_cap": 56_289_000_000,
        "st_borr": 0,
        "lt_borr": 0,
        "total_share": 5_601_000_000,
    }

    TushareIngestor._add_balance_sheet_derived_metrics(data)

    assert data["net_cash"] == 56_289_000_000
    assert data["per_share_net_cash"] == pytest.approx(10.05, abs=0.01)


def test_balance_sheet_derived_metrics_format_after_source_calculation(monkeypatch):
    """派生字段计算后再进入展示格式化。"""
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")
    data = {
        "money_cap": 56_289_000_000,
        "st_borr": 0,
        "lt_borr": 0,
        "total_share": 5_601_000_000,
    }

    TushareIngestor._add_balance_sheet_derived_metrics(data)
    formatted = format_payload_values("data.stock_balance_sheet", data)

    assert formatted["net_cash"] == "562.89亿元"
    assert formatted["per_share_net_cash"] == "10.05元/股"


def test_balance_sheet_derived_metrics_have_localized_labels(monkeypatch):
    """派生字段进入财报上下文时应使用本地化标签。"""
    monkeypatch.setattr("app.core.config.settings.SYSTEM_LANGUAGE", "zh")
    data = {
        "money_cap": 56_289_000_000,
        "st_borr": 0,
        "lt_borr": 0,
        "total_share": 5_601_000_000,
    }

    TushareIngestor._add_balance_sheet_derived_metrics(data)
    localized = localize_financial_report_payload(
        {"data": format_payload_values("data.stock_balance_sheet", data)},
        "data.stock_balance_sheet",
    )

    assert localized["data"]["净现金"] == "562.89亿元"
    assert localized["data"]["每股净现金"] == "10.05元/股"
    assert localized["data"]["实收资本/股本金额(非总股数)"] == "56.01亿元"
    assert "net_cash" not in localized["data"]
    assert "per_share_net_cash" not in localized["data"]
    assert "total_share" not in localized["data"]
