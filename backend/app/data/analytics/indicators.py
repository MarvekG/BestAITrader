from typing import List, Optional
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert
import ta
from datetime import datetime, date

from app.core.logger import get_logger
from app.models.stock_indicators import StockIndicators
from app.models.data_storage import KlineData
from app.core.utils.formatters import StockCodeStandardizer

logger = get_logger(__name__)

class IndicatorService:
    """Service for calculating and storing stock technical indicators."""

    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators for a given DataFrame of Kline data.
        df must have columns: ['open', 'high', 'low', 'close', 'volume', 'date']
        """
        if df.empty or len(df) < 2:
            return df

        # Sort by date just in case
        df = df.sort_values('date').reset_index(drop=True)

        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']

        # --- Trend ---
        # MA
        for window in [5, 10, 20, 30, 60, 120, 250]:
            df[f'ma{window}'] = ta.trend.sma_indicator(close, window=window)

        # --- Momentum ---
        # MACD (12, 26, 9)
        macd = ta.trend.MACD(close)
        df['macd'] = macd.macd()
        df['macd_signal'] = macd.macd_signal()
        df['macd_hist'] = macd.macd_diff()

        # RSI (6, 12, 24)
        df['rsi_6'] = ta.momentum.rsi(close, window=6)
        df['rsi_12'] = ta.momentum.rsi(close, window=12)
        df['rsi_24'] = ta.momentum.rsi(close, window=24)

        # KDJ (9, 3, 3) - Custom implementation for A-share standard
        # RSV = (Close - LowestLow_9) / (HighestHigh_9 - LowestLow_9) * 100
        # K = 2/3 * PrevK + 1/3 * RSV
        # D = 2/3 * PrevD + 1/3 * K
        # J = 3 * K - 2 * D
        low_min = low.rolling(window=9).min()
        high_max = high.rolling(window=9).max()
        rsv = (close - low_min) / (high_max - low_min) * 100
        
        # Initialize K, D with 50
        df['kdj_k'] = 50.0
        df['kdj_d'] = 50.0
        
        # Iterative calculation for K and D to match EMA smoothing logic
        # Note: pandas limit_direction and various smoothing methods might differ slightly, 
        # explicit loop is safer for exact replication of traditional EMA logic used in KDJ.
        k_values = [50.0]
        d_values = [50.0]
        rsv_values = rsv.fillna(50.0).values # Fill NaNs for start
        
        for i in range(1, len(rsv)):
            # If current rsv is NaN (e.g. not enough data), use 50 or prev
            curr_rsv = rsv_values[i]
            if np.isnan(curr_rsv):
                 k = k_values[-1]
                 d = d_values[-1]
            else:
                k = (2/3) * k_values[-1] + (1/3) * curr_rsv
                d = (2/3) * d_values[-1] + (1/3) * k
            k_values.append(k)
            d_values.append(d)
        
        # Assign back (overwrite the initial column)
        # Note: rsv has first 8 as NaN, our loop starts from index 1 (second element), 
        # but we need to verify alignment.
        # Let's use a simpler vectorized approach if 'rsv' has NaNs properly handled.
        # Actually, pure pandas ewm is better:
        # K = RSV.ewm(com=2, adjust=False).mean() ? No, KDJ is SMA-like smoothing but specific coefficients.
        
        # Correct approach using pandas ewm for performance? 
        # K = 1/3 RSV + 2/3 K_prev => K = alpha * RSV + (1-alpha) * K_prev, alpha=1/3.
        # This is strictly ewm with alpha=1/3, adjust=False.
        
        # However, to be perfectly safe with initial 50 value, let's stick to the vectorized EWM:
        # We need to fillna(50) for the very beginning or valid start.
        
        # Let's try the Pandas EWM approach which is faster
        df['rsv'] = rsv
        # Treat first valid RSV as the seed if we want standard behavior, or usually 50.
        # A-share KDJ:
        df['kdj_k'] = df['rsv'].ewm(alpha=1/3, adjust=False).mean()
        df['kdj_d'] = df['kdj_k'].ewm(alpha=1/3, adjust=False).mean()
        df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']
        
        # CCI (14)
        df['cci'] = ta.trend.cci(high, low, close, window=14)

        # Williams %R (14)
        df['wr_14'] = ta.momentum.williams_r(high, low, close, lbp=14)

        # --- Volatility ---
        # BOLL (20, 2)
        boll = ta.volatility.BollingerBands(close, window=20, window_dev=2)
        df['boll_upper'] = boll.bollinger_hband()
        df['boll_mid'] = boll.bollinger_mavg()
        df['boll_lower'] = boll.bollinger_lband()

        # ATR (14)
        try:
            df['atr'] = ta.volatility.average_true_range(high, low, close, window=14)
        except Exception:
            df['atr'] = None

        # --- Volume ---
        # OBV
        try:
            df['obv'] = ta.volume.on_balance_volume(close, volume)
        except Exception:
            df['obv'] = None
        
        # Clean up
        df = df.replace([np.inf, -np.inf], None)
        df = df.where(pd.notnull(df), None) # Replace NaN with None for SQL

        return df

    @staticmethod
    def save_indicators(db: Session, stock_code: str, df: pd.DataFrame):
        """
        Batch save calculated indicators to database.
        Updates existing records if conflict.
        """
        if df.empty:
            return

        formatted_code = StockCodeStandardizer.standardize(stock_code)
        df = df.replace([np.inf, -np.inf], np.nan).astype(object).where(pd.notnull(df), None)
        
        records = []
        for _, row in df.iterrows():
            if not row['date']:
                continue
                
            record = {
                "stock_code": formatted_code,
                "trade_date": row['date'],
                "ma5": row.get('ma5'),
                "ma10": row.get('ma10'),
                "ma20": row.get('ma20'),
                "ma30": row.get('ma30'),
                "ma60": row.get('ma60'),
                "ma120": row.get('ma120'),
                "ma250": row.get('ma250'),
                "macd": row.get('macd'),
                "macd_signal": row.get('macd_signal'),
                "macd_hist": row.get('macd_hist'),
                "kdj_k": row.get('kdj_k'),
                "kdj_d": row.get('kdj_d'),
                "kdj_j": row.get('kdj_j'),
                "rsi_6": row.get('rsi_6'),
                "rsi_12": row.get('rsi_12'),
                "rsi_24": row.get('rsi_24'),
                "cci": row.get('cci'),
                "wr_14": row.get('wr_14'),
                "boll_upper": row.get('boll_upper'),
                "boll_mid": row.get('boll_mid'),
                "boll_lower": row.get('boll_lower'),
                "atr": row.get('atr'),
                "obv": row.get('obv'),
                "updated_at": datetime.now()
            }
            records.append(record)

        if not records:
            return

        # Bulk upsert logic
        stmt = insert(StockIndicators).values(records)
        stmt = stmt.on_conflict_do_update(
            index_elements=['stock_code', 'trade_date'],
            set_={
                c.key: c for c in stmt.excluded if c.key not in ['id', 'stock_code', 'trade_date', 'created_at']
            }
        )
        try:
            db.execute(stmt)
            db.commit()
            logger.info(f"Saved {len(records)} indicator records for {stock_code}")
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to save indicators for {stock_code}: {e}")
            raise e

    @classmethod
    def process_stock(cls, db: Session, stock_code: str, force_update: bool = False):
        """
        基于日线行情重新计算并保存单只股票的技术指标。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            force_update: 是否强制更新，当前保留参数未使用。
        """
        # 1. Fetch Kline Data
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        # Note: We need a large enough window to calculate indicators correctly (e.g. MA250 requires 250 days)
        # For simplicity, we fetch all, or we could fetch last year + buffer.
        # Fetching strictly from DB.
        
        query = db.query(KlineData).filter(
            KlineData.stock_code == formatted_code,
            KlineData.freq == "D"
        ).order_by(KlineData.date.asc())
        
        kline_records = query.all()
        if not kline_records:
            logger.warning(f"No kline data found for {stock_code}")
            return
            
        data = [{
            'date': k.date,
            'open': k.open,
            'high': k.high,
            'low': k.low,
            'close': k.close,
            'volume': k.volume
        } for k in kline_records]
        
        df = pd.DataFrame(data)
        
        # 2. Calculate
        df_ind = cls.calculate_indicators(df)
        
        # 3. Save
        # Optimization: Only save rows that don't exist or need update?
        # For now, simplistic bulk upsert for all (or last N days if we optimized fetch)
        cls.save_indicators(db, formatted_code, df_ind)

indicator_service = IndicatorService()
