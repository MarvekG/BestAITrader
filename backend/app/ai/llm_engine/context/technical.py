from typing import Dict, Any, List, Sequence
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.ai.llm_engine.context.calculations import average, percent_change, to_float
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import KlineData, StockRealtimeMarket, IndexDaily
from app.models.stock_indicators import StockIndicators


def _max_drawdown_pct(klines: Sequence[Any]) -> float | None:
    peak = None
    max_drawdown = 0.0
    for kline in klines:
        high = to_float(getattr(kline, "high", None))
        low = to_float(getattr(kline, "low", None))
        if high is not None:
            peak = high if peak is None else max(peak, high)
        if peak not in (None, 0) and low is not None:
            drawdown = (low - peak) / peak * 100
            max_drawdown = min(max_drawdown, drawdown)
    return round(max_drawdown, 4)


def _build_price_volume_summary_payload(
    klines: Sequence[Any],
    indicators: Any | None = None,
) -> Dict[str, Any]:
    if not klines:
        return {"status": "missing"}

    ordered = sorted(klines, key=lambda item: item.date)
    latest = ordered[-1]
    latest_close = to_float(latest.close)
    start_close = to_float(ordered[0].close)
    latest_volume = to_float(latest.volume)
    high_record = max(ordered, key=lambda item: to_float(item.high) or float("-inf"))
    low_record = min(ordered, key=lambda item: to_float(item.low) or float("inf"))
    high_price = to_float(high_record.high)
    low_price = to_float(low_record.low)
    recent_5 = ordered[-5:]
    recent_20 = ordered[-20:]
    avg_volume_5d = average([item.volume for item in recent_5])
    avg_volume_20d = average([item.volume for item in recent_20])
    avg_turnover_5d = average([item.turnover for item in recent_5])
    avg_turnover_20d = average([item.turnover for item in recent_20])
    atr = to_float(getattr(indicators, "atr", None)) if indicators else None
    indicator_date = getattr(indicators, "trade_date", None) if indicators else None

    payload = {
        "status": "available",
        "data_sources": ["data.kline_data", "data.stock_indicators"],
        "scope": (
            f"{len(ordered)} daily K-line records from {ordered[0].date} to {latest.date}; "
            f"ATR date {indicator_date or 'missing'}"
        ),
        "window_records": len(ordered),
        "start_date": str(ordered[0].date),
        "end_date": str(latest.date),
        "indicator_date": str(indicator_date) if indicator_date else None,
        "start_close": start_close,
        "latest_close": latest_close,
        "window_return_pct": percent_change(latest_close, start_close),
        "window_high_price": high_price,
        "window_high_date": str(high_record.date),
        "window_low_price": low_price,
        "window_low_date": str(low_record.date),
        "drawdown_from_window_high_pct": percent_change(latest_close, high_price),
        "rebound_from_window_low_pct": percent_change(latest_close, low_price),
        "max_drawdown_pct": _max_drawdown_pct(ordered),
        "latest_volume": latest_volume,
        "avg_volume_5d": avg_volume_5d,
        "avg_volume_20d": avg_volume_20d,
        "volume_vs_5d_avg_pct": percent_change(latest_volume, avg_volume_5d),
        "volume_vs_20d_avg_pct": percent_change(latest_volume, avg_volume_20d),
        "volume_ratio_vs_20d": (
            round(latest_volume / avg_volume_20d, 4)
            if latest_volume is not None and avg_volume_20d not in (None, 0)
            else None
        ),
        "latest_turnover": latest.turnover,
        "avg_turnover_5d": avg_turnover_5d,
        "avg_turnover_20d": avg_turnover_20d,
        "atr": atr,
        "atr_pct": round(atr / latest_close * 100, 4) if latest_close not in (None, 0) and atr is not None else None,
        "one_atr_stop_price": round(latest_close - atr, 4) if latest_close is not None and atr is not None else None,
        "two_atr_stop_price": round(latest_close - atr * 2, 4) if latest_close is not None and atr is not None else None,
        "change_bases": {
            "window_return_pct": f"latest_close({latest.date}) vs start_close({ordered[0].date})",
            "drawdown_from_window_high_pct": f"latest_close({latest.date}) vs window_high({high_record.date})",
            "rebound_from_window_low_pct": f"latest_close({latest.date}) vs window_low({low_record.date})",
            "volume_vs_5d_avg_pct": f"latest_volume({latest.date}) vs avg_volume from {recent_5[0].date} to {recent_5[-1].date}",
            "volume_vs_20d_avg_pct": f"latest_volume({latest.date}) vs avg_volume from {recent_20[0].date} to {recent_20[-1].date}",
        },
        "notes": "max_drawdown_pct uses intrawindow running high to later low.",
    }
    return format_payload_values("technical.price_volume_summary", payload)


