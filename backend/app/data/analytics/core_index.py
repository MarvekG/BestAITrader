from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional

import pandas as pd

from app.core.config import settings
from app.core.logger import get_logger
from app.core.utils.formatters import StockCodeStandardizer
from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor

logger = get_logger(__name__)


def _month_range(target_date: date) -> tuple[str, str]:
    """Build the YYYYMMDD month window for a given date."""
    month_start = target_date.replace(day=1)
    next_month_anchor = (month_start + timedelta(days=32)).replace(day=1)
    month_end = next_month_anchor - timedelta(days=1)
    return month_start.strftime("%Y%m%d"), month_end.strftime("%Y%m%d")


def _build_candidate_ranges(as_of: Optional[date] = None) -> List[tuple[str, str]]:
    """Build recent monthly windows for querying index constituents."""
    anchor = as_of or datetime.now().date()
    ranges: List[tuple[str, str]] = []
    current = anchor.replace(day=1)

    for _ in range(3):
        start_date, end_date = _month_range(current)
        ranges.append((start_date, end_date))
        current = (current - timedelta(days=1)).replace(day=1)

    return ranges


async def get_core_index_constituent_codes(
    index_codes: Optional[Iterable[str]] = None,
    *,
    as_of: Optional[date] = None,
) -> List[str]:
    """通过 Tushare 获取核心指数成分股代码。

    Tushare `index_weight` provides monthly constituent/weight snapshots.
    We query recent month windows only and raise immediately if no usable data
    is returned.

    Args:
        index_codes: 指数代码列表；为空时使用 settings.CORE_INDICES。
        as_of: 用于选择最近月度窗口的参考日期。

    Returns:
        排序后的标准化股票代码列表。
    """
    resolved_index_codes = list(index_codes or settings.CORE_INDICES)
    pro = await TushareIngestor.get_pro_client()
    ranges = _build_candidate_ranges(as_of=as_of)
    resolved_codes = set()

    for raw_index_code in resolved_index_codes:
        index_code = StockCodeStandardizer.to_standard_index(raw_index_code)
        df: Optional[pd.DataFrame] = None

        for start_date, end_date in ranges:
            try:
                df = await asyncio.to_thread(
                    pro.index_weight,
                    index_code=index_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as exc:
                logger.warning(
                    "core index constituent query failed: index_code=%s start=%s end=%s error=%s",
                    index_code,
                    start_date,
                    end_date,
                    exc,
                )
                continue

            if df is not None and not df.empty:
                break

        if df is None or df.empty:
            raise RuntimeError(
                f"Tushare returned no constituent data for core index {index_code}"
            )

        if "trade_date" in df.columns and df["trade_date"].notna().any():
            latest_trade_date = df["trade_date"].max()
            df = df[df["trade_date"] == latest_trade_date].copy()

        if "con_code" not in df.columns:
            raise RuntimeError(
                f"Tushare core index constituent response missing con_code for {index_code}"
            )

        resolved_codes.update(
            StockCodeStandardizer.standardize(code)
            for code in df["con_code"].dropna().tolist()
        )

    if not resolved_codes:
        raise RuntimeError("Tushare returned no constituent codes for configured core indices")

    final_codes = sorted(resolved_codes)
    logger.info(
        "core index constituent query succeeded: index_count=%s resolved_count=%s",
        len(resolved_index_codes),
        len(final_codes),
    )
    return final_codes
