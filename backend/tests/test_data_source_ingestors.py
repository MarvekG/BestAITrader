#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试启用的数据源采集功能
Test enabled data sources for data ingestion
"""

import pytest
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from app.core.config import settings
from app.data.ingestors.plugins.akshare_ingestor import AkshareIngestor
from app.data.ingestors.manager import ingestor_manager
from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
from app.core.utils.date_utils import normalize_compact_date


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

    @pytest.mark.asyncio
    async def test_fetch_stock_announcements_returns_false_when_not_implemented(
        self, test_stock_code
    ):
        mock_pro = Mock()
        mock_pro.anns_d = Mock()

        mock_ingestion_service = Mock()
        mock_ingestion_service.write_dataframe = Mock(return_value=True)

        with patch(
            'app.data.ingestors.plugins.tushare_ingestor.DataIngestionService',
            return_value=mock_ingestion_service,
        ), patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
            ingestor = TushareIngestor()

        ingestor.pro = mock_pro

        result = await ingestor.fetch_and_ingest_stock_announcements(test_stock_code)

        assert result is False
        mock_pro.anns_d.assert_not_called()
        mock_ingestion_service.write_dataframe.assert_not_called()

    def test_tushare_ingestor_does_not_expose_concept_board_sync(self):
        """
        Tushare 采集器不再提供概念板块行情同步入口。
        """
        assert not hasattr(TushareIngestor, "fetch_and_ingest_board_concept")

    @pytest.mark.asyncio
    async def test_fetch_all_stock_basic_does_not_retry_entire_batch_on_executor_failure(self):
        """全量基础信息同步遇到执行器异常时不重复重跑整个批次。"""
        with patch('app.data.ingestors.plugins.tushare_ingestor.DataIngestionService', return_value=Mock()), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
            ingestor = TushareIngestor()
        ingestor._run_in_executor = AsyncMock(side_effect=ConnectionError("upstream closed"))

        with pytest.raises(ConnectionError):
            await ingestor.fetch_and_ingest_all_stock_basic()

        assert ingestor._run_in_executor.await_count == 1


@pytest.mark.asyncio
async def test_akshare_realtime_market_keeps_volume_in_shares():
    """AKShare 实时行情应按统一表约定保留成交量的股数单位。"""
    ingestor = AkshareIngestor.__new__(AkshareIngestor)
    ingestor.source = "akshare"
    ingestor.ingestion_service = Mock()
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
    ingestor._run_in_executor = AsyncMock(side_effect=[source_df, True])

    result = await ingestor.fetch_and_ingest_realtime_market()

    written_df = ingestor._run_in_executor.await_args_list[1].args[2]
    assert result["success"] is True
    assert written_df.iloc[0]["volume"] == 123456


@pytest.mark.asyncio
async def test_akshare_block_trade_keeps_source_display_units():
    """AKShare 大宗交易已返回万股、万元和百分数，不应重复换算。"""
    ingestor = AkshareIngestor.__new__(AkshareIngestor)
    ingestor.source = "akshare"
    ingestor.ingestion_service = Mock()
    source_df = pd.DataFrame(
        [
            {
                "交易日期": "2026-06-18",
                "证券代码": "000001",
                "成交价": 10.5,
                "折溢率": 1.25,
                "成交量": 20.0,
                "成交额": 210.0,
                "买方营业部": "买方",
                "卖方营业部": "卖方",
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(side_effect=[source_df, True])

    result = await ingestor.fetch_and_ingest_stock_block_trade(
        stock_code="000001.SZ",
        start_date="20260618",
        end_date="20260618",
    )

    written_df = ingestor._run_in_executor.await_args_list[1].args[2]
    assert result["success"] is True
    assert written_df.iloc[0]["volume"] == 20.0
    assert written_df.iloc[0]["amount"] == 210.0
    assert written_df.iloc[0]["premium_rate"] == 1.25


@pytest.mark.asyncio
async def test_akshare_lockup_release_keeps_percentage_values():
    """AKShare 解禁占比已是百分数，只转换解禁市值为万元。"""
    ingestor = AkshareIngestor.__new__(AkshareIngestor)
    ingestor.source = "akshare"
    ingestor.ingestion_service = Mock()
    source_df = pd.DataFrame(
        [
            {
                "解禁时间": "2026-06-18",
                "解禁数量": 2_000_000,
                "实际解禁数量": 1_500_000,
                "未解禁数量": 500_000,
                "实际解禁数量市值": 30_000_000,
                "占总市值比例": 0.8,
                "占流通市值比例": 1.2,
                "限售股类型": "首发原股东限售股份",
            }
        ]
    )
    ingestor._run_in_executor = AsyncMock(side_effect=[source_df, True])

    result = await ingestor.fetch_and_ingest_stock_lockup_release("000001.SZ")

    written_df = ingestor._run_in_executor.await_args_list[1].args[2]
    assert result["success"] is True
    assert written_df.iloc[0]["release_shares"] == 1_500_000
    assert written_df.iloc[0]["release_market_value"] == 3000.0
    assert written_df.iloc[0]["ratio_to_total"] == 0.8
    assert written_df.iloc[0]["ratio_to_float"] == 1.2


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
                with patch.object(settings, 'TUSHARE_TOKEN', 'test-token'):
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

        with patch('app.data.ingestors.manager.settings.ENABLE_DATA_SOURCE_FAILOVER', True):
            with patch.object(ingestor_manager, 'default_source', 'tushare'):
                with patch.object(settings, 'TUSHARE_TOKEN', 'test-token'):
                    with patch.object(
                        tushare_ingestor,
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
            with patch.object(settings, 'TUSHARE_TOKEN', 'test-token'):
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

    @pytest.mark.asyncio
    async def test_failover_skips_unavailable_source(
        self, test_stock_code, test_date_range
    ):
        """测试缺少配置时 manager 会跳过不可用数据源"""
        start_date, end_date = test_date_range
        tushare_ingestor = ingestor_manager.get_ingestor('tushare')

        with patch.object(ingestor_manager, 'default_source', 'tushare'):
            with patch.object(settings, 'TUSHARE_TOKEN', ''):
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

                    mock_tushare.assert_not_called()
                    assert result is False, "不可用数据源应被跳过"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
