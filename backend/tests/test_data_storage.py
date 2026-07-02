from datetime import datetime

import pytest

from app.data.storage import DataStorageService
from app.models.data_storage import StockBasic, StockRealtimeMarket


@pytest.mark.asyncio
async def test_get_stock_realtime_market_returns_latest_quote(async_db_session):
    async_db_session.add(
        StockBasic(
            stock_code="000651.SZ",
            name="格力电器",
            market="SZ",
        )
    )
    async_db_session.add_all(
        [
            StockRealtimeMarket(
                stock_code="000651.SZ",
                current_price=37.10,
                prev_close=37.15,
                pb_ratio=1.55,
                timestamp=datetime(2026, 6, 18, 9, 50),
            ),
            StockRealtimeMarket(
                stock_code="000651.SZ",
                current_price=37.16,
                prev_close=37.15,
                pb_ratio=1.56,
                timestamp=datetime(2026, 6, 18, 9, 55),
            ),
        ]
    )
    await async_db_session.commit()

    result = await DataStorageService().get_stock_realtime_market("000651.SZ")

    assert result["latest_price"] == 37.16
    assert result["current_price"] == 37.16
    assert result["yesterday_close"] == 37.15
    assert result["pb"] == 1.56
    assert result["update_time"] == "2026-06-18T09:55:00"
