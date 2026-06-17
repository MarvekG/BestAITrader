from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta

import pytest

from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.tasks.task_functions import cleanup_stock_realtime_market_history, sync_stock_data_func, _process_single_stock


@pytest.mark.asyncio
async def test_process_single_stock_syncs_kline_and_indicators():
    calls: list[str] = []

    async def _mark(name: str):
        calls.append(name)
        return True

    async def _stock_basic(*args, **kwargs):
        return await _mark("stock_basic")

    async def _kline_data(*args, **kwargs):
        return await _mark("kline_data")

    async def _valuation(*args, **kwargs):
        return await _mark("valuation")

    async def _top_holders(*args, **kwargs):
        return await _mark("top_holders")

    async def _fund_holding(*args, **kwargs):
        return await _mark("fund_holding")

    async def _realtime_market(*args, **kwargs):
        return await _mark("realtime_market")

    async def _stock_indicators(*args, **kwargs):
        return await _mark("stock_indicators")

    mock_ingestor = SimpleNamespace(
        fetch_and_ingest_stock_info=AsyncMock(side_effect=_stock_basic),
        fetch_and_ingest_stock_kline=AsyncMock(side_effect=_kline_data),
        fetch_and_ingest_stock_valuation=AsyncMock(side_effect=_valuation),
        fetch_and_ingest_stock_top_holders=AsyncMock(side_effect=_top_holders),
        fetch_and_ingest_stock_fund_holding=AsyncMock(side_effect=_fund_holding),
        fetch_and_ingest_realtime_market=AsyncMock(side_effect=_realtime_market),
    )

    with patch(
        "app.tasks.task_functions.get_sync_date_range",
        return_value=("2025-03-30", "2026-03-30"),
    ), patch("app.data.ingestors.manager.ingestor_manager", mock_ingestor), patch(
        "app.tasks.task_functions.calculate_indicators_func",
        new=AsyncMock(side_effect=_stock_indicators),
    ):
        result = await _process_single_stock("600519.SH", "task-1")

    assert result["status"] == "success"
    assert result["details"]["kline_data"] is True
    assert result["details"]["stock_indicators"] is True
    assert calls[-1] == "stock_indicators"
    assert "kline_data" in calls
    mock_ingestor.fetch_and_ingest_stock_kline.assert_awaited_once_with(
        "600519.SH",
        start_date="2025-03-30",
        end_date="2026-03-30",
        adjust="",
    )


