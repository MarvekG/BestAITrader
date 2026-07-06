from typing import Dict, Any, List
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import KlineData, StockRealtimeMarket, IndexDaily
from app.models.stock_indicators import StockIndicators


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
