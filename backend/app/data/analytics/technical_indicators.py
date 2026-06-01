from typing import Dict, Any, List
import pandas as pd
import numpy as np

def calculate_technical_indicators(kline_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calculate technical indicators from K-line data
    
    Args:
        kline_data: Dict containing 'data' list of kline records
        
    Returns:
        Dict of calculated indicators
    """
    if not kline_data or "data" not in kline_data or not kline_data["data"]:
        return {}
        
    try:
        df = pd.DataFrame(kline_data["data"])
        if df.empty:
            return {}
            
        # Ensure numeric types
        for col in ['close', 'open', 'high', 'low', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Sort by date ascending
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.sort_values('date')
            
        # Calculate MA (Moving Average)
        indicators = {}
        close = df['close']
        
        indicators['ma5'] = close.rolling(window=5).mean().iloc[-1]
        indicators['ma10'] = close.rolling(window=10).mean().iloc[-1]
        indicators['ma20'] = close.rolling(window=20).mean().iloc[-1]
        indicators['ma30'] = close.rolling(window=30).mean().iloc[-1]
        indicators['ma60'] = close.rolling(window=60).mean().iloc[-1]
        
        # Calculate MACD
        # EMA12
        ema12 = close.ewm(span=12, adjust=False).mean()
        # EMA26
        ema26 = close.ewm(span=26, adjust=False).mean()
        # DIF
        dif = ema12 - ema26
        # DEA
        dea = dif.ewm(span=9, adjust=False).mean()
        # MACD
        macd = (dif - dea) * 2
        
        indicators['macd'] = macd.iloc[-1]
        indicators['dif'] = dif.iloc[-1]
        indicators['dea'] = dea.iloc[-1]
        
        # Calculate KDJ (Simple version)
        low_list = df['low'].rolling(window=9, min_periods=9).min()
        high_list = df['high'].rolling(window=9, min_periods=9).max()
        rsv = (close - low_list) / (high_list - low_list) * 100
        
        # Using simple moving average for K, D as approximation or recursion
        # Standard KDJ uses recursive EMA
        k = 50
        d = 50
        k_list = []
        d_list = []
        
        for i in range(len(rsv)):
            if np.isnan(rsv.iloc[i]):
                k_list.append(np.nan)
                d_list.append(np.nan)
                continue
            
            k = (2/3) * k + (1/3) * rsv.iloc[i]
            d = (2/3) * d + (1/3) * k
            k_list.append(k)
            d_list.append(d)
        
        indicators['k'] = k_list[-1]
        indicators['d'] = d_list[-1]
        indicators['j'] = 3 * k_list[-1] - 2 * d_list[-1]
        
        # RTP handles NaN with 0 or None? 
        # Convert to float/None for JSON serialization
        return {k: (float(v) if not pd.isna(v) else None) for k, v in indicators.items()}
        
    except Exception as e:
        # logging?
        return {}