@pytest.mark.asyncio
async def test_sync_stock_data_func_does_not_sync_concept_boards():
    calls: list[str] = []

    async def _mark(name: str):
        calls.append(name)
        return True

    def _mark_side_effect(name: str):
        async def _side_effect(*args, **kwargs):
            return await _mark(name)

        return _side_effect

    mock_ingestor = SimpleNamespace(
        fetch_and_ingest_stock_info=AsyncMock(side_effect=_mark_side_effect("stock_info")),
        fetch_and_ingest_stock_kline=AsyncMock(side_effect=_mark_side_effect("stock_kline")),
        fetch_and_ingest_realtime_market=AsyncMock(side_effect=_mark_side_effect("realtime_market")),
        fetch_and_ingest_stock_valuation=AsyncMock(side_effect=_mark_side_effect("stock_valuation")),
        fetch_and_ingest_board_industry=AsyncMock(side_effect=_mark_side_effect("board_industry")),
        fetch_and_ingest_board_concept=AsyncMock(side_effect=AssertionError("concept board sync should be removed")),
        fetch_and_ingest_northbound=AsyncMock(side_effect=_mark_side_effect("northbound")),
        fetch_and_ingest_dragon_tiger=AsyncMock(side_effect=_mark_side_effect("dragon_tiger")),
        fetch_and_ingest_stock_interactive_qa=AsyncMock(side_effect=_mark_side_effect("stock_interactive_qa")),
        fetch_and_ingest_stock_limit_up_pool=AsyncMock(side_effect=_mark_side_effect("stock_limit_up_pool")),
        fetch_and_ingest_stock_money_flow=AsyncMock(side_effect=_mark_side_effect("stock_money_flow")),
        fetch_and_ingest_stock_shareholder_count=AsyncMock(side_effect=_mark_side_effect("stock_shareholder_count")),
        fetch_and_ingest_stock_pledge_risk=AsyncMock(side_effect=_mark_side_effect("stock_pledge_risk")),
        fetch_and_ingest_stock_insider_trading=AsyncMock(side_effect=_mark_side_effect("stock_insider_trading")),
        fetch_and_ingest_stock_lockup_release=AsyncMock(side_effect=_mark_side_effect("stock_lockup_release")),
        fetch_and_ingest_stock_margin_data=AsyncMock(side_effect=_mark_side_effect("stock_margin_data")),
        fetch_and_ingest_stock_block_trade=AsyncMock(side_effect=_mark_side_effect("stock_block_trade")),
        fetch_and_ingest_sector_money_flow=AsyncMock(side_effect=_mark_side_effect("sector_money_flow")),
        fetch_and_ingest_stock_top_holders=AsyncMock(side_effect=_mark_side_effect("stock_top_holders")),
        fetch_and_ingest_stock_fund_holding=AsyncMock(side_effect=_mark_side_effect("stock_fund_holding")),
    )

    class DummySessionLocal:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    with patch("app.data.ingestors.manager.ingestor_manager", mock_ingestor), patch(
        "app.core.database.SessionLocal", return_value=DummySessionLocal()
    ), patch(
        "app.tasks.task_functions.calculate_indicators_func",
        new=AsyncMock(side_effect=_mark_side_effect("technical_indicators")),
    ):
        result = await sync_stock_data_func("600519.SH")

    assert result["status"] == "success"
    assert "board_industry" in calls
    assert "sector_money_flow" in calls
    assert "technical_indicators" in calls
    mock_ingestor.fetch_and_ingest_board_concept.assert_not_awaited()


@pytest.mark.asyncio
async def test_cleanup_stock_realtime_market_history_keeps_recent_24h_records(test_db) -> None:
    latest_market_time = datetime.now() - timedelta(days=3)
    db = test_db()
    try:
        db.add(StockBasic(stock_code="600519.SH", name="贵州茅台"))
        db.add_all(
            [
                StockRealtimeMarket(
                    stock_code="600519.SH",
                    current_price=1,
                    timestamp=latest_market_time,
                ),
                StockRealtimeMarket(
                    stock_code="600519.SH",
                    current_price=2,
                    timestamp=latest_market_time - timedelta(hours=23, minutes=59),
                ),
                StockRealtimeMarket(
                    stock_code="600519.SH",
                    current_price=3,
                    timestamp=latest_market_time - timedelta(days=1, minutes=1),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    with patch("app.tasks.task_functions.SessionLocal", test_db):
        result = await cleanup_stock_realtime_market_history()

    db = test_db()
    try:
        rows = db.query(StockRealtimeMarket).order_by(StockRealtimeMarket.current_price.asc()).all()
        assert result["status"] == "success"
        assert result["deleted_count"] == 1
        assert [row.current_price for row in rows] == [1, 2]
        assert result["retention_hours"] == 24
    finally:
        db.close()


@pytest.mark.asyncio
async def test_cleanup_stock_realtime_market_history_deletes_records_older_than_24h(test_db) -> None:
    latest_market_time = datetime.now() - timedelta(days=1)
    db = test_db()
    try:
        db.add(StockBasic(stock_code="600519.SH", name="贵州茅台"))
        db.add_all(
            [
                StockRealtimeMarket(
                    stock_code="600519.SH",
                    current_price=1,
                    timestamp=latest_market_time,
                ),
                StockRealtimeMarket(
                    stock_code="600519.SH",
                    current_price=2,
                    timestamp=latest_market_time - timedelta(days=1, minutes=1),
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    with patch("app.tasks.task_functions.SessionLocal", test_db):
        result = await cleanup_stock_realtime_market_history(
            task_name="[Auto] Stock Market Intraday Cache Cleanup 1h"
        )

    assert result["status"] == "success"
    assert result["deleted_count"] == 1