class TechnicalSource:
    """
    Builds context for Technical Analyst.
    Fetches:
    - Recent K-line data (Open, High, Low, Close, Volume)
    - Pre-calculated technical indicators (MA, MACD, RSI, BOLL)
    - Real-time market data
    """

    async def _get_recent_klines(self, db: AsyncSession, stock_code: str, days: int = 5) -> List[Dict[str, Any]]:
        result = await db.execute(
            select(KlineData)
            .where(KlineData.stock_code == stock_code, KlineData.freq == 'D')
            .order_by(desc(KlineData.date))
            .limit(days)
        )
        klines = list(result.scalars().all())

        # Return in chronological order (oldest to newest)
        klines.reverse()

        payload = [
            {
                "date": str(k.date),
                "open": k.open,
                "high": k.high,
                "low": k.low,
                "close": k.close,
                "volume": k.volume,
                "pct_chg": k.change_percent,
            }
            for k in klines
        ]
        return format_payload_values("technical.kline", payload)

    async def _get_price_volume_summary(self, db: AsyncSession, stock_code: str, days: int = 60) -> Dict[str, Any]:
        """从日 K 线和最新 ATR 确定性计算价格量能摘要。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            days: 参与计算的最近日 K 记录数。

        Returns:
            带单位的价格区间、回撤、量能对比和 ATR 边界摘要。
        """
        kline_result = await db.execute(
            select(KlineData)
            .where(KlineData.stock_code == stock_code, KlineData.freq == 'D')
            .order_by(desc(KlineData.date))
            .limit(days)
        )
        klines = list(kline_result.scalars().all())
        indicator_result = await db.execute(
            select(StockIndicators)
            .where(StockIndicators.stock_code == stock_code)
            .order_by(desc(StockIndicators.trade_date))
        )
        return _build_price_volume_summary_payload(
            klines,
            indicator_result.scalars().first(),
        )

    async def _get_latest_indicators(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        result = await db.execute(
            select(StockIndicators)
            .where(StockIndicators.stock_code == stock_code)
            .order_by(desc(StockIndicators.trade_date))
        )
        ind = result.scalars().first()

        if not ind:
            return {}

        payload = {
            "date": str(ind.trade_date),
            "ma": {
                "ma5": ind.ma5,
                "ma10": ind.ma10,
                "ma20": ind.ma20,
                "ma30": ind.ma30,
                "ma60": ind.ma60,
                "ma120": ind.ma120,
                "ma250": ind.ma250,
            },
            "macd": {
                "dif": ind.macd, "dea": ind.macd_signal, "hist": ind.macd_hist
            },
            "rsi": {
                "rsi_6": ind.rsi_6, "rsi_12": ind.rsi_12, "rsi_24": ind.rsi_24
            },
            "kdj": {
                "k": ind.kdj_k, "d": ind.kdj_d, "j": ind.kdj_j
            },
            "boll": {
                "upper": ind.boll_upper,
                "mid": ind.boll_mid,
                "lower": ind.boll_lower,
            },
            "other": {
                "cci": ind.cci,
                "wr_14": ind.wr_14,
                "atr": ind.atr,
                "obv": ind.obv,
            }
        }
        return format_payload_values("technical.indicators", payload)

    async def _get_realtime_market(self, db: AsyncSession, stock_code: str) -> Dict[str, Any]:
        result = await db.execute(
            select(StockRealtimeMarket)
            .where(StockRealtimeMarket.stock_code == stock_code)
            .order_by(
                desc(StockRealtimeMarket.timestamp),
                desc(StockRealtimeMarket.updated_at),
                desc(StockRealtimeMarket.created_at),
            )
        )
        realtime = result.scalars().first()

        if not realtime:
            return {}

        payload = {
            "price": realtime.current_price,
            "pct_chg": realtime.change_percent,
            "turnover_rate": realtime.turnover_rate,
            "volume_ratio": realtime.volume_ratio,
            "amplitude": realtime.amplitude,
            "pb": realtime.pb_ratio,
            "pe": realtime.pe_dynamic,
            "amount": realtime.turnover,
            "volume": realtime.volume,
            "turnover": realtime.turnover,
            "total_market_cap": realtime.total_market_cap,
            "circulating_market_cap": realtime.circulating_market_cap,
            "timestamp": realtime.timestamp.isoformat() if realtime.timestamp else None,
        }
        return format_payload_values("technical.realtime_market", payload)

    async def _get_index_context(self, db: AsyncSession, index_code: str = "sh000001") -> Dict[str, Any]:
        """
        获取大盘指数数据作为参考
        Get market index data for reference (default: Shanghai Composite)
        """
        result = await db.execute(
            select(IndexDaily)
            .where(IndexDaily.index_code == index_code)
            .order_by(desc(IndexDaily.trade_date))
        )
        index = result.scalars().first()

        if not index:
            return {}

        payload = {
            "index_code": index.index_code,
            "date": str(index.trade_date),
            "close": index.close,
            "pct_chg": index.pct_chg,
            "volume": index.volume,
            "amount": index.amount,
        }
        return format_payload_values("technical.index_context", payload)
