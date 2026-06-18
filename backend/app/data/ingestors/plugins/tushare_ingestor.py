import tushare as ts
import asyncio
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional, Any
from app.data.ingestion.service import DataIngestionService
from app.core.config import settings
from app.data.ingestors.base_ingestor import BaseIngestor
from app.data.ingestors.plugins.column_mapping import ColumnMapper
from app.data.ingestors.rate_limiter import LeakyBucketRateLimiter
from app.data.metadata.field_units import format_payload_values
from app.core.utils.formatters import StockCodeStandardizer
from app.core.utils.date_utils import normalize_compact_date
from app.core.logger import get_logger

logger = get_logger(__name__)


class TushareIngestor(BaseIngestor):
    source_name = "tushare"
    display_name = "Tushare"
    required_settings = ("TUSHARE_TOKEN",)

    # 类级别的限流器实例（所有 Tushare 实例共享）
    _shared_rate_limiter: Optional[LeakyBucketRateLimiter] = None

    def __init__(self):
        self.ingestion_service = DataIngestionService()
        self.source = self.get_source_name()
        self.pro = self.get_pro_client() if settings.TUSHARE_TOKEN else None
        self._stock_info_cache = {}  # Cache for shares and financial data

        # 初始化限流器（单例模式）
        if TushareIngestor._shared_rate_limiter is None:
            TushareIngestor._shared_rate_limiter = LeakyBucketRateLimiter(
                max_calls_per_minute=settings.TUSHARE_MAX_CALLS_PER_MINUTE
            )

        # 实例级别的限流器引用（Tushare 独立使用）
        self.rate_limiter = TushareIngestor._shared_rate_limiter
        logger.info(
            "TushareIngestor initialized",
            extra={
                "config": self.get_tushare_config(),
                "rate_limit": f"{self.rate_limiter.max_calls_per_minute} calls/min",
                "rate_limit_timeout_seconds": settings.DATA_SOURCE_RATE_LIMIT_TIMEOUT_SECONDS,
            }
        )

    async def _run_in_executor(self, func, *args, use_cache: bool = True, cache_ttl: int = 60, **kwargs):
        """
        重写基类方法，在调用 Tushare API 前先获取限流令牌。

        Args:
            func: 阻塞函数。
            *args: 位置参数。
            use_cache: 是否使用 Redis 缓存。
            cache_ttl: 缓存过期时间（秒）。
            **kwargs: 关键字参数。

        Returns:
            函数执行结果。
        """
        # 在调用 API 前先获取 Tushare 限流令牌
        acquired = await self.rate_limiter.acquire(timeout=settings.DATA_SOURCE_RATE_LIMIT_TIMEOUT_SECONDS)
        if not acquired:
            logger.warning(
                "Tushare rate limiter timeout",
                extra={
                    "func": self._get_func_name(func),
                    "timeout_seconds": settings.DATA_SOURCE_RATE_LIMIT_TIMEOUT_SECONDS,
                }
            )
            return False

        # 调用基类方法执行实际 API 请求
        return await super()._run_in_executor(func, *args, use_cache=use_cache, cache_ttl=cache_ttl, **kwargs)

    @staticmethod
    def get_pro_client():
        if settings.TUSHARE_API:
            TushareIngestor.update_tushare_config(api_url=settings.TUSHARE_API)
        if not settings.TUSHARE_TOKEN:
            raise ValueError("Tushare token is not configured")
        return ts.pro_api(settings.TUSHARE_TOKEN)

    def update_token(self, token: str):
        """Update Tushare token at runtime"""
        if token:
            self.pro = ts.pro_api(token)
            settings.TUSHARE_TOKEN = token
            logger.info("Tushare token updated at runtime")

    @staticmethod
    def update_tushare_config(
            token: Optional[str] = None, api_url: Optional[str] = None) -> Dict[str, Any]:
        """
        更新Tushare配置 (Deprecated mechanism, use EnvManager in API layer)
        This method is kept for compatibility but token persistence happens in API layer.
        """
        from tushare.pro.client import DataApi
        result = {}

        # 更新API地址
        if api_url:
            logger.info(f"before updating Tushare config: {DataApi._DataApi__http_url}")
            DataApi._DataApi__http_url = api_url
            logger.info(f"after updating Tushare config: ${DataApi._DataApi__http_url}")
            result["api_url"] = api_url

        # 更新token
        if token:
            result["token"] = token
            # Note: Persistence is now handled by EnvManager in the API endpoint

        return result

    @staticmethod
    def get_tushare_config() -> Dict[str, Any]:
        """获取当前Tushare配置"""
        from tushare.pro.client import DataApi
        return {
            "api_url": getattr(
                DataApi,
                "_DataApi__http_url",
                "http://api.waditu.com/dataapi"),
            "token": f"...{settings.TUSHARE_TOKEN[-3:]}" if settings.TUSHARE_TOKEN else None
        }

    async def fetch_and_ingest_stock_kline(
            self,
            stock_code: str,
            start_date: str,
            end_date: str,
            period: str = "daily",
            adjust: str = "qfq") -> Optional[dict]:
        """
        采集单只股票 K 线行情并写入标准行情表。

        官方文档:
            - daily: https://tushare.pro/document/2?doc_id=27
            - weekly: https://tushare.pro/document/2?doc_id=144
            - monthly: https://tushare.pro/document/2?doc_id=145

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            period: 行情周期，支持 daily、weekly、monthly。
            adjust: 复权参数，当前实现不传递给 Tushare Pro 接口。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}
                
            # Map period to Tushare API and frequency
            period_map = {
                "daily": (self.pro.daily, "D", "daily"),
                "weekly": (self.pro.weekly, "W", "weekly"),
                "monthly": (self.pro.monthly, "M", "monthly")
            }
            if period not in period_map:
                logger.warning(f"Unsupported period {period} for Tushare kline. Using daily.")
                period = "daily"
                
            api_func, freq, api_name = period_map[period]

            # Ensure stock code has suffix (e.g. 000001.SZ) for Tushare API
            ts_code = StockCodeStandardizer.standardize(stock_code)
            params = {'ts_code': ts_code}
            if start_date:
                params['start_date'] = start_date.replace('-', '')
            if end_date:
                params['end_date'] = end_date.replace('-', '')

            df = await self._run_in_executor(api_func, **params)
            if df is None or df.empty:
                return {"success": False, "data": [], "count": 0}

            # Align columns with KlineData model using ColumnMapper
            df = ColumnMapper.map_columns(df, 'data.kline_data', source='tushare')

            # Ensure trade_date is date object
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'].astype(str), format='mixed', errors='coerce').dt.date

            # Standardize fields
            df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)
            df['freq'] = freq
            df['data_source'] = 'tushare'

            # Unit conversion: Tushare amount is in Thousand RMB -> convert to RMB
            if 'turnover' in df.columns:
                df['turnover'] = pd.to_numeric(df['turnover'], errors='coerce') * 1000

            # Round numerical columns to 3 decimal places
            numeric_cols = ['open', 'close', 'high', 'low', 'volume', 'turnover', 'change', 'change_percent']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = df[col].apply(lambda x: round(float(x), 3) if x is not None else x)

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source, target_table='kline_data'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest tushare {period} kline for {stock_code}: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_info(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票基础信息并写入股票基础表。

        官方文档:
            - stock_basic: https://tushare.pro/document/2?doc_id=25

        Args:
            stock_code: Tushare 标准股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}
            api_name = 'stock_basic'
            df = await self._run_in_executor(self.pro.stock_basic, ts_code=stock_code)
            if df is None or df.empty:
                return {"success": False, "data": [], "count": 0}

            df['trade_date'] = pd.Timestamp.now().strftime('%Y%m%d')
            df.rename(columns={'ts_code': 'stock_code'}, inplace=True)
            df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            # Ensure list_date is date object
            if 'list_date' in df.columns:
                df['list_date'] = pd.to_datetime(df['list_date'].astype(str), format='mixed', errors='coerce').dt.date

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source, target_table='stock_basic'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest tushare stock_basic for {stock_code}: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_valuation(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> Optional[dict]:
        """
        采集单只股票每日估值与基础行情指标并写入估值历史表。

        官方文档:
            - daily_basic: https://tushare.pro/document/2?doc_id=32

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认最近一年。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认今天。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}
            api_name = 'daily_basic'
            ts_code = StockCodeStandardizer.standardize(stock_code)

            params = {'ts_code': ts_code}

            if start_date:
                params['start_date'] = start_date.replace('-', '')
            else:
                # Default to last 365 days if no start date provided
                params['start_date'] = (pd.Timestamp.now() - pd.Timedelta(days=365)).strftime('%Y%m%d')

            if end_date:
                params['end_date'] = end_date.replace('-', '')
            else:
                params['end_date'] = pd.Timestamp.now().strftime('%Y%m%d')

            df = await self._run_in_executor(self.pro.daily_basic, **params)

            if df is None or df.empty:
                # Fallback to latest 1 if range returned nothing (though unlikely for a year)
                df = await self._run_in_executor(self.pro.daily_basic, ts_code=ts_code, limit=1)

            if df is None or df.empty:
                return {"success": False, "data": [], "count": 0}

            df = ColumnMapper.map_columns(
                df, 'data.stock_valuation_history', source='tushare'
            )

            # Unit conversion:
            # Tushare total_mv/circ_mv are in 万元 -> convert to 元
            # Tushare total_share/float_share are in 万股 -> convert to 股
            val_unit_cols = ['total_market_value', 'circulating_market_value', 'total_share', 'float_share', 'free_share']
            for col in val_unit_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce') * 10000

            df['stock_code'] = df['stock_code'].apply(
                StockCodeStandardizer.standardize
            )

            # Ensure data_date is date object
            if 'data_date' in df.columns:
                df['data_date'] = pd.to_datetime(df['data_date'].astype(str), format='mixed', errors='coerce').dt.date

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source, target_table='stock_valuation_history'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest tushare daily_basic for {stock_code}: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_financial_indicators(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> Optional[dict]:
        """
        获取单只股票财务指标并返回标准化记录。

        官方文档:
            - fina_indicator: https://tushare.pro/document/2?doc_id=79

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认最近一年。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认今天。

        Returns:
            获取成功返回标准化记录；无数据、配置缺失或异常返回空结果。
        """
        try:
            if not self.pro:
                logger.error("Tushare API client (pro) not initialized")
                return {"success": False, "data": [], "count": 0}

            def _parse_tushare_date(value):
                if value is None or pd.isna(value):
                    return None
                parsed = pd.to_datetime(str(value).strip(), format='%Y%m%d', errors='coerce')
                return None if pd.isna(parsed) else parsed.date()

            code = StockCodeStandardizer.standardize(stock_code)
            logger.info(f"Fetching financial indicators for {code}")
            today = datetime.now().date()
            parsed_start_date = pd.to_datetime(start_date, errors='coerce') if start_date else pd.Timestamp(today - timedelta(days=365))
            parsed_end_date = pd.to_datetime(end_date, errors='coerce') if end_date else pd.Timestamp(today)
            if pd.isna(parsed_start_date):
                logger.error(f"Invalid start_date for financial indicators sync: {start_date}")
                return {"success": False, "data": [], "count": 0}
            if pd.isna(parsed_end_date):
                logger.error(f"Invalid end_date for financial indicators sync: {end_date}")
                return {"success": False, "data": [], "count": 0}

            fina_indicator_params = {'ts_code': code}
            fina_indicator_params['start_date'] = parsed_start_date.strftime('%Y%m%d')

            df = await self._run_in_executor(
                self.pro.fina_indicator,
                cache_ttl=3600,
                **fina_indicator_params,
            )

            if df is None or df.empty:
                logger.warning(f"No financial indicators found for {code}")
                return {"success": False, "data": [], "count": 0}

            logger.debug(
                "tushare fina_indicator raw fields fetched",
                extra={
                    "stock_code": code,
                    "raw_fields": list(df.columns),
                },
            )

            df = ColumnMapper.map_columns(
                df.copy(),
                'data.financial_indicator',
                source='tushare_fina_indicator',
                strict=False,
            )

            records = []

            for _, row in df.iterrows():
                row_dict = row.to_dict()

                ann_date = _parse_tushare_date(row_dict.get('announcement_date'))
                report_date = _parse_tushare_date(row_dict.get('report_date'))

                if not report_date:
                    continue
                if report_date < parsed_start_date.date() or report_date > parsed_end_date.date():
                    continue

                standardized_data = {}
                for k, v in row_dict.items():
                    if k in ['stock_code', 'announcement_date', 'report_date']:
                        continue
                    standardized_data[k] = v if pd.notnull(v) else None
                standardized_data = format_payload_values('data.financial_indicator', standardized_data)
                record = {
                    'stock_code': code,
                    'announcement_date': ann_date,
                    'report_date': report_date,
                    'data': standardized_data,
                    'update_date': today,
                    'data_source': self.source
                }
                records.append(record)

            if not records:
                return {"success": False, "data": [], "count": 0}

            final_df = pd.DataFrame(records)

            if not final_df.empty:
                sort_cols = ['report_date']
                if 'announcement_date' in final_df.columns:
                    sort_cols.append('announcement_date')

                final_df = final_df.sort_values(
                    sort_cols, ascending=[True, False]
                ).drop_duplicates(subset=['report_date', 'announcement_date'], keep='first')

                latest_record = final_df.sort_values(
                    sort_cols, ascending=[False, False]
                ).iloc[0]
                latest_data = latest_record.get('data') or {}
                logger.debug(
                    "tushare fina_indicator latest standardized data",
                    extra={
                        "stock_code": code,
                        "report_date": latest_record.get('report_date'),
                        "announcement_date": latest_record.get('announcement_date'),
                        "data_keys": sorted(latest_data.keys()),
                        "standardized_data": latest_data,
                    },
                )

            return {
                "success": True,
                "data": final_df.to_dict("records"),
                "count": len(final_df),
            }
        except Exception as e:
            logger.error(f"Failed to ingest tushare financial indicators: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_northbound(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票沪深港股通持股明细，并补充日线价格用于派生指标。

        官方文档:
            - hk_hold: https://tushare.pro/document/2?doc_id=188
            - daily: https://tushare.pro/document/2?doc_id=27

        Args:
            stock_code: Tushare 标准股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}
            api_name = 'hk_hold'
            # Fetch base northbound data
            df = await self._run_in_executor(self.pro.hk_hold, ts_code=stock_code)
            if df is None or df.empty:
                return {"success": False, "data": [], "count": 0}

            # Supplement with Kline data to fill missing metrics (price, market value)
            # Detect actual range from northbound data to ensure full coverage
            min_date = df['trade_date'].min()
            max_date = df['trade_date'].max()

            kline_df = await self._run_in_executor(
                self.pro.daily, ts_code=stock_code,
                start_date=min_date, end_date=max_date
            )

            if kline_df is not None and not kline_df.empty:
                # Merge on trade_date
                df = pd.merge(
                    df,
                    kline_df[['trade_date', 'close', 'pct_chg']],
                    on='trade_date',
                    how='left'
                )
                # Fill gaps (e.g. alignment issues)
                df = df.sort_values('trade_date', ascending=True)
                df['close'] = df['close'].ffill()
                df['pct_chg'] = df['pct_chg'].ffill()
                df = df.sort_values('trade_date', ascending=False)

            # Column Mappings
            df = ColumnMapper.map_columns(
                df, 'data.northbound_data', source='tushare', strict=False
            )

            # Ensure date is date object
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'].astype(str), format='mixed', errors='coerce').dt.date

            # 1. Normalize hold_ratio (Tushare returns percentage like 4.38, frontend expects 0.0438)
            if 'hold_ratio' in df.columns:
                df['hold_ratio'] = df['hold_ratio'] / 100.0

            # 2. Derive price and value metrics
            if 'close' in df.columns:
                df['close_price'] = df['close']
            if 'pct_chg' in df.columns:
                df['change_percent'] = df['pct_chg']

            if 'hold_shares' in df.columns and 'close_price' in df.columns:
                df['hold_value'] = df['hold_shares'] * df['close_price']

            # 3. Calculate Daily Changes (Historical Diff)
            # Sort by date ascending to use diff()
            df = df.sort_values('date', ascending=True)

            # net_buy_volume (shares)
            if 'hold_shares' in df.columns:
                df['net_buy_volume'] = df['hold_shares'].diff()

            # metrics dependent on hold_value (CNY)
            if 'hold_value' in df.columns:
                df['hold_value_change'] = df['hold_value'].diff()
                # Compute net_buy_amount based on shares change and current price
                if 'close_price' in df.columns and 'net_buy_volume' in df.columns:
                    df['net_buy_amount'] = (
                        df['net_buy_volume'] * df['close_price']
                    )

            # 5. Fill NaN values from diff() for the first record
            # The first record (earliest date) will have NaN for all diff fields
            # We can either drop it or fill with 0 (assuming no change before first record)
            diff_cols = ['net_buy_volume', 'hold_value_change', 'net_buy_amount']
            for col in diff_cols:
                if col in df.columns:
                    df[col] = df[col].fillna(0)

            # 4. Standardize and Ingest
            df['stock_code'] = df['stock_code'].apply(
                StockCodeStandardizer.standardize
            )

            # Final Cleanup: Sort back to descending
            df = df.sort_values('date', ascending=False)

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source, target_table='northbound_data'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest tushare hk_hold for {stock_code}: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_company_profile(self, stock_code: str) -> Optional[dict]:
        """
        采集上市公司基础资料并写入数据表。

        官方文档:
            - stock_company: https://tushare.pro/document/2?doc_id=112

        Args:
            stock_code: Tushare 标准股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}
            api_name = 'stock_company'
            df = await self._run_in_executor(self.pro.stock_company, ts_code=stock_code)
            if df is None or df.empty:
                return {"success": False, "data": [], "count": 0}

            df.rename(columns={'ts_code': 'stock_code'}, inplace=True)
            df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest company profile {stock_code}: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_all_stock_basic(self) -> Optional[dict]:
        """
        全量采集 A 股基础信息并写入股票基础表。

        官方文档:
            - stock_basic: https://tushare.pro/document/2?doc_id=25

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        import time
        if not self.pro:
            return {"success": False, "data": [], "count": 0}
        api_name = 'stock_basic'

        # Fetch all listing stocks
        # Exchange options: SSE, SZSE, BSE
        dfs = []
        for exchange in ['SSE', 'SZSE', 'BSE']:
            # Add small delay between requests to avoid rate limit
            if len(dfs) > 0:
                time.sleep(0.5)

            sub_df = await self._run_in_executor(
                self.pro.stock_basic,
                exchange=exchange,
                list_status='L',
                fields='ts_code,symbol,name,area,industry,market,list_date'
            )
            if sub_df is not None and not sub_df.empty:
                dfs.append(sub_df)

        if not dfs:
            logger.warning("No data retrieved from Tushare stock_basic")
            return {"success": False, "data": [], "count": 0}

        df = pd.concat(dfs, ignore_index=True)
        if df.empty:
            return {"success": False, "data": [], "count": 0}

        df['trade_date'] = pd.Timestamp.now().strftime('%Y%m%d')
        df = ColumnMapper.map_columns(
            df, 'data.stock_basic', source='tushare'
        )

        df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

        # Handle list_date conversion (Ensure it's a date object to avoid DB mismatch)
        if 'list_date' in df.columns:
            try:
                # Tushare typically returns 'YYYYMMDD' as strings or ints
                # Converting to string first and using format='mixed' ensures robust parsing
                df['list_date'] = pd.to_datetime(df['list_date'].astype(str), format='mixed', errors='coerce').dt.date
            except Exception as e:
                logger.warning(f"Failed to convert list_date in Tushare: {e}")

        await self._run_in_executor(
            self.ingestion_service.write_dataframe,
            api_name, df, source=self.source, target_table='stock_basic'
        )
        logger.info(f"Successfully synced {len(df)} stocks basic info from Tushare")
        # 返回字典格式

        return {

            "success": True,

            "data": df.to_dict("records"),

            "count": len(df)

        }

    async def fetch_and_ingest_index_daily(
        self,
        index_code: str,
        start_date: str,
        end_date: str
    ) -> Optional[dict]:
        """
        采集大盘指数日线行情并写入指数日线表。

        官方文档:
            - index_daily: https://tushare.pro/document/2?doc_id=95

        Args:
            index_code: 指数代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        if not self.pro:
            return {"success": False, "data": [], "count": 0}
        try:
            # Map frontend "none" strings
            if start_date and str(start_date).lower() == "none":
                start_date = None
            if end_date and str(end_date).lower() == "none":
                end_date = None

            # 1. Standardize Code for Tushare Index (000xxx -> .SH, 399xxx -> .SZ)
            ts_code = StockCodeStandardizer.to_standard_index(index_code)

            logger.info(f"Fetching Tushare index daily for {index_code} (as {ts_code})")

            # 2. 转换日期格式：YYYY-MM-DD -> YYYYMMDD（Tushare 要求）
            # Convert date format: YYYY-MM-DD -> YYYYMMDD (required by Tushare)
            if start_date:
                start_date = str(start_date).replace('-', '')
            if end_date:
                end_date = str(end_date).replace('-', '')

            # 3. Call API
            df = await self._run_in_executor(
                self.pro.index_daily, ts_code=ts_code, start_date=start_date, end_date=end_date
            )

            if df is not None and not df.empty:
                # 4. Standardize Columns (ts_code -> index_code)
                df = ColumnMapper.map_columns(df, target_table='data.index_daily', source=self.source)

                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'index_daily', df, source=self.source, target_table='data.index_daily'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            logger.warning(f"No data returned for index {index_code} ({ts_code}) [{start_date}~{end_date}]")
            return {"success": False, "data": [], "count": 0}
        except Exception as e:
            logger.error(f"Failed to ingest index daily for {index_code}: {e}", exc_info=True)
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_realtime_market(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票有效实时行情并写入实时行情表。

        官方文档:
            - get_realtime_quotes: https://tushare.org/trading.html

        Args:
            stock_code: 股票代码。

        Returns:
            采集并写入有效价格行情成功返回 True；无数据、价格无效、配置缺失或异常返回 False。
        """
        try:
            # 1. 准备查询代码 (get_realtime_quotes 接收 6 位数字代码或列表)
            symbol = StockCodeStandardizer.to_number(stock_code)

            logger.info(f"Fetching Tushare realtime market for {symbol}")

            # 2. 调用接口 (同步转异步)
            df = await self._run_in_executor(ts.get_realtime_quotes, symbol)

            if df is None or df.empty:
                logger.warning(f"No realtime data returned from Tushare for {stock_code}")
                return {"success": False, "data": [], "count": 0}

            # [ADDITION] Calculate change percent and amount based on previous close
            # 在A股市场，涨跌幅的有效基准是“昨收 (pre_close)”，而非“今开 (open)”
            for col in ['price', 'pre_close']:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            if 'price' in df.columns and 'pre_close' in df.columns:
                df['change_amount'] = df['price'] - df['pre_close']
                # Calculate percentage, avoid division by zero
                df['change_percent'] = df.apply(
                    lambda row: (
                        row['change_amount'] /
                        row['pre_close'] *
                        100) if pd.notna(
                        row.get('pre_close')) and row.get('pre_close') != 0 else 0.0,
                    axis=1)
            else:
                df['change_amount'] = 0.0
                df['change_percent'] = 0.0

            # 3. 字段映射 (使用已配置的 data.stock_realtime_market 映射)
            mapping_key = 'data.stock_realtime_market'
            df = ColumnMapper.map_columns(df, mapping_key, source=self.source, strict=True)

            # 4. 标准化代码与增加元数据
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            if 'current_price' in df.columns:
                df['current_price'] = pd.to_numeric(df['current_price'], errors='coerce')
                df = df[df['current_price'] > 0].copy()
                if df.empty:
                    return {"success": False, "data": [], "count": 0}

            df['data_source'] = self.source
            df['timestamp'] = pd.Timestamp.now()

            # 5. 持久化入库
            success = await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'tushare_realtime', df, source=self.source,
                target_table='data.stock_realtime_market'
            )

            if success:
                logger.info(f"Successfully ingested Tushare realtime market for {stock_code}")
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            return {"success": False, "data": [], "count": 0}

        except Exception as e:
            logger.error(f"Failed to ingest Tushare realtime market for {stock_code}: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_dragon_tiger(self, start_date: str, end_date: str = None) -> Optional[dict]:
        """
        按日期范围采集龙虎榜每日统计数据并写入龙虎榜表。

        官方文档:
            - top_list: https://tushare.pro/document/2?doc_id=106

        Args:
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时只采集开始日期。

        Returns:
            任一交易日采集并写入成功返回 True；全部无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                logger.error("Tushare API client (pro) not initialized")
                return {"success": False, "data": [], "count": 0}

            api_name = 'top_list'

            # Standardizing date range
            try:
                start_dt = datetime.strptime(start_date.replace('-', ''), '%Y%m%d')
                if end_date:
                    end_dt = datetime.strptime(end_date.replace('-', ''), '%Y%m%d')
                else:
                    end_dt = start_dt
            except Exception as e:
                logger.error(f"Invalid date format: {start_date} / {end_date}: {e}")
                return {"success": False, "data": [], "count": 0}

            current_dt = start_dt
            success_count = 0
            total_days = (end_dt - start_dt).days + 1

            logger.info(f"Syncing Dragon Tiger for {total_days} days ({start_date} to {end_date or start_date})")

            while current_dt <= end_dt:
                date_str = current_dt.strftime('%Y%m%d')
                logger.info(f"Fetching Dragon Tiger data for {date_str} via Tushare")

                try:
                    df = await self._run_in_executor(self.pro.top_list, trade_date=date_str)

                    if df is not None and not df.empty:
                        # Map columns
                        df = ColumnMapper.map_columns(df, 'data.dragon_tiger_data', source='tushare')

                        # Convert trade_date if it exists
                        if 'trade_date' in df.columns:
                            df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d').dt.date

                        # Standardize stock code
                        df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                        # Set data source
                        df['data_source'] = self.source

                        # Write to database
                        await self._run_in_executor(
                            self.ingestion_service.write_dataframe,
                            api_name, df, source=self.source, target_table='dragon_tiger_data'
                        )
                        success_count += 1
                    else:
                        logger.info(f"No dragon tiger data for {date_str}")
                except Exception as e:
                    logger.error(f"Failed to fetch Dragon Tiger data for {date_str}: {e}")

                current_dt += pd.Timedelta(days=1)

                # Small delay to avoid rate limits if syncing multi days
                if total_days > 1:
                    await asyncio.sleep(0.5)

            return success_count > 0
        except Exception as e:
            logger.error(f"Failed to ingest tushare dragon tiger: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_board_industry(self) -> Optional[dict]:
        """
        采集外部行业板块数据，并合并行情数据补充价格指标。

        官方文档:
            - dc_index: https://tushare.pro/document/2?doc_id=362
            - dc_daily: https://tushare.pro/document/2?doc_id=382

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            # 1. 获取最新交易日数据
            trade_date = datetime.now().strftime('%Y%m%d')

            logger.info(f"Fetching industry data from Tushare dc_index/dc_daily for date: {trade_date}")
            df_index = await self._run_in_executor(
                self.pro.dc_index, trade_date=trade_date
            )
            df_daily = await self._run_in_executor(
                self.pro.dc_daily, trade_date=trade_date
            )

            # 如果今天没数据 (开市前或非交易日)，尝试回溯
            if (df_index is None or df_index.empty) or (df_daily is None or df_daily.empty):
                logger.info("No data for today, trying to find latest available industry data...")
                # 简单逻辑：取最近 7 天
                for days in range(1, 8):
                    target_date = (datetime.now() - pd.Timedelta(days=days)).strftime('%Y%m%d')
                    df_index = await self._run_in_executor(self.pro.dc_index, trade_date=target_date)
                    df_daily = await self._run_in_executor(self.pro.dc_daily, trade_date=target_date)
                    if df_index is not None and not df_index.empty and df_daily is not None and not df_daily.empty:
                        logger.info(f"Found industry data for {target_date}")
                        break

            if df_index is None or df_index.empty:
                logger.warning("No industry data found in recent 7 days via Tushare dc_index")
                return {"success": False, "data": [], "count": 0}

            # 2. 合并数据 (dc_index 缺少价格和涨跌额，dc_daily 有价格但缺少领涨和家数统计)
            if df_daily is not None and not df_daily.empty:
                # 只保留核心价格字段以防冲突
                df_daily_lite = df_daily[['ts_code', 'close', 'change', 'amount', 'swing']].copy()
                df = pd.merge(df_index, df_daily_lite, on='ts_code', how='left')
            else:
                df = df_index

            # 3. 计算排名 (按涨跌幅降序)
            if 'pct_change' in df.columns:
                df = df.sort_values(by='pct_change', ascending=False)
                df['rank'] = range(1, len(df) + 1)
            else:
                df['rank'] = 0

            # 4. 列名映射
            df = ColumnMapper.map_columns(
                df, 'data.industry_data', source='tushare_industry'
            )

            # 5. 补充字段
            df['timestamp'] = pd.Timestamp.now()
            df['data_source'] = self.source

            # Note: IndustryData uses board_code as unique identifier logic
            if 'board_code' in df.columns:
                df['stock_code'] = df['board_code']

            # 5. 写入数据库
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'industry_sync', df, source=self.source, force_sync=True,
                target_table='industry_data'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest industry data (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_money_flow(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票资金流向并写入个股资金流表。

        官方文档:
            - moneyflow: https://tushare.pro/document/2?doc_id=170

        Args:
            stock_code: Tushare 标准股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            # 获取最近 30 天数据
            df = await self._run_in_executor(
                self.pro.moneyflow, ts_code=stock_code,
                start_date=(datetime.now() - pd.Timedelta(days=30)).strftime('%Y%m%d')
            )

            if df is not None and not df.empty:
                # 计算各档位净流入金额 (Tushare 返回的是 buy_* 和 sell_*)
                df['net_inflow_small'] = (df['buy_sm_amount'] - df['sell_sm_amount']) * 10000
                df['net_inflow_medium'] = (df['buy_md_amount'] - df['sell_md_amount']) * 10000
                df['net_inflow_large'] = (df['buy_lg_amount'] - df['sell_lg_amount']) * 10000
                df['net_inflow_huge'] = (df['buy_elg_amount'] - df['sell_elg_amount']) * 10000
                df['net_inflow_main'] = df['net_inflow_large'] + df['net_inflow_huge']

                # 计算占比 (如果 Tushare 没有直接提供)
                total_amount = (df['buy_sm_amount'] + df['sell_sm_amount'] +
                                df['buy_md_amount'] + df['sell_md_amount'] +
                                df['buy_lg_amount'] + df['sell_lg_amount'] +
                                df['buy_elg_amount'] + df['sell_elg_amount']) * 10000

                total_amount = total_amount.replace(0, pd.NA)
                df['net_inflow_ratio_small'] = (df['net_inflow_small'] / total_amount * 100).fillna(0)
                df['net_inflow_ratio_medium'] = (df['net_inflow_medium'] / total_amount * 100).fillna(0)
                df['net_inflow_ratio_large'] = (df['net_inflow_large'] / total_amount * 100).fillna(0)
                df['net_inflow_ratio_huge'] = (df['net_inflow_huge'] / total_amount * 100).fillna(0)
                df['net_inflow_ratio_main'] = (df['net_inflow_main'] / total_amount * 100).fillna(0)

                # Calculate moving sums for net_inflow_main (3d, 5d, 10d)
                # Tushare usually returns data sorted by date descending, ensure ascending for rolling
                df = df.sort_values('trade_date', ascending=True)
                df['net_inflow_main_3d'] = df['net_inflow_main'].rolling(window=3).sum()
                df['net_inflow_main_5d'] = df['net_inflow_main'].rolling(window=5).sum()
                df['net_inflow_main_10d'] = df['net_inflow_main'].rolling(window=10).sum()

                # 列名映射 (使用 global_mappings 中的 ts_code/trade_date)
                df = ColumnMapper.map_columns(df, 'data.stock_money_flow', source=self.source)

                if 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(
                        df['trade_date'].astype(str), format='mixed', errors='coerce'
                    ).dt.date

                # 补充字段
                df['data_source'] = self.source
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                # 写入数据库
                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'moneyflow', df, source=self.source, target_table='stock_money_flow'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            return {"success": False, "data": [], "count": 0}
        except Exception as e:
            logger.error(f"Failed to ingest money flow for {stock_code} (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_shareholder_count(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票股东人数变动，并补充每日指标用于派生持股数据。

        官方文档:
            - stk_holdernumber: https://tushare.pro/document/2?doc_id=166
            - daily_basic: https://tushare.pro/document/2?doc_id=32

        Args:
            stock_code: Tushare 标准股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            df = await self._run_in_executor(
                self.pro.stk_holdernumber, ts_code=stock_code
            )

            if df is not None and not df.empty:
                # 1. 映射列名
                df = ColumnMapper.map_columns(df, 'data.stock_shareholder_count', source=self.source)
                df['data_source'] = self.source
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                # Date conversion
                if 'end_date' in df.columns:
                    df['end_date'] = pd.to_datetime(df['end_date'].astype(str), errors='coerce').dt.date
                if 'ann_date' in df.columns:
                    df['ann_date'] = pd.to_datetime(df['ann_date'].astype(str), errors='coerce').dt.date

                # 2. 预处理日期并排序 (用于计算环比和去重)
                # Ensure they are datetime for sorting temporarily
                df['tmp_end_date'] = pd.to_datetime(df['end_date'])
                if 'ann_date' in df.columns:
                    df['tmp_ann_date'] = pd.to_datetime(df['ann_date'])
                    # 同一 end_date 按 ann_date 升序排列，以便用 last 保留最新公告
                    df = df.sort_values(by=['tmp_end_date', 'tmp_ann_date'], ascending=True)
                else:
                    df = df.sort_values(by='tmp_end_date', ascending=True)

                # 按业务主键 (stock_code, end_date) 去重，保留最后一行 (即最新的公告)
                df = df.drop_duplicates(subset=['stock_code', 'end_date'], keep='last')

                # 3. 计算环比指标 (股东户数)
                # 确保是按 end_date 升序排列的
                df = df.sort_values(by='end_date', ascending=True)
                df['holder_count_prev'] = df['holder_count'].shift(1)
                df['holder_count_change'] = df['holder_count'] - df['holder_count_prev']

                # 避免除以 0 的情况
                prev_count_safe = df['holder_count_prev'].replace(0, pd.NA)
                df['holder_count_change_ratio'] = (df['holder_count_change'] / prev_count_safe) * 100

                # Clean up temporary columns
                if 'tmp_end_date' in df.columns:
                    df = df.drop(columns=['tmp_end_date'])
                if 'tmp_ann_date' in df.columns:
                    df = df.drop(columns=['tmp_ann_date'])

                # 4. 尝试补充基本面数据 (总股本, 总市值)
                try:
                    # 获取该股票的所有每日指标 (total_share/total_mv)
                    # 由于股东户数可能跨度很大，不设限制地获取该股历史
                    df_basic = await self._run_in_executor(
                        self.pro.daily_basic, ts_code=stock_code,
                        fields='ts_code,trade_date,total_share,total_mv,close'
                    )
                    if df_basic is not None and not df_basic.empty:
                        df_basic['trade_date'] = pd.to_datetime(df_basic['trade_date'])
                        df_basic = df_basic.sort_values('trade_date')

                        # 标准化 ts_code 以对齐 df
                        df_basic['stock_code'] = df_basic['ts_code'].apply(StockCodeStandardizer.standardize)

                        # 确保日期类型兼容 (merge_asof 要求两边 key 的类型一致)
                        # Ensure date types are compatible for merge_asof
                        df['tmp_merge_date'] = pd.to_datetime(df['end_date'])
                        df_basic['trade_date'] = pd.to_datetime(df_basic['trade_date'])

                        # Ensure both are sorted by merge key
                        df = df.sort_values('tmp_merge_date')
                        df_basic = df_basic.sort_values('trade_date')

                        # 使用 merge_asof 查找最接近 end_date 的基本面数据 (<= end_date)
                        # Find fundamental data closest to end_date
                        df = pd.merge_asof(
                            df,
                            df_basic[['stock_code', 'trade_date', 'total_share', 'total_mv', 'close']],
                            left_on='tmp_merge_date',
                            right_on='trade_date',
                            by='stock_code',
                            direction='backward'
                        )
                        # 清理临时排序列
                        if 'tmp_merge_date' in df.columns:
                            df.drop(columns=['tmp_merge_date'], inplace=True)
                except Exception as e:
                    logger.warning(f"Failed to fetch daily_basic for {stock_code} to complement shareholder data: {e}")

                # 5. 计算派生指标
                if 'total_share' in df.columns:
                    # Tushare total_share 是万股 -> 换算成 股
                    # Note: daily_basic 中的 total_share 是总股本，不是流通股本
                    df['total_share'] = pd.to_numeric(df['total_share'], errors='coerce') * 10000
                    df['avg_hold_shares'] = df['total_share'] / df['holder_count'].replace(0, pd.NA)

                if 'close' in df.columns:
                    df.rename(columns={'close': 'price_at_end'}, inplace=True)
                    # 计算区间涨跌幅 (基于 end_date 排序后的前后变动)
                    # 确保 df 仍按 end_date 升序
                    df = df.sort_values(by='end_date', ascending=True)
                    df['price_at_end_prev'] = df['price_at_end'].shift(1)
                    # 涨跌幅 = (今收 - 上收) / 上收 * 100
                    # 注意：这是两个报告期之间的涨跌幅，近似于"区间涨跌幅"
                    df['price_change_ratio'] = (df['price_at_end'] - df['price_at_end_prev']
                                                ) / df['price_at_end_prev'] * 100

                if 'total_mv' in df.columns:
                    # Tushare total_mv 是万元 -> 换算成 元
                    df['total_mv'] = pd.to_numeric(df['total_mv'], errors='coerce') * 10000
                    df['avg_hold_value'] = df['total_mv'] / df['holder_count'].replace(0, pd.NA)

                # 清理临时列
                if 'trade_date' in df.columns:
                    df.drop(columns=['trade_date'], inplace=True)

                # 将 NaN 转为 None 以便 SQLAlchemy 处理 (显式转为 object 避免类型不兼容)
                df = df.astype(object).where(pd.notnull(df), None)

                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'shareholder_count', df, source=self.source, target_table='stock_shareholder_count'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            return {"success": False, "data": [], "count": 0}
        except Exception as e:
            logger.error(f"Failed to ingest shareholder count for {stock_code} (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_pledge_risk(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票股权质押明细数据并写入质押风险表。

        官方文档:
            - pledge_detail: https://tushare.pro/document/2?doc_id=111

        Args:
            stock_code: 股票代码。

        Returns:
            采集并写入成功返回 True；无数据视为成功；配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            ts_code = StockCodeStandardizer.standardize(stock_code)

            df = await self._run_in_executor(
                self.pro.pledge_detail, ts_code=ts_code
            )

            if df is None or df.empty:
                logger.warning(f"No stock pledge data found for {ts_code}")
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }

            df = ColumnMapper.map_columns(df, 'data.stock_pledge_risk', source=self.source)

            # 单位转换：万股 -> 股
            if 'pledge_shares' in df.columns:
                df['pledge_shares'] = pd.to_numeric(df['pledge_shares'], errors='coerce') * 10000

            # 数值转换
            numeric_cols = [
                'pledge_ratio_to_total',
                'pledge_ratio_to_holder',
                'pledge_price',
                'current_price',
                'liquidate_price']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 日期转换
            date_cols = ['pledge_date', 'ann_date', 'release_date']
            for col in date_cols:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

            df['data_source'] = self.source
            df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            # 去重：根据唯一约束 (stock_code, pledgor_name, pledge_date) 去除重复记录
            # Tushare 可能返回同一天同一人的多条重复记录，导致数据库写入时的 ON CONFLICT 报错
            subset_cols = ['stock_code', 'pledgor_name', 'pledge_date']
            # 确保列存在
            existing_subset = [col for col in subset_cols if col in df.columns]
            if existing_subset:
                df.drop_duplicates(subset=existing_subset, inplace=True)

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'pledge_detail', df, source=self.source, target_table='stock_pledge_risk'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest pledge risk for {stock_code} (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_all_pledge_summary(self, stock_code: str = None) -> Optional[dict]:
        """
        采集单只或全市场股权质押汇总数据并写入质押汇总表。

        官方文档:
            - pledge_stat: https://tushare.pro/document/2?doc_id=110

        Args:
            stock_code: 股票代码；为空时采集全市场最新质押汇总。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                logger.error("Tushare API client (pro) not initialized")
                return {"success": False, "data": [], "count": 0}

            api_name = 'pledge_stat'

            # Standardize stock_code if provided
            ts_code = None
            if stock_code:
                ts_code = StockCodeStandardizer.standardize(stock_code)
                logger.info(f"Fetching aggregate pledge summary for {ts_code} from Tushare (pledge_stat)...")
            else:
                logger.info("Fetching aggregate pledge summary for ALL stocks from Tushare (pledge_stat)...")

            # Fetch from Tushare
            # Note: Tushare defaults to latest summary if ts_code and end_date are omitted.
            # Providing end_date (today) might return empty if data is not yet updated for today.
            df = await self._run_in_executor(
                self.pro.pledge_stat, ts_code=ts_code
            )

            if df is None or df.empty:
                logger.warning(f"No pledge summary data returned from Tushare pledge_stat for {ts_code or 'All'}")
                return {"success": False, "data": [], "count": 0}

            logger.info(f"TuShare pledge_stat returned {len(df)} records for {ts_code or 'All'}")

            # 列名映射 (使用 strict 模式)
            df = ColumnMapper.map_columns(
                df, 'data.stock_pledge_summary', source=self.source, strict=True
            )

            # 标准化 stock_code
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            # 日期转换
            today = datetime.now().date()
            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d', errors='coerce').dt.date
                df['trade_date'] = df['trade_date'].fillna(today)
            else:
                df['trade_date'] = today

            # 过滤掉代码为空的记录
            df.dropna(subset=['stock_code'], inplace=True)

            # 数值转换
            numeric_cols = ['pledge_count', 'unrestricted_pledge_shares', 'restricted_pledge_shares',
                            'total_share', 'pledge_ratio']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 设置数据源
            df['data_source'] = self.source

            # 写入数据库
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source, target_table='stock_pledge_summary'
            )

            logger.info(
                f"Successfully ingested {len(df)} records into stock_pledge_summary from TuShare ({ts_code or 'All'})")
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }

        except Exception as e:
            logger.error(f"Failed to ingest pledge summary (Tushare) for {stock_code or 'All'}: {e}")
            return {"success": False, "data": [], "count": 0}


    async def fetch_and_ingest_stock_lockup_release(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票未来限售股解禁数据并写入解禁表。

        官方文档:
            - share_float: https://tushare.pro/document/2?doc_id=160

        Args:
            stock_code: Tushare 标准股票代码。

        Returns:
            采集并写入成功返回 True；无数据视为成功；配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            # Determine date range: [Today, Today + 30 days]
            import pandas as pd
            now = pd.Timestamp.now()
            start_date_str = now.strftime('%Y%m%d')
            end_date_str = (now + pd.Timedelta(days=30)).strftime('%Y%m%d')

            logger.info(f"Fetching lockup release for {stock_code} (Tushare) range: {start_date_str}-{end_date_str}")

            df = await self._run_in_executor(
                self.pro.share_float,
                ts_code=stock_code,
                start_date=start_date_str,
                end_date=end_date_str
            )

            if df is not None and not df.empty:
                df = ColumnMapper.map_columns(df, 'data.stock_lockup_release', source=self.source)
                df['data_source'] = self.source
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                if 'release_date' in df.columns:
                    df['release_date'] = pd.to_datetime(df['release_date'], errors='coerce')
                if 'release_shares' in df.columns:
                    # Tushare share_float.float_share already uses raw shares.
                    df['release_shares'] = pd.to_numeric(df['release_shares'], errors='coerce')

                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'share_float', df, source=self.source, target_table='stock_lockup_release'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            else:
                logger.info(f"No lockup release data found for {stock_code} in range {start_date_str}-{end_date_str}")
                return True  # Not an error, just no data
        except Exception as e:
            logger.error(f"Failed to ingest lockup release for {stock_code} (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_margin_data(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票融资融券明细数据并写入两融表。

        官方文档:
            - margin_detail: https://tushare.pro/document/2?doc_id=59

        Args:
            stock_code: Tushare 标准股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            df = await self._run_in_executor(
                self.pro.margin_detail, ts_code=stock_code
            )

            if df is not None and not df.empty:
                df = ColumnMapper.map_columns(df, 'data.stock_margin_data', source=self.source)
                df['data_source'] = self.source
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                # Date conversion
                if 'trade_date' in df.columns:
                    df['trade_date'] = pd.to_datetime(df['trade_date'].astype(str), errors='coerce').dt.date

                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'margin_detail', df, source=self.source, target_table='stock_margin_data'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            return {"success": False, "data": [], "count": 0}
        except Exception as e:
            logger.error(f"Failed to ingest margin data for {stock_code} (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_limit_up_pool(self, trade_date: str = None) -> Optional[dict]:
        """
        采集每日涨停池数据并写入涨停池表。

        官方文档:
            - limit_list_d: https://tushare.pro/document/2?doc_id=298

        Args:
            trade_date: 交易日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时尝试最近 3 天。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            # 如果没有指定日期，尝试今天。
            target_dates = []
            if trade_date:
                # Normalize to YYYYMMDD
                trade_date = pd.to_datetime(trade_date).strftime('%Y%m%d')
                target_dates = [trade_date]
            else:
                today = datetime.now()
                # 尝试最近3天 (防止周末/节假日为空)
                target_dates = [
                    today.strftime('%Y%m%d'),
                    (today - pd.Timedelta(days=1)).strftime('%Y%m%d'),
                    (today - pd.Timedelta(days=2)).strftime('%Y%m%d')
                ]

            df = None
            found_date = None
            for d in target_dates:
                # limit_type='U' (涨停)
                df = await self._run_in_executor(
                    self.pro.limit_list_d, trade_date=d, limit_type='U'
                )
                if df is not None and not df.empty:
                    found_date = d
                    logger.info(f"Found limit up pool data for {d}")
                    break

            if df is not None and not df.empty:
                # 1. Map Columns
                df = ColumnMapper.map_columns(
                    df, 'data.stock_limit_up_pool', source=self.source, strict=False
                )

                # 2. Add Metadata
                df['data_source'] = self.source
                if found_date:
                    df['update_date'] = pd.to_datetime(found_date).date()
                else:
                    df['update_date'] = pd.Timestamp.now().date()

                if 'stock_code' in df.columns:
                    df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                # 3. Type Conversion
                numeric_cols = [
                    'limit_up_price',
                    'pct_chg',
                    'turnover',
                    'circ_mv',
                    'total_mv',
                    'turnover_rate',
                    'fund_amount']
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'limit_list_d', df, source=self.source, target_table='stock_limit_up_pool'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            return {"success": False, "data": [], "count": 0}

        except Exception as e:
            logger.error(f"Failed to ingest stock_limit_up_pool: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_limit_down_pool(self, trade_date: str = None) -> Optional[dict]:
        """
        采集每日跌停池数据并写入跌停池表。

        官方文档:
            - limit_list_d: https://tushare.pro/document/2?doc_id=298

        Args:
            trade_date: 交易日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时尝试最近 3 天。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            # Wenn kein Datum angegeben ist, versuche heute, gestern, vorgestern
            target_dates = []
            if trade_date:
                # Normalize to YYYYMMDD
                trade_date = pd.to_datetime(trade_date).strftime('%Y%m%d')
                target_dates = [trade_date]
            else:
                today = datetime.now()
                target_dates = [
                    today.strftime('%Y%m%d'),
                    (today - pd.Timedelta(days=1)).strftime('%Y%m%d'),
                    (today - pd.Timedelta(days=2)).strftime('%Y%m%d')
                ]

            df = None
            found_date = None
            for d in target_dates:
                # limit_type='D' (跌停)
                df = await self._run_in_executor(
                    self.pro.limit_list_d, trade_date=d, limit_type='D'
                )
                if df is not None and not df.empty:
                    found_date = d
                    logger.info(f"Found limit down pool data for {d}")
                    break

            if df is not None and not df.empty:
                # 1. Map Columns
                df = ColumnMapper.map_columns(
                    df, 'data.stock_limit_down_pool', source=self.source, strict=False
                )

                # 2. Add Metadata
                df['data_source'] = self.source
                if found_date:
                    df['update_date'] = pd.to_datetime(found_date).date()
                else:
                    df['update_date'] = pd.Timestamp.now().date()

                if 'stock_code' in df.columns:
                    df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                # 3. Type Conversion
                numeric_cols = [
                    'limit_down_price',
                    'pct_chg',
                    'turnover',
                    'circ_mv',
                    'total_mv',
                    'turnover_rate',
                    'fund_amount']
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'limit_list_d', df, source=self.source, target_table='stock_limit_down_pool'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            return {"success": False, "data": [], "count": 0}

        except Exception as e:
            logger.error(f"Failed to ingest stock_limit_down_pool: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_zhaban_pool(self, trade_date: str = None) -> Optional[dict]:
        """
        采集每日炸板池数据并写入炸板池表。

        官方文档:
            - limit_list_d: https://tushare.pro/document/2?doc_id=298

        Args:
            trade_date: 交易日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时尝试最近 3 天。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            # Normalize date
            target_dates = []
            if trade_date:
                # Normalize to YYYYMMDD
                trade_date = pd.to_datetime(trade_date).strftime('%Y%m%d')
                target_dates = [trade_date]
            else:
                today = datetime.now()
                target_dates = [
                    today.strftime('%Y%m%d'),
                    (today - pd.Timedelta(days=1)).strftime('%Y%m%d'),
                    (today - pd.Timedelta(days=2)).strftime('%Y%m%d')
                ]

            df = None
            found_date = None
            for d in target_dates:
                # limit_type='Z' (炸板)
                df = await self._run_in_executor(
                    self.pro.limit_list_d, trade_date=d, limit_type='Z'
                )
                if df is not None and not df.empty:
                    found_date = d
                    logger.info(f"Found zhaban pool data for {d}")
                    break

            if df is not None and not df.empty:
                # 1. Map Columns
                df = ColumnMapper.map_columns(
                    df, 'data.stock_zhaban_pool', source=self.source, strict=False
                )

                # 2. Add Metadata
                df['data_source'] = self.source
                if found_date:
                    df['update_date'] = pd.to_datetime(found_date).date()
                else:
                    df['update_date'] = pd.Timestamp.now().date()

                if 'stock_code' in df.columns:
                    df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

                # 3. Type Conversion
                numeric_cols = [
                    'latest_price',
                    'limit_up_price',
                    'pct_chg',
                    'turnover',
                    'circ_mv',
                    'total_mv',
                    'turnover_rate']
                for col in numeric_cols:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')

                await self._run_in_executor(
                    self.ingestion_service.write_dataframe,
                    'limit_list_d', df, source=self.source, target_table='stock_zhaban_pool'
                )
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }
            return {"success": False, "data": [], "count": 0}

        except Exception as e:
            logger.error(f"Failed to ingest stock_zhaban_pool: {e}")
            return {"success": False, "data": [], "count": 0}
    async def fetch_and_ingest_stock_insider_trading(self, stock_code: str) -> Optional[dict]:
        """
        采集高管及相关人员持股变动数据并写入内部人交易表。

        官方文档:
            - stk_holdertrade: https://tushare.pro/document/2?doc_id=175

        Args:
            stock_code: 股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            api_name = 'stock_insider_trading'
            # Ensure stock code standard
            ts_code = StockCodeStandardizer.standardize(stock_code)

            # Use a longer window to ensure we get some test data (e.g. from 2000)
            # In production this might be incremental, but here we fetch history.
            start_date = '20000101'

            df = await self._run_in_executor(
                self.pro.stk_holdertrade, ts_code=ts_code, start_date=start_date
            )

            if df is None or df.empty:
                logger.warning(f"No insider trading data found for {stock_code} (Tushare)")
                return {"success": False, "data": [], "count": 0}

            # Mapping
            # 'in_de' -> 'change_type'
            # 'change_vol' -> 'change_shares' (Units, not Wan)
            df = ColumnMapper.map_columns(df, 'data.stock_insider_trading', source=self.source, strict=False)

            df['stock_code'] = ts_code
            df['data_source'] = self.source

            # Deduplicate as per model UniqueConstraint: stock_code, insider_name, trade_date, ann_date
            # Fallback: if trade_date (change date) is missing, use ann_date
            if 'trade_date' not in df.columns and 'ann_date' in df.columns:
                df['trade_date'] = df['ann_date']

            subset = ['stock_code', 'insider_name', 'trade_date', 'ann_date']
            if all(col in df.columns for col in subset):
                df = df.drop_duplicates(subset=subset, keep='last')

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df,
                source=self.source,
                target_table='stock_insider_trading'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }
        except Exception as e:
            logger.error(f"Failed to ingest insider trading (Tushare) for {stock_code}: {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_block_trade(
        self, stock_code: str, start_date: str = None, end_date: str = None
    ) -> Optional[dict]:
        """
        采集个股或全市场大宗交易数据并写入大宗交易表。

        官方文档:
            - block_trade: https://tushare.pro/document/2?doc_id=161

        Args:
            stock_code: 股票代码；为空时按日期范围采集全市场数据。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认最近 3 天。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认今天。

        Returns:
            采集并写入成功返回 True；无数据视为成功；配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            api_name = 'block_trade'
            # Fix: handle empty stock_code properly for global sync
            if not stock_code:
                ts_code = ''
            else:
                ts_code = StockCodeStandardizer.to_standard(stock_code)

            # 处理日期参数 | Handle date parameters
            if not start_date:
                # 默认采集最近 3 天，确保覆盖最近一个交易日
                start_date = (datetime.now() - timedelta(days=3)).strftime('%Y%m%d')
            else:
                start_date = start_date.replace('-', '').replace('/', '')

            if not end_date:
                end_date = datetime.now().strftime('%Y%m%d')
            else:
                end_date = end_date.replace('-', '').replace('/', '')

            # Prepare arguments
            # Tushare API might treat empty string as fetch all or specific param behavior
            kwargs = {
                "start_date": start_date,
                "end_date": end_date
            }
            if ts_code:
                kwargs['ts_code'] = ts_code

            df = await self._run_in_executor(
                self.pro.block_trade, **kwargs
            )

            if df is None or df.empty:
                return True  # No data is valid

            # Column Mapping
            df = ColumnMapper.map_columns(df, 'data.stock_block_trade', source=self.source, strict=False)

            # Standardize & Enrich
            # Fix: Don't overwrite stock_code with empty string if stock_code arg is empty
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)
            elif stock_code:
                df['stock_code'] = StockCodeStandardizer.standardize(stock_code)

            df['data_source'] = self.source

            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

            # Write key is tricky for block trade as one day can have multiple trades.
            # But underlying table has unique constraint on multiple fields.

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source, target_table='stock_block_trade'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }

        except Exception as e:
            logger.error(f"Failed to ingest block trade for {stock_code} (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_sector_money_flow(self, stock_code: str) -> Optional[dict]:
        """
        采集股票所属行业的板块资金流并写入行业资金流表。

        官方文档:
            - stock_basic: https://tushare.pro/document/2?doc_id=25
            - moneyflow_ind_dc: https://tushare.pro/document/2?doc_id=344

        Args:
            stock_code: 股票代码。

        Returns:
            采集并写入成功返回 True；行业无匹配数据视为成功；配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            # 1. 获取个股所属行业 (同花顺行业/申万行业)
            # Tushare moneyflow_ind_ths 对应的是同花顺行业
            ts_code = StockCodeStandardizer.to_standard(stock_code)

            def get_industry():
                # 为了匹配 moneyflow_ind_ths，我们可能需要查询 ths_member 或直接从 stock_basic 获取 industry
                # 注意：stock_basic 的 industry 字段可能不完全匹配 ths 的行业名，但通常是大分类一致。
                df_basic = self.pro.stock_basic(ts_code=ts_code, fields='industry')
                if df_basic is not None and not df_basic.empty:
                    return df_basic.iloc[0]['industry']
                return None

            industry = await self._run_in_executor(get_industry)

            if not industry:
                logger.warning(f"Could not determine industry for {stock_code}, skipping sector flow.")
                return {"success": False, "data": [], "count": 0}

            api_name = 'moneyflow_ind_dc'
            start_date = (datetime.now() - timedelta(days=10)).strftime('%Y%m%d')
            end_date = datetime.now().strftime('%Y%m%d')

            logger.info(f"Fetching industry money flow for {industry} via Tushare {api_name}")

            # 这里的 industry 如果不带代码，API 可能返回所有行业，我们需要过滤
            # moneyflow_ind_dc uses 'name' for sector name
            df = await self._run_in_executor(
                self.pro.query, api_name=api_name, start_date=start_date, end_date=end_date
            )

            if df is None or df.empty:
                return {"success": False, "data": [], "count": 0}

            # Rename columns to match filter if necessary (ColumnMapper will do it later for DB, but we need it now for filter)
            # TuShare moneyflow_ind_dc returns 'name'
            if 'name' in df.columns:
                df = df[df['name'] == industry]

            if df.empty:
                logger.info(f"No specific money flow data for industry {industry}")
                # 返回字典格式

                return {

                    "success": True,

                    "data": df.to_dict("records"),

                    "count": len(df)

                }

            # Column Mapping (using what we added to column_mapping.json)
            df = ColumnMapper.map_columns(df, 'data.sector_money_flow', source=self.source, strict=False)

            # Enrich
            df['data_source'] = self.source

            # Map main_net_inflow from net_inflow if not present
            if 'main_net_inflow' not in df.columns and 'net_inflow' in df.columns:
                df['main_net_inflow'] = df['net_inflow']

            if 'trade_date' in df.columns:
                df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

            # Ingest
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name, df, source=self.source, target_table='sector_money_flow'
            )
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }

        except Exception as e:
            logger.error(f"Failed to ingest sector money flow for {stock_code} (Tushare): {e}")
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_stock_top_holders(self, stock_code: str) -> Optional[dict]:
        """
        采集股票前十大股东数据并写入十大股东表。

        官方文档:
            - top10_holders: https://tushare.pro/document/2?doc_id=61

        Args:
            stock_code: 股票代码。

        Returns:
            采集并写入成功返回 True；无数据、配置缺失或异常返回 False。
        """
        try:
            ts_code = StockCodeStandardizer.to_standard(stock_code)
            logger.info(f"Fetching top 10 shareholders for {stock_code} ({ts_code}) via Tushare top10_holders")

            # 获取数据 (API 本身自带历史数据)
            df = await self._run_in_executor(
                self.pro.top10_holders,
                ts_code=ts_code
            )
            if df is None or not isinstance(df, pd.DataFrame):
                logger.warning(f"No valid top 10 shareholders data found for {stock_code} (Tushare)")
                return {"success": False, "data": [], "count": 0}

            if df.empty:
                logger.warning(f"Empty top 10 shareholders returned for {stock_code} (Tushare)")
                return {"success": False, "data": [], "count": 0}

            # 1. 字段映射
            df = ColumnMapper.map_columns(
                df, 'data.stock_top_holders', source=self.source, strict=False
            )

            # 2. 补充元数据
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source

            if 'report_date' in df.columns:
                df['report_date'] = pd.to_datetime(df['report_date']).dt.date

            # 3. 处理排名 (由获取的数据按持股量排序计算)
            df = df.sort_values(['report_date', 'hold_amount'], ascending=[False, False])
            df['holder_rank'] = df.groupby('report_date').cumcount() + 1

            # 4. 入库
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'top10_holders', df, source=self.source, target_table='stock_top_holders'
            )

            logger.info(f"Successfully ingested {len(df)} top 10 shareholder records for {stock_code} (Tushare)")
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }

        except Exception:
            logger.exception(f"Failed to ingest top 10 shareholders for {stock_code} (Tushare)")
            return {"success": False, "data": [], "count": 0}


    async def fetch_and_ingest_stock_interactive_qa(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[dict]:
        """
        采集上证或深证互动问答数据并写入互动问答表。

        官方文档:
            - irm_qa_sh: https://tushare.pro/document/2?doc_id=366
            - irm_qa_sz: https://tushare.pro/document/2?doc_id=367

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认最近 180 天。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认今天。

        Returns:
            采集并写入成功返回 True；无数据、市场不支持、配置缺失或异常返回 False。
        """
        try:
            if not self.pro:
                return {"success": False, "data": [], "count": 0}

            std_code = StockCodeStandardizer.standardize(stock_code)
            market = StockCodeStandardizer.get_market(std_code)

            if not start_date:
                start_date = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')
            if not end_date:
                end_date = datetime.now().strftime('%Y-%m-%d')

            start_date_compact = normalize_compact_date(start_date)
            end_date_compact = normalize_compact_date(end_date)

            if market == 'SH':
                api_name = 'irm_qa_sh'
                df = await self._run_in_executor(
                    self.pro.irm_qa_sh,
                    ts_code=std_code,
                    start_date=start_date_compact,
                    end_date=end_date_compact
                )
                mapping_source = 'tushare_sh'
            elif market == 'SZ':
                api_name = 'irm_qa_sz'
                df = await self._run_in_executor(
                    self.pro.irm_qa_sz,
                    ts_code=std_code,
                    start_date=start_date_compact,
                    end_date=end_date_compact
                )
                mapping_source = 'tushare_sz'
            else:
                logger.warning("Tushare interactive QA is not supported for %s (%s)", stock_code, market)
                return {"success": False, "data": [], "count": 0}

            if df is None or df.empty:
                logger.warning("No Tushare interactive QA found for %s", stock_code)
                return {"success": False, "data": [], "count": 0}

            df = ColumnMapper.map_columns(
                df,
                'data.stock_interactive_qa',
                source=mapping_source,
                strict=True
            )

            for col in ['question_time', 'answer_time']:
                if col in df.columns:
                    df[col] = pd.to_datetime(df[col], errors='coerce')
            if 'trade_date' in df.columns:
                trade_date_str = df['trade_date'].astype('string')
                parsed_compact = pd.to_datetime(trade_date_str, format='%Y%m%d', errors='coerce')
                parsed_fallback = pd.to_datetime(df['trade_date'], errors='coerce')
                df = df.copy()
                df['trade_date'] = parsed_compact.fillna(parsed_fallback).dt.date

            if df is None or df.empty:
                logger.warning("No interactive QA rows left before write for %s", stock_code)
                return {"success": False, "data": [], "count": 0}

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                api_name,
                df,
                source=self.source,
                target_table='stock_interactive_qa'
            )

            logger.info("Successfully ingested %s interactive QA rows for %s (Tushare)", len(df), stock_code)
            # 返回字典格式

            return {

                "success": True,

                "data": df.to_dict("records"),

                "count": len(df)

            }

        except Exception as e:
            logger.error(f"Failed to ingest interactive QA for {stock_code} (Tushare): {e}", exc_info=True)
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_income_statement(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> Optional[dict]:
        """
        获取单只股票利润表并返回标准化记录。

        官方文档:
            - income: https://tushare.pro/document/2?doc_id=33

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认最近一年。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时默认今天。

        Returns:
            获取成功返回标准化记录；无数据、参数缺失、配置缺失或异常返回空结果。
        """
        try:
            if not self.pro:
                logger.error("Tushare API client (pro) not initialized")
                return {"success": False, "data": [], "count": 0}

            if not stock_code:
                logger.error("Stock code is required for income statement sync (Tushare).")
                return {"success": False, "data": [], "count": 0}

            code = StockCodeStandardizer.standardize(stock_code)
            today = datetime.now().date()
            normalized_start_date = normalize_compact_date(start_date) if start_date else (today - timedelta(days=365)).strftime('%Y%m%d')
            normalized_end_date = normalize_compact_date(end_date) if end_date else today.strftime('%Y%m%d')
            params = {'ts_code': code}
            params['start_date'] = normalized_start_date
            params['end_date'] = normalized_end_date

            logger.info(
                "Fetching income statement for %s via Tushare income with params=%s",
                code,
                params,
            )

            df = await self._run_in_executor(
                self.pro.income,
                cache_ttl=3600,
                **params,
            )

            if df is None or df.empty:
                logger.warning(f"No income statement data found for {code} (Tushare)")
                return {"success": False, "data": [], "count": 0}

            df = ColumnMapper.map_columns(
                df.copy(),
                'data.stock_income_statement',
                source='tushare_income_statement',
                strict=True,
            )

            records = []

            def _parse_tushare_date(value):
                if value is None or pd.isna(value):
                    return None
                parsed = pd.to_datetime(str(value).strip(), format='%Y%m%d', errors='coerce')
                if pd.isna(parsed):
                    parsed = pd.to_datetime(value, errors='coerce')
                return None if pd.isna(parsed) else parsed.date()

            metadata_fields = {
                'stock_code',
                'announcement_date',
                'report_date',
                'report_type',
            }

            for row_dict in df.to_dict(orient='records'):
                record_stock_code = row_dict.get('stock_code')
                announcement_date = _parse_tushare_date(row_dict.get('announcement_date'))
                report_date = _parse_tushare_date(row_dict.get('report_date'))
                report_type = row_dict.get('report_type')

                if not report_date or not record_stock_code:
                    continue

                standardized_data = {}
                for key, value in row_dict.items():
                    if key in metadata_fields:
                        continue
                    if pd.isna(value):
                        standardized_data[key] = None
                    else:
                        standardized_data[key] = value
                standardized_data = format_payload_values(
                    'data.stock_income_statement',
                    standardized_data,
                )
                if not standardized_data:
                    continue

                records.append({
                    'stock_code': record_stock_code,
                    'announcement_date': announcement_date,
                    'report_date': report_date,
                    'report_type': report_type,
                    'data': standardized_data,
                    'update_date': today,
                    'data_source': self.source,
                })

            if not records:
                return {"success": False, "data": [], "count": 0}

            final_df = pd.DataFrame(records)
            final_df = final_df.sort_values(
                ['report_date', 'announcement_date', 'report_type'],
                ascending=[True, False, True],
                na_position='last',
            ).drop_duplicates(
                subset=['report_date', 'announcement_date', 'report_type'],
                keep='first',
            )

            return {
                "success": True,
                "data": final_df.to_dict("records"),
                "count": len(final_df),
            }

        except Exception as e:
            logger.error(f"Failed to ingest income statement for {stock_code} (Tushare): {e}", exc_info=True)
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_balance_sheet(
            self,
            stock_code: str,
            start_date: str,
            end_date: str) -> Optional[dict]:
        """
        获取单只股票资产负债表并返回标准化记录。

        官方文档:
            - balancesheet: https://tushare.pro/document/2?doc_id=36

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD。

        Returns:
            获取成功返回标准化记录；无数据、参数缺失、配置缺失或异常返回空结果。
        """
        try:
            if not self.pro:
                logger.error("Tushare API client (pro) not initialized")
                return {"success": False, "data": [], "count": 0}

            if not stock_code:
                logger.error("Stock code is required for balance sheet sync (Tushare).")
                return {"success": False, "data": [], "count": 0}
            if not start_date or not end_date:
                logger.error("start_date and end_date are required for balance sheet sync (Tushare).")
                return {"success": False, "data": [], "count": 0}

            code = StockCodeStandardizer.standardize(stock_code)
            today = datetime.now().date()
            normalized_start_date = normalize_compact_date(start_date)
            normalized_end_date = normalize_compact_date(end_date)
            table_mapping = ColumnMapper.get_table_mapping('data.stock_balance_sheet', 'tushare_balance_sheet')
            params = {
                'ts_code': code,
                'start_date': normalized_start_date,
                'end_date': normalized_end_date,
                'fields': ",".join(table_mapping.keys()),
            }

            logger.info(
                "Fetching balance sheet for %s via Tushare balancesheet with params=%s",
                code,
                {k: v for k, v in params.items() if k != 'fields'},
            )

            df = await self._run_in_executor(
                self.pro.balancesheet,
                cache_ttl=3600,
                **params,
            )

            if df is None or df.empty:
                logger.warning(f"No balance sheet data found for {code} (Tushare)")
                return {"success": False, "data": [], "count": 0}

            df = ColumnMapper.map_columns(
                df.copy(),
                'data.stock_balance_sheet',
                source='tushare_balance_sheet',
                strict=True,
            )

            records = []

            def _parse_tushare_date(value):
                if value is None or pd.isna(value):
                    return None
                parsed = pd.to_datetime(str(value).strip(), format='%Y%m%d', errors='coerce')
                if pd.isna(parsed):
                    parsed = pd.to_datetime(value, errors='coerce')
                return None if pd.isna(parsed) else parsed.date()

            metadata_fields = {
                'stock_code',
                'announcement_date',
                'report_date',
                'report_type',
            }

            for row_dict in df.to_dict(orient='records'):
                record_stock_code = row_dict.get('stock_code')
                announcement_date = _parse_tushare_date(row_dict.get('announcement_date'))
                report_date = _parse_tushare_date(row_dict.get('report_date'))
                report_type = row_dict.get('report_type')

                if not report_date or not record_stock_code:
                    continue

                standardized_data = {}
                for key, value in row_dict.items():
                    if key in metadata_fields or str(key).startswith('_unused_'):
                        continue
                    if pd.isna(value):
                        standardized_data[key] = None
                    else:
                        standardized_data[key] = value
                standardized_data = format_payload_values(
                    'data.stock_balance_sheet',
                    standardized_data,
                )
                if not standardized_data:
                    continue

                records.append({
                    'stock_code': record_stock_code,
                    'announcement_date': announcement_date,
                    'report_date': report_date,
                    'report_type': report_type,
                    'data': standardized_data,
                    'update_date': today,
                    'data_source': self.source,
                })

            if not records:
                return {"success": False, "data": [], "count": 0}

            final_df = pd.DataFrame(records)
            final_df = final_df.sort_values(
                ['report_date', 'announcement_date', 'report_type'],
                ascending=[True, False, True],
                na_position='last',
            ).drop_duplicates(
                subset=['report_date', 'announcement_date', 'report_type'],
                keep='first',
            )

            return {
                "success": True,
                "data": final_df.to_dict("records"),
                "count": len(final_df),
            }

        except Exception as e:
            logger.error(f"Failed to ingest balance sheet for {stock_code} (Tushare): {e}", exc_info=True)
            return {"success": False, "data": [], "count": 0}

    async def fetch_and_ingest_cashflow_statement(
            self,
            stock_code: str,
            start_date: str,
            end_date: str) -> Optional[dict]:
        """
        获取单只股票现金流量表并返回标准化记录。

        官方文档:
            - cashflow: https://tushare.pro/document/2?doc_id=44

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD。

        Returns:
            获取成功返回标准化记录；无数据、参数缺失、配置缺失或异常返回空结果。
        """
        try:
            if not self.pro:
                logger.error("Tushare API client (pro) not initialized")
                return {"success": False, "data": [], "count": 0}

            if not stock_code:
                logger.error("Stock code is required for cashflow statement sync (Tushare).")
                return {"success": False, "data": [], "count": 0}
            if not start_date or not end_date:
                logger.error("start_date and end_date are required for cashflow statement sync (Tushare).")
                return {"success": False, "data": [], "count": 0}

            code = StockCodeStandardizer.standardize(stock_code)
            today = datetime.now().date()
            normalized_start_date = normalize_compact_date(start_date)
            normalized_end_date = normalize_compact_date(end_date)
            table_mapping = ColumnMapper.get_table_mapping('data.stock_cashflow_statement', 'tushare_cashflow_statement')
            params = {
                'ts_code': code,
                'start_date': normalized_start_date,
                'end_date': normalized_end_date,
                'fields': ",".join(table_mapping.keys()),
            }

            logger.info(
                "Fetching cashflow statement for %s via Tushare cashflow with params=%s",
                code,
                {k: v for k, v in params.items() if k != 'fields'},
            )

            df = await self._run_in_executor(
                self.pro.cashflow,
                cache_ttl=3600,
                **params,
            )

            if df is None or df.empty:
                logger.warning(f"No cashflow statement data found for {code} (Tushare)")
                return {"success": False, "data": [], "count": 0}

            df = ColumnMapper.map_columns(
                df.copy(),
                'data.stock_cashflow_statement',
                source='tushare_cashflow_statement',
                strict=True,
            )

            records = []

            def _parse_tushare_date(value):
                if value is None or pd.isna(value):
                    return None
                parsed = pd.to_datetime(str(value).strip(), format='%Y%m%d', errors='coerce')
                if pd.isna(parsed):
                    parsed = pd.to_datetime(value, errors='coerce')
                return None if pd.isna(parsed) else parsed.date()

            metadata_fields = {
                'stock_code',
                'announcement_date',
                'report_date',
                'report_type',
            }

            for row_dict in df.to_dict(orient='records'):
                record_stock_code = row_dict.get('stock_code')
                announcement_date = _parse_tushare_date(row_dict.get('announcement_date'))
                report_date = _parse_tushare_date(row_dict.get('report_date'))
                report_type = row_dict.get('report_type')

                if not report_date or not record_stock_code:
                    continue

                standardized_data = {}
                for key, value in row_dict.items():
                    if key in metadata_fields or str(key).startswith('_unused_'):
                        continue
                    if pd.isna(value):
                        standardized_data[key] = None
                    else:
                        standardized_data[key] = value
                standardized_data = format_payload_values(
                    'data.stock_cashflow_statement',
                    standardized_data,
                )
                if not standardized_data:
                    continue

                records.append({
                    'stock_code': record_stock_code,
                    'announcement_date': announcement_date,
                    'report_date': report_date,
                    'report_type': report_type,
                    'data': standardized_data,
                    'update_date': today,
                    'data_source': self.source,
                })

            if not records:
                return {"success": False, "data": [], "count": 0}

            final_df = pd.DataFrame(records)
            final_df = final_df.sort_values(
                ['report_date', 'announcement_date', 'report_type'],
                ascending=[True, False, True],
                na_position='last',
            ).drop_duplicates(
                subset=['report_date', 'announcement_date', 'report_type'],
                keep='first',
            )

            return {
                "success": True,
                "data": final_df.to_dict("records"),
                "count": len(final_df),
            }

        except Exception as e:
            logger.error(f"Failed to ingest cashflow statement for {stock_code} (Tushare): {e}", exc_info=True)
            return {"success": False, "data": [], "count": 0}
