from datetime import date, timedelta

from app.data.analytics.indicators import IndicatorService
from app.models.data_storage import KlineData, StockBasic


def test_process_stock_uses_daily_kline_only(db_session, monkeypatch):
    stock_code = "000001.SZ"
    db_session.add(
        StockBasic(
            stock_code=stock_code,
            name="平安银行",
            industry="Bank",
            data_source="test",
        )
    )
    db_session.flush()
    start = date(2026, 1, 1)
    daily_closes = [10, 11, 12, 13, 14]
    weekly_closes = [100, 101, 102, 103, 104]
    for index, close in enumerate(daily_closes):
        trade_date = start + timedelta(days=index)
        db_session.add(
            KlineData(
                stock_code=stock_code,
                date=trade_date,
                freq="D",
                open=close - 0.5,
                high=close + 1,
                low=close - 1,
                close=close,
                volume=1000 + index,
                data_source="test",
            )
        )
        db_session.add(
            KlineData(
                stock_code=stock_code,
                date=trade_date,
                freq="W",
                open=close - 0.5,
                high=close + 1,
                low=close - 1,
                close=weekly_closes[index],
                volume=2000 + index,
                data_source="test",
            )
        )
    db_session.commit()

    saved = {}

    def fake_save_indicators(db, saved_stock_code, df):
        saved["stock_code"] = saved_stock_code
        saved["df"] = df.copy()

    monkeypatch.setattr(IndicatorService, "save_indicators", staticmethod(fake_save_indicators))

    IndicatorService.process_stock(db_session, stock_code)

    assert saved["stock_code"] == stock_code
    assert saved["df"]["close"].tolist() == daily_closes
    assert saved["df"].iloc[-1]["ma5"] == sum(daily_closes) / len(daily_closes)
