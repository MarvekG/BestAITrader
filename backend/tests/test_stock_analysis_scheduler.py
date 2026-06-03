from datetime import datetime

from app.models.stock_warehouse import StockWarehouse
from app.tasks.stock_analysis_scheduler import is_due_for_auto_analysis


def _warehouse_stock(**overrides):
    values = {
        "stock_code": "600519.SH",
        "user_id": 1,
        "is_active": True,
        "auto_analysis_enabled": True,
        "auto_analysis_frequency": "daily",
        "auto_analysis_time": "09:35",
        "last_auto_analysis_at": None,
    }
    values.update(overrides)
    return StockWarehouse(**values)


def test_auto_analysis_waits_until_configured_time():
    stock = _warehouse_stock(auto_analysis_time="09:40")

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 39)) is False
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 40)) is True


def test_auto_analysis_does_not_catch_up_after_configured_time():
    stock = _warehouse_stock(auto_analysis_time="09:40")

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 45)) is False


def test_auto_analysis_uses_short_trigger_window_after_configured_time():
    stock = _warehouse_stock(auto_analysis_time="09:40")

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 44)) is True


def test_auto_analysis_daily_frequency_runs_once_per_day():
    stock = _warehouse_stock(last_auto_analysis_at=datetime(2026, 5, 11, 9, 45))

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 10, 0)) is False
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 12, 9, 35)) is True


def test_auto_analysis_run_immediately_skips_time_gate():
    stock = _warehouse_stock(
        auto_analysis_time="09:40",
        auto_analysis_run_immediately=True,
    )

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 39)) is True


def test_auto_analysis_run_immediately_false_requires_trigger_window():
    stock = _warehouse_stock(
        auto_analysis_time="09:40",
        auto_analysis_run_immediately=False,
    )

    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 39)) is False
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 40)) is True
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 44)) is True
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 9, 45)) is False


def test_auto_analysis_run_immediately_runs_even_if_already_run():
    stock = _warehouse_stock(
        auto_analysis_time="09:40",
        auto_analysis_run_immediately=True,
        last_auto_analysis_at=datetime(2026, 5, 11, 9, 45),
    )

    # Even though it already ran today at 09:45, run-immediately should trigger again
    assert is_due_for_auto_analysis(stock, datetime(2026, 5, 11, 10, 0)) is True


def test_auto_analysis_weekly_and_monthly_frequency():
    weekly = _warehouse_stock(
        auto_analysis_frequency="weekly",
        last_auto_analysis_at=datetime(2026, 5, 11, 9, 45),
    )
    monthly = _warehouse_stock(
        auto_analysis_frequency="monthly",
        last_auto_analysis_at=datetime(2026, 5, 11, 9, 45),
    )

    assert is_due_for_auto_analysis(weekly, datetime(2026, 5, 15, 10, 0)) is False
    assert is_due_for_auto_analysis(weekly, datetime(2026, 5, 18, 9, 35)) is True
    assert is_due_for_auto_analysis(monthly, datetime(2026, 5, 18, 10, 0)) is False
    assert is_due_for_auto_analysis(monthly, datetime(2026, 6, 1, 9, 35)) is True
