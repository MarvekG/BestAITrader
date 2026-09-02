from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.data.analytics.indicators import IndicatorService
from app.models.data_storage import KlineData, StockBasic
from app.models.stock_indicators import StockIndicators


@pytest.mark.asyncio
async def test_process_stock_uses_daily_kline_only(async_db_session, monkeypatch):
    stock_code = "000001.SZ"
    async_db_session.add(
        StockBasic(
            stock_code=stock_code,
            name="平安银行",
            industry="Bank",
            data_source="test",
        )
    )
    await async_db_session.flush()
    start = date(2026, 1, 1)
    daily_closes = [10, 11, 12, 13, 14]
    weekly_closes = [100, 101, 102, 103, 104]
    for index, close in enumerate(daily_closes):
        trade_date = start + timedelta(days=index)
        async_db_session.add(
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
        async_db_session.add(
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
    await async_db_session.commit()

    saved = {}

    async def fake_save_indicators(saved_stock_code, df):
        saved["stock_code"] = saved_stock_code
        saved["df"] = df.copy()

    monkeypatch.setattr(IndicatorService, "save_indicators", staticmethod(fake_save_indicators))

    await IndicatorService.process_stock(stock_code)

    assert saved["stock_code"] == stock_code
    assert saved["df"]["close"].tolist() == daily_closes
    assert saved["df"].iloc[-1]["ma5"] == sum(daily_closes) / len(daily_closes)


@pytest.mark.asyncio
async def test_save_indicators_chunks_large_batches_and_upserts(test_db, async_db_session):
    """超过单语句参数上限的大批量保存要分块写入，并保持 upsert 幂等。"""
    stock_code = "600887.SH"
    async_db_session.add(
        StockBasic(
            stock_code=stock_code,
            name="伊利股份",
            industry="Food",
            data_source="test",
        )
    )
    await async_db_session.commit()

    row_count = 1100  # 超过 500 的块大小，强制走多次分块
    base = date(2022, 1, 3)
    df = pd.DataFrame({
        "date": [base + timedelta(days=i) for i in range(row_count)],
        "ma5": [10.0 + i for i in range(row_count)],
        "close": [10.0 + i for i in range(row_count)],
    })

    await IndicatorService.save_indicators(stock_code, df)

    count = await async_db_session.scalar(
        select(func.count()).select_from(StockIndicators).where(StockIndicators.stock_code == stock_code)
    )
    assert count == row_count

    # 相同 trade_date 再次保存应走冲突更新路径，行数不变、值被刷新
    df["ma5"] = 99.0
    await IndicatorService.save_indicators(stock_code, df)

    count_after = await async_db_session.scalar(
        select(func.count()).select_from(StockIndicators).where(StockIndicators.stock_code == stock_code)
    )
    assert count_after == row_count
    sample = await async_db_session.scalar(
        select(StockIndicators.ma5).where(
            StockIndicators.stock_code == stock_code,
            StockIndicators.trade_date == base,
        )
    )
    assert sample == 99.0
