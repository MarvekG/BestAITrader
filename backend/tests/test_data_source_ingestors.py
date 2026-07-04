#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试启用的数据源采集功能
Test enabled data sources for data ingestion
"""

import pytest
import pandas as pd
import numpy as np
import tushare as ts
import uuid
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from app.data.ingestion.service import DataIngestionService
from app.data.ingestors.plugins.akshare_ingestor import AkshareIngestor
from app.data.ingestors.manager import ingestor_manager
from app.data.ingestors.plugins.column_mapping import ColumnMapper
from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
from app.core.utils.date_utils import normalize_compact_date
from app.models.data_storage import IndexDaily, NorthboundData, StockLimitUpPool, StockRealtimeMarket, StockTopHolders


@pytest.fixture
def test_stock_code():
    """测试用的股票代码"""
    return "000001.SZ"


@pytest.fixture
def test_date_range():
    """测试用的日期范围"""
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
    return start_date, end_date


@pytest.fixture
def mock_tushare_data(test_stock_code):
    """创建模拟的 tushare K线数据"""
    dates = pd.date_range(start='2026-01-15', end='2026-01-25', freq='D')
    data = {
        'ts_code': [test_stock_code] * len(dates),
        'trade_date': [d.strftime('%Y%m%d') for d in dates],
        'open': [10.0 + i * 0.1 for i in range(len(dates))],
        'close': [10.1 + i * 0.1 for i in range(len(dates))],
        'high': [10.2 + i * 0.1 for i in range(len(dates))],
        'low': [9.9 + i * 0.1 for i in range(len(dates))],
        'vol': [1000000 + i * 10000 for i in range(len(dates))],
        'amount': [10000000 + i * 100000 for i in range(len(dates))],
        'change': [0.1 for i in range(len(dates))],
        'pre_close': [10.0 for i in range(len(dates))],
        'pct_chg': [1.0 + i * 0.1 for i in range(len(dates))],
    }
    return pd.DataFrame(data)


class TestIngestorRegistration:
    """测试采集器注册"""

    def test_required_ingestors_registered(self):
        """测试所有必需的采集器都已注册"""
        registered_ingestors = list(ingestor_manager.ingestors.keys())
        required_ingestors = ['tushare']

        for ingestor_name in required_ingestors:
            assert ingestor_name in registered_ingestors, \
                f"采集器 {ingestor_name} 未注册"

    def test_get_ingestor(self):
        """测试获取采集器"""
        removed_ingestor = ingestor_manager.get_ingestor('removed_source')
        assert removed_ingestor is None, "已移除的数据源不应被注册"

        tushare_ingestor = ingestor_manager.get_ingestor('tushare')
        assert tushare_ingestor is not None, "无法获取 tushare 采集器"


class TestDataSourcePriority:
    """测试数据源优先级"""

    def test_priority_list(self):
        """测试数据源优先级列表"""
        priority_list = ingestor_manager.get_prioritized_sources()
        assert len(priority_list) > 0, "优先级列表为空"
        assert priority_list[0] == ingestor_manager.default_source, \
            "默认数据源应该在优先级列表的第一位"

    def test_default_source_first(self):
        """测试默认数据源在第一位"""
        priority_list = ingestor_manager.get_prioritized_sources()
        default_source = ingestor_manager.default_source
        assert priority_list[0] == default_source, \
            f"默认数据源 {default_source} 应该在第一位，实际: {priority_list[0]}"


@pytest.mark.asyncio
async def test_sync_all_boards_and_pools_excludes_concept_boards(monkeypatch):
    """
    板块与股票池批量同步只保留行业板块，避免继续写入概念板块行情。
    """
    calls: list[str] = []

    def mark_side_effect(name: str):
        async def side_effect(*args, **kwargs):
            calls.append(name)
            return True

        return side_effect

    monkeypatch.setattr(
        ingestor_manager,
        "fetch_and_ingest_board_industry",
        AsyncMock(side_effect=mark_side_effect("board_industry")),
    )
    monkeypatch.setattr(
        ingestor_manager,
        "fetch_and_ingest_board_concept",
        AsyncMock(side_effect=AssertionError("concept board sync should be removed")),
        raising=False,
    )
    monkeypatch.setattr(
        ingestor_manager,
        "fetch_and_ingest_stock_limit_up_pool",
        AsyncMock(side_effect=mark_side_effect("stock_limit_up_pool")),
    )
    monkeypatch.setattr(
        ingestor_manager,
        "fetch_and_ingest_stock_limit_down_pool",
        AsyncMock(side_effect=mark_side_effect("stock_limit_down_pool")),
    )
    monkeypatch.setattr(
        ingestor_manager,
        "fetch_and_ingest_stock_zhaban_pool",
        AsyncMock(side_effect=mark_side_effect("stock_zhaban_pool")),
    )

    result = await ingestor_manager.sync_all_boards_and_pools()

    assert result is True
    assert calls == [
        "board_industry",
        "stock_limit_up_pool",
        "stock_limit_down_pool",
        "stock_zhaban_pool",
    ]
    ingestor_manager.fetch_and_ingest_board_concept.assert_not_awaited()


class TestDateUtils:
    def test_normalize_compact_date_accepts_common_formats(self):
        assert normalize_compact_date('20260318') == '20260318'
        assert normalize_compact_date('2026-03-18') == '20260318'
        assert normalize_compact_date('2026/03/18') == '20260318'


class TestTushareIngestor:
    """测试 Tushare 数据采集器"""

    def test_tushare_ingestor_does_not_expose_concept_board_sync(self):
        """
        Tushare 采集器不再提供概念板块行情同步入口。
        """
        assert not hasattr(TushareIngestor, "fetch_and_ingest_board_concept")

    @pytest.mark.asyncio
    async def test_tushare_kline_uses_pro_bar_with_adjustment(self, mock_tushare_data):
        """Tushare K 线应通过 pro_bar 获取复权行情。"""
        ingestor = TushareIngestor.__new__(TushareIngestor)
        ingestor.source = "tushare"
        ingestor.pro = Mock()
        ingestor.ensure_pro = AsyncMock(return_value=ingestor.pro)
        ingestor.ingestion_service = Mock()
        ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
        ingestor._run_in_executor = AsyncMock(return_value=mock_tushare_data)

        result = await ingestor.fetch_and_ingest_stock_kline(
            "000001.SZ",
            start_date="2026-01-15",
            end_date="2026-01-25",
            adjust="qfq",
        )

        assert result["success"] is True
        called_func = ingestor._run_in_executor.await_args.args[0]
        assert called_func is ts.pro_bar
        assert ingestor._run_in_executor.await_args.kwargs["api"] is ingestor.pro
        assert ingestor._run_in_executor.await_args.kwargs["freq"] == "D"
        assert ingestor._run_in_executor.await_args.kwargs["adj"] == "qfq"


@pytest.mark.asyncio
async def test_tushare_realtime_market_normalizes_numeric_strings():
    """Tushare 实时行情字符串数值应在写入实时行情表前转为数值类型。"""
    ingestor = TushareIngestor.__new__(TushareIngestor)
    ingestor.source = "tushare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    source_df = pd.DataFrame(
        [
            {
                "code": "000001",
                "name": "平安银行",
                "date": "2026-07-02",
                "time": "10:12:23",
                "price": "12.34",
                "pre_close": "12.00",
                "volume": "13236539",
                "amount": "15406199797.180",
                "high": "12.50",
                "low": "12.10",
                "open": "12.20",
                "bid": "12.33",
                "ask": "12.34",
                "b1_v": "100",
                "b1_p": "12.33",
                "b2_v": "200",
                "b2_p": "12.32",
                "b3_v": "300",
                "b3_p": "12.31",
                "b4_v": "400",
                "b4_p": "12.30",
                "b5_v": "500",
                "b5_p": "12.29",
                "a1_v": "100",
                "a1_p": "12.34",
                "a2_v": "200",
                "a2_p": "12.35",
                "a3_v": "300",
                "a3_p": "12.36",
                "a4_v": "400",
                "a4_p": "12.37",
                "a5_v": "500",
                "a5_p": "12.38",
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(return_value=source_df)

    result = await ingestor.fetch_and_ingest_realtime_market("000001.SZ")

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result["success"] is True
    for column in ["current_price", "prev_close", "volume", "turnover", "high", "low", "open"]:
        assert pd.api.types.is_numeric_dtype(written_df[column])
    assert written_df.iloc[0]["volume"] == 13_236_539
    assert isinstance(result["data"][0]["timestamp"], pd.Timestamp)


def test_data_ingestion_normalizes_dedicated_table_bind_values():
    """专用表写入应使用 asyncpg 可绑定的 Python 原生标量类型。"""
    timestamp = pd.Timestamp("2026-07-02T10:20:39.379941")
    df = pd.DataFrame(
        [
            {
                "stock_code": "000001.SZ",
                "current_price": np.float64(12.34),
                "main_net_inflow_rank_today": np.int64(7),
                "change_percent": np.nan,
                "timestamp": timestamp,
            }
        ]
    )

    records = DataIngestionService()._prepare_records(
        df,
        StockRealtimeMarket,
        api_name="tushare_realtime",
        source="tushare",
    )

    assert isinstance(records[0]["id"], uuid.UUID)
    assert records[0]["timestamp"] == timestamp.to_pydatetime()
    assert isinstance(records[0]["current_price"], float)
    assert isinstance(records[0]["main_net_inflow_rank_today"], int)
    assert records[0]["change_percent"] is None


def test_data_ingestion_normalizes_values_by_column_type():
    """专用表应按 SQLAlchemy 列类型转换日期、字符串和数值字段。"""
    service = DataIngestionService()

    index_records = service._prepare_records(
        pd.DataFrame([{"index_code": "000001.SH", "trade_date": "20240105", "close": np.float64(2929.18)}]),
        IndexDaily,
        api_name="index_daily",
        source="tushare",
    )
    limit_records = service._prepare_records(
        pd.DataFrame([{"stock_code": "000001.SZ", "update_date": "2024-01-02", "limit_up_days": np.int64(2)}]),
        StockLimitUpPool,
        api_name="limit_list_d",
        source="tushare",
    )
    holder_records = service._prepare_records(
        pd.DataFrame([{"stock_code": "000001.SZ", "report_date": "20250823", "change": np.float64(0.0)}]),
        StockTopHolders,
        api_name="top10_holders",
        source="tushare",
    )

    assert index_records[0]["trade_date"] == date(2024, 1, 5)
    assert isinstance(index_records[0]["close"], float)
    assert limit_records[0]["update_date"] == date(2024, 1, 2)
    assert limit_records[0]["limit_up_days"] == "2"
    assert holder_records[0]["report_date"] == date(2025, 8, 23)
    assert holder_records[0]["change"] == "0.0"


@pytest.mark.asyncio
async def test_bulk_upsert_splits_large_batches_before_asyncpg_parameter_limit(monkeypatch):
    """批量 upsert 应按参数上限分片，避免 asyncpg 32767 参数限制。"""
    service = DataIngestionService()
    chunk_sizes = []

    async def fake_bulk_upsert_chunk(_model, records):
        chunk_sizes.append(len(records))

    monkeypatch.setattr(service, "_bulk_upsert_chunk", fake_bulk_upsert_chunk)
    records = [
        {
            "id": uuid.uuid4(),
            "stock_code": "000001.SZ",
            "date": date(2024, 1, 1),
            "hold_shares": 1.0,
            "hold_value": 2.0,
            "hold_ratio": 3.0,
            "close_price": 4.0,
            "change_percent": 5.0,
            "net_buy_volume": 6.0,
            "net_buy_amount": 7.0,
            "hold_value_change": 8.0,
            "data_source": "tushare",
        }
        for _ in range(2500)
    ]

    await service._bulk_upsert(NorthboundData, records)

    assert len(chunk_sizes) == 2
    # created_at/updated_at have Python-side defaults and are also bound by SQLAlchemy.
    assert max(chunk_sizes) * 14 <= 30000
    assert sum(chunk_sizes) == len(records)


@pytest.mark.asyncio
async def test_tushare_dragon_tiger_normalizes_amounts_to_cny():
    """Tushare 龙虎榜金额字段官方单位为元，应原样写入元。"""
    ingestor = TushareIngestor.__new__(TushareIngestor)
    ingestor.source = "tushare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    fake_pro = Mock()
    fake_pro.top_list = Mock()
    ingestor.pro = fake_pro
    source_df = pd.DataFrame(
        [
            {
                "trade_date": "20260618",
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "close": 10.5,
                "pct_change": 10.0,
                "turnover_rate": 3.0,
                "amount": 50000,
                "l_sell": 1200,
                "l_buy": 1800,
                "l_amount": 3000,
                "net_amount": 600,
                "reason": "日涨幅偏离值达7%",
                "net_rate": 20.0,
                "amount_rate": 6.0,
                "float_values": 123456,
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(return_value=source_df)

    result = await ingestor.fetch_and_ingest_dragon_tiger("20260618")

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result is True
    assert written_df.iloc[0]["net_buy_amount"] == 600
    assert written_df.iloc[0]["buy_amount"] == 1800
    assert written_df.iloc[0]["sell_amount"] == 1200
    assert written_df.iloc[0]["total_trade_amount"] == 3000
    assert written_df.iloc[0]["market_total_trade_amount"] == 50000
    assert written_df.iloc[0]["floating_market_capitalization"] == 123456


@pytest.mark.asyncio
async def test_tushare_lockup_release_normalizes_shares():
    """Tushare 限售解禁股份字段官方单位为股，应原样写入股。"""
    ingestor = TushareIngestor.__new__(TushareIngestor)
    ingestor.source = "tushare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    fake_pro = Mock()
    fake_pro.share_float = Mock()
    ingestor.pro = fake_pro
    source_df = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "float_date": "20260618",
                "float_share": 123456.0,
                "float_ratio": 1.2,
                "share_type": "首发原股东限售股份",
                "holder_name": "股东A",
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(return_value=source_df)

    result = await ingestor.fetch_and_ingest_stock_lockup_release("000001.SZ")

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result["success"] is True
    assert written_df.iloc[0]["release_shares"] == pytest.approx(123456.0)


@pytest.mark.asyncio
async def test_tushare_limit_pool_normalizes_market_values_to_cny():
    """Tushare 涨停池市值字段官方返回元，应原样写入元。"""
    ingestor = TushareIngestor.__new__(TushareIngestor)
    ingestor.source = "tushare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    fake_pro = Mock()
    fake_pro.limit_list_d = Mock()
    ingestor.pro = fake_pro
    source_df = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "trade_date": "20260618",
                "close": 10.5,
                "pct_chg": 10.0,
                "amount": 123456,
                "float_mv": 1000,
                "total_mv": 2000,
                "turnover_ratio": 5.5,
                "fd_amount": 300000,
                "first_time": "09:25:00",
                "last_time": "09:30:00",
                "open_times": 0,
                "up_stat": "1/1",
                "limit_times": "1",
                "industry": "银行",
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(return_value=source_df)

    result = await ingestor.fetch_and_ingest_stock_limit_up_pool("20260618")

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result["success"] is True
    assert written_df.iloc[0]["circ_mv"] == 1000
    assert written_df.iloc[0]["total_mv"] == 2000


@pytest.mark.asyncio
async def test_tushare_sector_money_flow_keeps_official_cny_amounts():
    """Tushare 东财行业资金流金额字段官方单位为元，应原样写入元。"""
    ingestor = TushareIngestor.__new__(TushareIngestor)
    ingestor.source = "tushare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    fake_pro = Mock()
    ingestor.pro = fake_pro
    source_df = pd.DataFrame(
        [
            {
                "name": "银行",
                "trade_date": "20260618",
                "net_amount": 1200,
                "net_amount_rate": 1.5,
                "buy_elg_amount": 500,
                "buy_elg_amount_rate": 0.6,
                "buy_lg_amount": 300,
                "buy_lg_amount_rate": 0.4,
                "buy_md_amount": 200,
                "buy_md_amount_rate": 0.3,
                "buy_sm_amount": 100,
                "buy_sm_amount_rate": 0.2,
                "close": 1234.5,
                "pct_change": 2.3,
                "rank": 1,
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(side_effect=["银行", source_df])

    result = await ingestor.fetch_and_ingest_sector_money_flow("000001.SZ")

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result["success"] is True
    assert written_df.iloc[0]["net_inflow"] == 1200
    assert written_df.iloc[0]["main_net_inflow"] == 1200
    assert written_df.iloc[0]["huge_net_inflow"] == 500
    assert written_df.iloc[0]["large_net_inflow"] == 300
    assert written_df.iloc[0]["medium_net_inflow"] == 200
    assert written_df.iloc[0]["small_net_inflow"] == 100


def test_tushare_pledge_detail_maps_holder_ratio_to_model_field():
    """Tushare 质押明细持股占比字段应映射到模型字段。"""
    source_df = pd.DataFrame(
        [
            {
                "ts_code": "000001.SZ",
                "ann_date": "20260618",
                "holder_name": "股东A",
                "pledgor": "机构A",
                "pledge_amount": 100,
                "start_date": "20260618",
                "release_date": "20260620",
                "p_total_ratio": 1.1,
                "h_total_ratio": 2.2,
                "holding_amount": 1000,
                "is_buyback": "N",
                "is_release": "N",
                "pledged_amount": 100,
            }
        ]
    )

    mapped_df = ColumnMapper.map_columns(source_df, "data.stock_pledge_risk", source="tushare")

    assert "pledge_ratio_to_holder" in mapped_df.columns
    assert "h_total_ratio" not in mapped_df.columns
    assert mapped_df.iloc[0]["pledge_ratio_to_holder"] == 2.2


@pytest.mark.asyncio
async def test_akshare_realtime_market_keeps_volume_in_shares():
    """AKShare 实时行情应按统一表约定保留成交量的股数单位。"""
    ingestor = AkshareIngestor.__new__(AkshareIngestor)
    ingestor.source = "akshare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    source_df = pd.DataFrame(
        [
            {
                "代码": "000001",
                "名称": "平安银行",
                "最新价": 10.0,
                "涨跌幅": 1.0,
                "涨跌额": 0.1,
                "成交量": 123456,
                "成交额": 1234560,
                "最高": 10.1,
                "最低": 9.9,
                "今开": 9.95,
                "昨收": 9.9,
                "买入": 9.99,
                "卖出": 10.0,
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(return_value=source_df)

    result = await ingestor.fetch_and_ingest_realtime_market()

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result["success"] is True
    assert written_df.iloc[0]["volume"] == 123456


@pytest.mark.asyncio
async def test_akshare_block_trade_normalizes_source_units():
    """AKShare 大宗交易原始股数、元和比例应换算为库表约定单位。"""
    ingestor = AkshareIngestor.__new__(AkshareIngestor)
    ingestor.source = "akshare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    source_df = pd.DataFrame(
        [
            {
                "交易日期": "2026-06-18",
                "证券代码": "000001",
                "成交价": 10.5,
                "折溢率": -0.053452,
                "成交量": 302100,
                "成交额": 1283900,
                "买方营业部": "买方",
                "卖方营业部": "卖方",
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(return_value=source_df)

    result = await ingestor.fetch_and_ingest_stock_block_trade(
        stock_code="000001.SZ",
        start_date="20260618",
        end_date="20260618",
    )

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result["success"] is True
    assert written_df.iloc[0]["volume"] == pytest.approx(30.21)
    assert written_df.iloc[0]["amount"] == pytest.approx(128.39)
    assert written_df.iloc[0]["premium_rate"] == pytest.approx(-5.3452)


@pytest.mark.asyncio
async def test_akshare_lockup_release_converts_ratio_to_percent():
    """AKShare 解禁占比原始比例应换算为库表约定的百分数。"""
    ingestor = AkshareIngestor.__new__(AkshareIngestor)
    ingestor.source = "akshare"
    ingestor.ingestion_service = Mock()
    ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)
    source_df = pd.DataFrame(
        [
            {
                "解禁时间": "2026-06-18",
                "解禁数量": 2_000_000,
                "实际解禁数量": 1_500_000,
                "未解禁数量": 500_000,
                "实际解禁数量市值": 30_000_000,
                "占总市值比例": 0.014691,
                "占流通市值比例": 0.025,
                "限售股类型": "首发原股东限售股份",
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(return_value=source_df)

    result = await ingestor.fetch_and_ingest_stock_lockup_release("000001.SZ")

    written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
    assert result["success"] is True
    assert written_df.iloc[0]["release_shares"] == 1_500_000
    assert written_df.iloc[0]["release_market_value"] == 3000.0
    assert written_df.iloc[0]["ratio_to_total"] == pytest.approx(1.4691)
    assert written_df.iloc[0]["ratio_to_float"] == pytest.approx(2.5)


class TestFailoverMechanism:
    """测试灾备切换机制"""

    @pytest.mark.asyncio
    async def test_failover_skips_unregistered_default_source(
        self, test_stock_code, test_date_range
    ):
        """测试默认源不可用时仍可使用 tushare"""
        start_date, end_date = test_date_range

        tushare_ingestor = ingestor_manager.get_ingestor('tushare')

        with patch('app.data.ingestors.manager.settings.ENABLE_DATA_SOURCE_FAILOVER', True):
            with patch.object(ingestor_manager, 'default_source', 'removed_source'):
                with patch.object(
                    tushare_ingestor,
                    'fetch_and_ingest_stock_kline',
                    return_value=True
                ) as mock_tushare:
                    result = await ingestor_manager.fetch_and_ingest_stock_kline(
                        stock_code=test_stock_code,
                        start_date=start_date,
                        end_date=end_date,
                        adjust="qfq"
                    )

                    mock_tushare.assert_called_once()
                    assert result is True, "tushare 应该在默认源不可用时可用"

    @pytest.mark.asyncio
    async def test_failover_all_sources_failed(
        self, test_stock_code, test_date_range
    ):
        """测试所有数据源都失败的情况"""
        start_date, end_date = test_date_range

        tushare_ingestor = ingestor_manager.get_ingestor('tushare')
        akshare_ingestor = ingestor_manager.get_ingestor('akshare')

        with patch('app.data.ingestors.manager.settings.ENABLE_DATA_SOURCE_FAILOVER', True):
            with patch.object(ingestor_manager, 'default_source', 'tushare'):
                with patch.object(
                    tushare_ingestor,
                    'fetch_and_ingest_stock_kline',
                    return_value=False
                ):
                    with patch.object(
                        akshare_ingestor,
                        'fetch_and_ingest_stock_kline',
                        return_value=False
                    ):
                        result = await ingestor_manager.fetch_and_ingest_stock_kline(
                            stock_code=test_stock_code,
                            start_date=start_date,
                            end_date=end_date,
                            adjust="qfq"
                        )

                        assert result is False, "所有数据源失败应该返回 False"

    @pytest.mark.asyncio
    async def test_failover_first_source_success(
        self, test_stock_code, test_date_range
    ):
        """测试第一个数据源成功，不需要切换"""
        start_date, end_date = test_date_range

        tushare_ingestor = ingestor_manager.get_ingestor('tushare')

        # 模拟 tushare 成功
        with patch.object(ingestor_manager, 'default_source', 'tushare'):
            with patch.object(
                tushare_ingestor,
                'fetch_and_ingest_stock_kline',
                return_value=True
            ) as mock_tushare:
                result = await ingestor_manager.fetch_and_ingest_stock_kline(
                    stock_code=test_stock_code,
                    start_date=start_date,
                    end_date=end_date,
                    adjust="qfq"
                )

                mock_tushare.assert_called_once()
                assert result is True, "第一个数据源成功应该返回 True"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
