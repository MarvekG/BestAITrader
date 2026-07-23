from datetime import date
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

import pandas as pd

from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor


class TestTushareShareholderCount(IsolatedAsyncioTestCase):
    async def test_complements_daily_basic_with_mixed_datetime_units(self):
        """股东户数与每日指标日期精度不同时仍应完成基本面补全。"""
        shareholder_df = pd.DataFrame(
            [
                {
                    "ts_code": "300308.SZ",
                    "end_date": "20260331",
                    "holder_num": 100_000,
                    "ann_date": "20260420",
                },
                {
                    "ts_code": "300308.SZ",
                    "end_date": "20260630",
                    "holder_num": 110_000,
                    "ann_date": "20260720",
                },
            ]
        )
        daily_basic_df = pd.DataFrame(
            [
                {
                    "ts_code": "300308.SZ",
                    "trade_date": "20260331",
                    "total_share": 125_619.78,
                    "total_mv": 2_280_000.0,
                    "close": 100.0,
                },
                {
                    "ts_code": "300308.SZ",
                    "trade_date": "20260630",
                    "total_share": 125_619.78,
                    "total_mv": 2_120_000.0,
                    "close": 110.0,
                },
            ]
        )

        ingestor = TushareIngestor.__new__(TushareIngestor)
        ingestor.source = "tushare"
        ingestor.pro = Mock()
        ingestor.ensure_pro = AsyncMock(return_value=ingestor.pro)
        ingestor._run_in_executor = AsyncMock(
            side_effect=[shareholder_df, daily_basic_df]
        )
        ingestor.ingestion_service = Mock()
        ingestor.ingestion_service.write_dataframe = AsyncMock(return_value=True)

        result = await ingestor.fetch_and_ingest_stock_shareholder_count("300308.SZ")

        self.assertTrue(result["success"])
        written_df = ingestor.ingestion_service.write_dataframe.await_args.args[1]
        latest = written_df.loc[
            written_df["end_date"] == date(2026, 6, 30)
        ].iloc[0]
        self.assertAlmostEqual(latest["total_share"], 125_619.78 * 10_000)
        self.assertAlmostEqual(latest["total_mv"], 2_120_000.0 * 10_000)
        self.assertAlmostEqual(latest["price_at_end"], 110.0)
        self.assertAlmostEqual(
            latest["avg_hold_shares"],
            (125_619.78 * 10_000) / 110_000,
        )
        self.assertAlmostEqual(
            latest["avg_hold_value"],
            (2_120_000.0 * 10_000) / 110_000,
        )
        self.assertAlmostEqual(latest["price_change_ratio"], 10.0)
        self.assertNotIn("tmp_merge_date", written_df.columns)
