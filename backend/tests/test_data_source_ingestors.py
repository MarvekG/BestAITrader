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
from app.data.ingestors.manager import ingestor_manager
from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
from app.core.utils.date_utils import normalize_compact_date
from app.models.data_storage import StockBasic


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
    async def test_fetch_stock_daily_with_mock(
        self, test_stock_code, test_date_range, mock_tushare_data
    ):
        """测试 tushare 日线数据采集 (使用模拟数据)"""
        start_date, end_date = test_date_range
        ingestor = ingestor_manager.get_ingestor('tushare')

        # 模拟 tushare pro 对象
        mock_pro = Mock()
        mock_pro.daily = Mock(return_value=mock_tushare_data)

        with patch.object(ingestor, 'pro', mock_pro):
            result = await ingestor.fetch_and_ingest_stock_kline(
                stock_code=test_stock_code,
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )

            assert result is True, "tushare 数据采集应该成功"

    @pytest.mark.asyncio
    async def test_fetch_stock_daily_no_token(
        self, test_stock_code, test_date_range
    ):
        """测试 tushare 没有 token 的情况"""
        start_date, end_date = test_date_range
        ingestor = ingestor_manager.get_ingestor('tushare')

        # 模拟没有 token
        with patch.object(ingestor, 'pro', None):
            result = await ingestor.fetch_and_ingest_stock_kline(
                stock_code=test_stock_code,
                start_date=start_date,
                end_date=end_date,
                adjust="qfq"
            )

            assert result is False, "没有 token 应该返回 False"

    @pytest.mark.asyncio
    async def test_fetch_stock_valuation_keeps_share_fields_in_valuation(self, test_stock_code, db_session):
        """daily_basic 同步应把股本字段保留在估值表写入数据中。"""
        db_session.add(StockBasic(stock_code=test_stock_code, name="平安银行", data_source="tushare"))
        db_session.commit()

        mock_pro = Mock()
        mock_pro.daily_basic = Mock(return_value=pd.DataFrame({
            'ts_code': [test_stock_code],
            'trade_date': ['20260610'],
            'close': [10.0],
            'pe': [8.0],
            'pe_ttm': [8.5],
            'pb': [1.2],
            'ps': [1.0],
            'ps_ttm': [1.1],
            'dv_ratio': [3.0],
            'dv_ttm': [3.2],
            'total_share': [500000.0],
            'float_share': [450000.0],
            'free_share': [400000.0],
            'total_mv': [5_000_000.0],
            'circ_mv': [4_500_000.0],
            'turnover_rate': [0.8],
            'turnover_rate_f': [1.0],
            'volume_ratio': [0.9],
        }))

        mock_ingestion_service = Mock()
        mock_ingestion_service.write_dataframe = Mock(return_value=True)

        with patch('app.data.ingestors.plugins.tushare_ingestor.DataIngestionService', return_value=mock_ingestion_service), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.set_token'), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
            ingestor = TushareIngestor()
        ingestor._run_in_executor = AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))

        with patch.object(ingestor, 'pro', mock_pro):
            result = await ingestor.fetch_and_ingest_stock_valuation(test_stock_code, start_date='2026-06-10')

        assert result is True
        stock = db_session.query(StockBasic).filter(StockBasic.stock_code == test_stock_code).first()
        assert stock.total_share is None
        assert stock.float_share is None

        df_arg = mock_ingestion_service.write_dataframe.call_args[0][1]
        assert df_arg['total_share'].iloc[0] == 500000.0 * 10000
        assert df_arg['float_share'].iloc[0] == 450000.0 * 10000
        assert df_arg['free_share'].iloc[0] == 400000.0 * 10000

    @pytest.mark.asyncio
    async def test_fetch_financial_indicators_uses_fina_indicator_without_storing_definitions(
        self, test_stock_code
    ):
        mock_pro = Mock()
        mock_pro.fina_indicator = Mock(return_value=pd.DataFrame({
            'ts_code': [test_stock_code],
            'ann_date': ['20260115'],
            'end_date': ['20251231'],
            'eps': [1.23],
            'dt_eps': [1.11],
            'roe': [15.6],
            'roe_dt': [15.2],
            'capital_rese_ps': [2.34],
            'surplus_rese_ps': [1.23],
            'undist_profit_ps': [5.67],
            'retainedps': [4.56],
            'assets_turn': [0.88],
            'gross_margin': [123456.78],
            'grossprofit_margin': [27.5],
            'netprofit_yoy': [22.5],
        }))

        mock_ingestion_service = Mock()
        mock_ingestion_service.write_dataframe = Mock(return_value=True)

        with patch('app.data.ingestors.plugins.tushare_ingestor.DataIngestionService', return_value=mock_ingestion_service), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.set_token'), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
            ingestor = TushareIngestor()
        ingestor._run_in_executor = AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))

        with patch.object(ingestor, 'pro', mock_pro), \
             patch.object(ingestor.ingestion_service, 'write_dataframe', return_value=True) as mock_write:
            result = await ingestor.fetch_and_ingest_financial_indicators(test_stock_code)

        assert result is True
        args = mock_write.call_args[0]
        assert args[0] == 'fina_indicator'
        final_df = args[1]
        assert len(final_df) == 1
        payload = final_df.iloc[0]['data']
        assert payload['eps'] == 1.23
        assert payload['diluted_eps'] == 1.11
        assert payload['roe'] == 15.6
        assert payload['roe_diluted'] == 15.2
        assert payload['capital_reserve_ps'] == 2.34
        assert payload['surplus_reserve_ps'] == 1.23
        assert payload['undistributed_profit_ps'] == 5.67
        assert payload['retained_earnings_ps'] == 4.56
        assert payload['asset_turnover'] == 0.88
        assert payload['gross_margin'] == 123456.78
        assert payload['grossprofit_margin'] == 27.5
        assert payload['net_profit_yoy'] == 22.5
        assert '_indicator_definitions' not in payload

    @pytest.mark.asyncio
    async def test_fetch_financial_indicators_passes_start_date_to_fina_indicator(
        self, test_stock_code
    ):
        mock_pro = Mock()
        mock_pro.fina_indicator = Mock(return_value=pd.DataFrame({
            'ts_code': [test_stock_code],
            'ann_date': ['20260115'],
            'end_date': ['20251231'],
            'eps': [1.23],
        }))

        mock_ingestion_service = Mock()
        mock_ingestion_service.write_dataframe = Mock(return_value=True)

        with patch('app.data.ingestors.plugins.tushare_ingestor.DataIngestionService', return_value=mock_ingestion_service), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.set_token'), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
            ingestor = TushareIngestor()
        ingestor._run_in_executor = AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))
        ingestor.pro = mock_pro

        result = await ingestor.fetch_and_ingest_financial_indicators(
            test_stock_code,
            start_date='2024-01-01',
        )

        assert result is True
        mock_pro.fina_indicator.assert_called_once_with(
            ts_code=test_stock_code,
            start_date='20240101',
        )

    @pytest.mark.asyncio
    async def test_fetch_financial_indicators_parses_tushare_dates_as_yyyymmdd(
        self, test_stock_code
    ):
        mock_pro = Mock()
        mock_pro.fina_indicator = Mock(return_value=pd.DataFrame({
            'ts_code': [test_stock_code],
            'ann_date': [20260115],
            'end_date': [20250930],
            'eps': [1.23],
        }))

        mock_ingestion_service = Mock()
        mock_ingestion_service.write_dataframe = Mock(return_value=True)

        with patch('app.data.ingestors.plugins.tushare_ingestor.DataIngestionService', return_value=mock_ingestion_service), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.set_token'), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
            ingestor = TushareIngestor()
        ingestor._run_in_executor = AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))
        ingestor.pro = mock_pro

        with patch.object(ingestor.ingestion_service, 'write_dataframe', return_value=True) as mock_write:
            result = await ingestor.fetch_and_ingest_financial_indicators(test_stock_code)

        assert result is True
        final_df = mock_write.call_args[0][1]
        assert final_df.iloc[0]['report_date'].isoformat() == '2025-09-30'
        assert final_df.iloc[0]['announcement_date'].isoformat() == '2026-01-15'

    @pytest.mark.asyncio
    async def test_fetch_stock_announcements_returns_false_when_not_implemented(
        self, test_stock_code
    ):
        mock_pro = Mock()
        mock_pro.anns_d = Mock()

        mock_ingestion_service = Mock()
        mock_ingestion_service.write_dataframe = Mock(return_value=True)

        with patch('app.data.ingestors.plugins.tushare_ingestor.DataIngestionService', return_value=mock_ingestion_service), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
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
    async def test_fetch_stock_earnings_forecast_ignores_missing_update_flag(
        self, test_stock_code
    ):
        mock_pro = Mock()
        mock_pro.forecast = Mock(return_value=pd.DataFrame({
            'ts_code': [test_stock_code],
            'ann_date': ['20260115'],
            'end_date': ['20251231'],
            'type': ['预增'],
            'p_change_min': [10.0],
            'p_change_max': [20.0],
            'net_profit_min': [100000.0],
            'net_profit_max': [120000.0],
            'last_parent_net': [90000.0],
            'summary': ['业绩增长'],
            'change_reason': ['主营业务改善'],
            'first_ann_date': ['20260110'],
        }))

        with patch('app.data.ingestors.plugins.tushare_ingestor.DataIngestionService', return_value=Mock()), \
             patch('app.data.ingestors.plugins.tushare_ingestor.ts.pro_api', return_value=Mock()):
            ingestor = TushareIngestor()
        ingestor._run_in_executor = AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs))

        with patch.object(ingestor, 'pro', mock_pro), \
             patch.object(ingestor.ingestion_service, 'write_dataframe', return_value=True) as mock_write:
            result = await ingestor.fetch_and_ingest_stock_earnings_forecast(test_stock_code)

        assert result is True
        final_df = mock_write.call_args[0][1]
        assert final_df.iloc[0]['stock_code'] == test_stock_code
        assert final_df.iloc[0]['report_date'].isoformat() == '2025-12-31'
        assert final_df.iloc[0]['ann_date'].isoformat() == '2026-01-15'
        assert 'update_flag' not in final_df.columns
        assert '_unused_update_flag' not in final_df.columns

    @pytest.mark.asyncio
    async def test_fetch_stock_pledge_risk_with_mock(
        self, test_stock_code
    ):
        """测试 tushare 股权质押数据采集 (使用模拟数据)"""
        ingestor = ingestor_manager.get_ingestor('tushare')

        # 模拟数据
        mock_df = pd.DataFrame({
            'ts_code': [test_stock_code],
            'ann_date': ['20230101'],
            'holder_name': ['Test Holder'],
            'pledge_amount': [100.0],  # 100万股
            'start_date': ['20230101'],
            'end_date': ['20240101'],
            'is_release': ['0'],
            'release_date': [None],
            'pledgor': ['Test Pledgor'],
            'holding_amount': [1000.0],
            'pledged_amount': [100.0],
            'p_total_ratio': [1.5],
            'h_total_ratio': [10.0],
            'is_buyback': ['0']
        })

        # 模拟 tushare pro 对象
        mock_pro = Mock()
        mock_pro.pledge_detail = Mock(return_value=mock_df)

        with patch.object(ingestor, 'pro', mock_pro):
            # 模拟 ingestion_service.write_dataframe 避免实际写入数据库
            with patch.object(ingestor.ingestion_service, 'write_dataframe', new_callable=Mock) as mock_write:
                mock_write.return_value = True
                result = await ingestor.fetch_and_ingest_stock_pledge_risk(test_stock_code)

                assert result is True, "tushare 股权质押数据采集应该成功"
                mock_pro.pledge_detail.assert_called_once_with(ts_code=test_stock_code)
                mock_write.assert_called_once()
                # 验证 mock_write 被调用时的参数
                call_args = mock_write.call_args
                assert call_args[0][0] == 'pledge_detail'
                df_arg = call_args[0][1]
                assert 'pledge_shares' in df_arg.columns
                # pledge_amount was 100 (万股), should become 100 * 10000 = 1000000
                assert df_arg['pledge_shares'].iloc[0] == 1000000


    @pytest.mark.asyncio
    async def test_fetch_stock_pledge_risk_empty_mock(
        self, test_stock_code
    ):
        """测试 tushare 股权质押数据采集 (数据为空)"""
        ingestor = ingestor_manager.get_ingestor('tushare')

        # 模拟空数据
        mock_df = pd.DataFrame()
        mock_pro = Mock()
        mock_pro.pledge_detail = Mock(return_value=mock_df)

        with patch.object(ingestor, 'pro', mock_pro):
            result = await ingestor.fetch_and_ingest_stock_pledge_risk(
                stock_code=test_stock_code
            )

            assert result is True, "无数据时应该返回 True"

    @pytest.mark.asyncio
    async def test_fetch_stock_lockup_release_preserves_tushare_raw_shares(
        self, test_stock_code
    ):
        """测试 Tushare 限售股解禁数量按官方股数单位写入。"""
        ingestor = ingestor_manager.get_ingestor('tushare')
        mock_df = pd.DataFrame({
            'ts_code': [test_stock_code],
            'float_date': ['20260630'],
            'float_share': [5_000_000.0],
            'float_ratio': [1.2],
            'share_type': ['首发原股东'],
            'holder_name': ['Test Holder'],
        })
        mock_pro = Mock()
        mock_pro.share_float = Mock(return_value=mock_df)

        with patch.object(ingestor, 'pro', mock_pro):
            with patch.object(ingestor.ingestion_service, 'write_dataframe', new_callable=Mock) as mock_write:
                mock_write.return_value = True
                result = await ingestor.fetch_and_ingest_stock_lockup_release(test_stock_code)

        assert result is True
        mock_pro.share_float.assert_called_once()
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == 'share_float'
        df_arg = call_args[0][1]
        assert df_arg['release_shares'].iloc[0] == 5_000_000


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
