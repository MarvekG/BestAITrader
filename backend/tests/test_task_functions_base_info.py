from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.data_storage import StockBasic, StockRealtimeMarket
from app.tasks.task_functions import (
    cleanup_stock_realtime_market_history,
    sync_base_info_func,
    sync_bulk_tables_func,
    sync_stock_data_func,
    _process_single_stock,
)


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
        adjust="qfq",
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

    def _date_range(task_type: str = "normal"):
        if task_type == "kline_base_info":
            return "2025-07-01", "2026-07-01"
        if task_type == "margin":
            return "2026-06-15", "2026-07-01"
        return "2026-06-28", "2026-07-01"

    with patch("app.tasks.task_functions.get_sync_date_range", side_effect=_date_range), patch(
        "app.data.ingestors.manager.ingestor_manager", mock_ingestor
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
    mock_ingestor.fetch_and_ingest_stock_kline.assert_awaited_once_with(
        "600519.SH",
        start_date="2025-07-01",
        end_date="2026-07-01",
        adjust="qfq",
    )


@pytest.mark.asyncio
async def test_sync_stock_data_func_stores_only_step_statuses():
    def _mark_side_effect(name: str):
        async def _side_effect(*args, **kwargs):
            return {"success": True, "data": [{"source": name, "payload": "large interface data"}], "count": 1}

        return _side_effect

    mock_ingestor = SimpleNamespace(
        fetch_and_ingest_stock_info=AsyncMock(side_effect=_mark_side_effect("stock_info")),
        fetch_and_ingest_stock_kline=AsyncMock(side_effect=_mark_side_effect("stock_kline")),
        fetch_and_ingest_realtime_market=AsyncMock(side_effect=_mark_side_effect("realtime_market")),
        fetch_and_ingest_stock_valuation=AsyncMock(side_effect=_mark_side_effect("stock_valuation")),
        fetch_and_ingest_board_industry=AsyncMock(side_effect=_mark_side_effect("board_industry")),
        fetch_and_ingest_northbound=AsyncMock(side_effect=_mark_side_effect("northbound")),
        fetch_and_ingest_dragon_tiger=AsyncMock(side_effect=_mark_side_effect("dragon_tiger")),
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
    )

    with patch("app.data.ingestors.manager.ingestor_manager", mock_ingestor), patch(
        "app.tasks.task_functions.calculate_indicators_func",
        new=AsyncMock(return_value={"success": True, "data": [{"payload": "indicator data"}], "count": 1}),
    ):
        result = await sync_stock_data_func("600519.SH")

    assert result["status"] == "success"
    assert set(result["details"].values()) == {True}
    assert "large interface data" not in str(result)
    assert "indicator data" not in str(result)


@pytest.mark.asyncio
async def test_sync_bulk_tables_kline_uses_qfq_adjustment():
    mock_ingestor = SimpleNamespace(
        _get_all_stock_codes=AsyncMock(return_value=["600519.SH"]),
        _get_all_stock_codes_from_stock_basic=AsyncMock(return_value=["600519.SH"]),
        fetch_and_ingest_stock_kline=AsyncMock(return_value=True),
        fetch_and_ingest_all_stock_basic=AsyncMock(),
        fetch_and_ingest_index_daily=AsyncMock(),
        fetch_and_ingest_stock_valuation=AsyncMock(),
        fetch_and_ingest_realtime_market=AsyncMock(),
        fetch_and_ingest_board_industry=AsyncMock(),
        fetch_and_ingest_northbound=AsyncMock(),
        fetch_and_ingest_dragon_tiger=AsyncMock(),
        fetch_and_ingest_stock_money_flow=AsyncMock(),
        fetch_and_ingest_sector_money_flow=AsyncMock(),
        fetch_and_ingest_stock_block_trade=AsyncMock(),
        fetch_and_ingest_stock_margin_data=AsyncMock(),
        fetch_and_ingest_stock_limit_up_pool=AsyncMock(),
        fetch_and_ingest_stock_limit_down_pool=AsyncMock(),
        fetch_and_ingest_stock_zhaban_pool=AsyncMock(),
        fetch_and_ingest_stock_shareholder_count=AsyncMock(),
        fetch_and_ingest_stock_pledge_risk=AsyncMock(),
        fetch_and_ingest_all_pledge_summary=AsyncMock(),
        fetch_and_ingest_stock_insider_trading=AsyncMock(),
        fetch_and_ingest_stock_lockup_release=AsyncMock(),
        fetch_and_ingest_stock_top_holders=AsyncMock(),
    )
    with patch("app.data.ingestors.manager.ingestor_manager", mock_ingestor):
        result = await sync_bulk_tables_func(tables=["kline"], start_date="2025-07-01", end_date="2026-07-01")

    assert result["status"] == "success"
    mock_ingestor.fetch_and_ingest_stock_kline.assert_awaited_once_with(
        stock_code="600519.SH",
        start_date="2025-07-01",
        end_date="2026-07-01",
        adjust="qfq",
    )


@pytest.mark.asyncio
async def test_cleanup_stock_realtime_market_history_keeps_recent_24h_records(test_db) -> None:
    latest_market_time = datetime.now() - timedelta(days=3)
    async with test_db() as db:
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
        await db.commit()

    result = await cleanup_stock_realtime_market_history()

    async with test_db() as db:
        rows = (await db.execute(
            select(StockRealtimeMarket).order_by(StockRealtimeMarket.current_price.asc())
        )).scalars().all()
        assert result["status"] == "success"
        assert result["deleted_count"] == 1
        assert [row.current_price for row in rows] == [1, 2]
        assert result["retention_hours"] == 24


@pytest.mark.asyncio
async def test_cleanup_stock_realtime_market_history_deletes_records_older_than_24h(test_db) -> None:
    latest_market_time = datetime.now() - timedelta(days=1)
    async with test_db() as db:
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
        await db.commit()

    result = await cleanup_stock_realtime_market_history(
        task_name="[Auto] Stock Market Intraday Cache Cleanup 1h"
    )

    assert result["status"] == "success"
    assert result["deleted_count"] == 1


@pytest.mark.asyncio
async def test_sync_base_info_batch_does_not_hold_session_during_work(monkeypatch) -> None:
    """批量基础信息同步只应在加载股票列表时持有数据库会话。"""
    active_sessions = 0
    max_sessions = 0

    class _Scalars:
        def all(self):
            return ["600519.SH"]

    class _Result:
        def scalars(self):
            return _Scalars()

    class _FakeSession:
        async def __aenter__(self):
            nonlocal active_sessions, max_sessions
            active_sessions += 1
            max_sessions = max(max_sessions, active_sessions)
            return self

        async def __aexit__(self, *_args):
            nonlocal active_sessions
            active_sessions -= 1

        async def execute(self, _statement):
            return _Result()

    class _FakeSessionFactory:
        def __call__(self):
            return _FakeSession()

    async def _fetch_and_ingest_all_stock_basic():
        assert active_sessions == 0
        return True

    async def _update_task_status(**_kwargs):
        assert active_sessions == 0

    async def _process_stock(_stock_code, _task_id):
        assert active_sessions == 0
        return {"status": "success"}

    mock_ingestor = SimpleNamespace(fetch_and_ingest_all_stock_basic=AsyncMock(side_effect=_fetch_and_ingest_all_stock_basic))

    monkeypatch.setattr("app.tasks.task_functions.database_module.AsyncSessionLocal", _FakeSessionFactory())
    monkeypatch.setattr("app.tasks.task_functions.task_manager.update_task_status", _update_task_status)
    monkeypatch.setattr("app.tasks.task_functions._process_single_stock", _process_stock)

    with patch("app.data.ingestors.manager.ingestor_manager", mock_ingestor):
        result = await sync_base_info_func(task_id="base-info-task", scope="warehouse")

    assert result["status"] == "success"
    assert active_sessions == 0
    assert max_sessions == 1
