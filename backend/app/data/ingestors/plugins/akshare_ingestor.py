"""
AKShare 数据源插件。

AKShare 是开源的财经数据接口库，无需 token 即可获取 A 股行情、财务等数据。
官方文档: https://akshare.akfamily.xyz/
"""

import akshare as ak
import asyncio
import time
import pandas as pd
from datetime import datetime, timedelta
from typing import Any, Optional

from app.data.ingestors.base_ingestor import BaseIngestor
from app.data.ingestion.service import DataIngestionService
from app.data.ingestors.rate_limiter import LeakyBucketRateLimiter
from app.core.utils.formatters import StockCodeStandardizer
from app.core.logger import get_logger

logger = get_logger(__name__)


def _format_symbol_for_daily(stock_code: str) -> str:
    """
    将股票代码格式化为 stock_zh_a_daily 接口需要的格式。

    Args:
        stock_code: 标准股票代码（如 000001.SZ 或 600000.SH）

    Returns:
        格式化后的代码（如 sz000001 或 sh600000）

    Examples:
        >>> _format_symbol_for_daily("000001.SZ")
        'sz000001'
        >>> _format_symbol_for_daily("600000.SH")
        'sh600000'
    """
    symbol = StockCodeStandardizer.to_number(stock_code)

    # 构造 stock_zh_a_daily 需要的 symbol 格式（sz000001 或 sh600000）
    if stock_code.endswith('.SZ'):
        return f"sz{symbol}"
    elif stock_code.endswith('.SH'):
        return f"sh{symbol}"
    else:
        # 根据代码判断市场
        return f"sz{symbol}" if symbol.startswith(('0', '3')) else f"sh{symbol}"


class AkshareIngestor(BaseIngestor):
    """AKShare 数据采集插件。"""

    source_name = "akshare"
    display_name = "AKShare"
    required_settings = ()  # AKShare 无需配置

    # 类级别的限流器实例（所有 AKShare 实例共享）
    _shared_rate_limiter: Optional[LeakyBucketRateLimiter] = None

    def __init__(self):
        self.ingestion_service = DataIngestionService()
        self.source = self.get_source_name()

        # 初始化限流器（单例模式）
        if AkshareIngestor._shared_rate_limiter is None:
            from app.core.config import settings
            max_calls = getattr(settings, 'AKSHARE_MAX_CALLS_PER_MINUTE', 60)
            AkshareIngestor._shared_rate_limiter = LeakyBucketRateLimiter(max_calls_per_minute=max_calls)

        # 实例级别的限流器引用（AKShare 独立使用）
        self.rate_limiter = AkshareIngestor._shared_rate_limiter
        logger.info(
            "AkshareIngestor initialized",
            extra={
                "rate_limit": f"{self.rate_limiter.max_calls_per_minute} calls/min"
            }
        )

    async def _run_in_executor(self, func, *args, use_cache: bool = True, cache_ttl: int = 60, **kwargs):
        """
        重写基类方法，在调用 AKShare API 前先获取限流令牌。

        Args:
            func: 阻塞函数。
            *args: 位置参数。
            use_cache: 是否使用 Redis 缓存。
            cache_ttl: 缓存过期时间（秒）。
            **kwargs: 关键字参数。

        Returns:
            函数执行结果。
        """
        # 在调用 API 前先获取 AKShare 限流令牌
        acquired = await self.rate_limiter.acquire(timeout=30.0)
        if not acquired:
            logger.warning(
                "AKShare rate limiter timeout after 30s",
                extra={"func": self._get_func_name(func)}
            )
            # 超时后仍尝试调用（让 AKShare 自己返回错误）

        # 调用基类方法执行实际 API 请求
        return await super()._run_in_executor(func, *args, use_cache=use_cache, cache_ttl=cache_ttl, **kwargs)

    def _normalize_ths_statement_records(
        self,
        df: pd.DataFrame,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        *,
        report_type: str,
    ) -> list[dict[str, Any]]:
        """将 AKShare 同花顺财报宽表转换为上下文使用的标准记录。

        Args:
            df: AKShare 同花顺财报接口返回的宽表。
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时不过滤下界。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时不过滤上界。
            report_type: 写入上下文 meta 的报表类型。

        Returns:
            按报告期聚合后的标准财务记录列表。
        """
        if df is None or df.empty or "报告期" not in df.columns:
            return []

        start_dt = pd.to_datetime(start_date.replace("-", ""), format="%Y%m%d", errors="coerce") if start_date else None
        end_dt = pd.to_datetime(end_date.replace("-", ""), format="%Y%m%d", errors="coerce") if end_date else None
        code = StockCodeStandardizer.standardize(stock_code)
        normalized_df = df.copy()
        normalized_df["report_date"] = pd.to_datetime(normalized_df["报告期"], errors="coerce")
        if start_dt is not None and not pd.isna(start_dt):
            normalized_df = normalized_df[normalized_df["report_date"] >= start_dt]
        if end_dt is not None and not pd.isna(end_dt):
            normalized_df = normalized_df[normalized_df["report_date"] <= end_dt]

        metadata_columns = {"报告期", "report_date", "报表核心指标", "报表全部指标", "补充资料："}
        records: list[dict[str, Any]] = []

        for _, row in normalized_df.iterrows():
            report_dt = row.get("report_date")
            if pd.isna(report_dt):
                continue

            data: dict[str, Any] = {}
            for field_name, value in row.items():
                if field_name in metadata_columns:
                    continue
                if value is None or value is False or pd.isna(value) or value == "":
                    continue
                data[str(field_name)] = value

            if not data:
                continue
            records.append({
                "stock_code": code,
                "report_date": report_dt.date(),
                "announcement_date": None,
                "report_type": report_type,
                "data": data,
                "data_source": self.source,
            })

        return sorted(records, key=lambda item: str(item.get("report_date") or ""), reverse=True)

    def _normalize_financial_indicator_records(
        self,
        df: pd.DataFrame,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """将 AKShare 财务指标宽表转换为上下文使用的标准记录。

        Args:
            df: ``stock_financial_analysis_indicator`` 返回的财务指标宽表。
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时不过滤下界。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD；为空时不过滤上界。

        Returns:
            按报告期倒序排列的标准财务指标记录列表。
        """
        if df is None or df.empty or "日期" not in df.columns:
            return []

        start_dt = pd.to_datetime(start_date.replace("-", ""), format="%Y%m%d", errors="coerce") if start_date else None
        end_dt = pd.to_datetime(end_date.replace("-", ""), format="%Y%m%d", errors="coerce") if end_date else None
        code = StockCodeStandardizer.standardize(stock_code)
        normalized_df = df.copy()
        normalized_df["report_date"] = pd.to_datetime(normalized_df["日期"], errors="coerce")
        if start_dt is not None and not pd.isna(start_dt):
            normalized_df = normalized_df[normalized_df["report_date"] >= start_dt]
        if end_dt is not None and not pd.isna(end_dt):
            normalized_df = normalized_df[normalized_df["report_date"] <= end_dt]

        records: list[dict[str, Any]] = []
        for _, row in normalized_df.iterrows():
            report_dt = row.get("report_date")
            if pd.isna(report_dt):
                continue
            data: dict[str, Any] = {}
            for field_name, value in row.items():
                if field_name in {"日期", "report_date"}:
                    continue
                if value is None or pd.isna(value) or value == "":
                    continue
                data[str(field_name)] = value
            if not data:
                continue
            records.append({
                "stock_code": code,
                "report_date": report_dt.date(),
                "announcement_date": None,
                "report_type": "akshare_financial_analysis_indicator",
                "data": data,
                "data_source": self.source,
            })

        return sorted(records, key=lambda item: str(item.get("report_date") or ""), reverse=True)

    async def fetch_and_ingest_stock_kline(
            self,
            stock_code: str,
            start_date: str,
            end_date: str,
            period: str = "daily",
            adjust: str = "qfq") -> Optional[dict]:
        """
        采集单只股票 K 线行情并写入标准行情表。

        使用 stock_zh_a_daily 替代 stock_zh_a_hist，避免流控。

        官方文档:
            - stock_zh_a_daily: https://akshare.akfamily.xyz/data/stock/stock.html

        Args:
            stock_code: 股票代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            period: 行情周期，支持 daily、weekly、monthly。
            adjust: 复权参数，支持 qfq(前复权)、hfq(后复权)、空字符串(不复权)。

        Returns:
            返回字典 {"success": bool, "data": 数据列表, "count": 数据行数}；异常返回 None。
        """
        try:
            # 格式化股票代码
            daily_symbol = _format_symbol_for_daily(stock_code)

            # 映射 adjust 参数
            adjust_map = {
                "qfq": "qfq",  # 前复权
                "hfq": "hfq",  # 后复权
                "": ""         # 不复权
            }
            ak_adjust = adjust_map.get(adjust, "qfq")

            logger.info(f"Fetching AKShare kline (stock_zh_a_daily) for {daily_symbol}, adjust={ak_adjust}")

            # 调用 stock_zh_a_daily（避免流控）
            df = await self._run_in_executor(
                ak.stock_zh_a_daily,
                symbol=daily_symbol,
                adjust=ak_adjust
            )

            if df is None or df.empty:
                logger.warning(f"No kline data returned from AKShare for {stock_code}")
                return {"success": False, "data": [], "count": 0}

            # stock_zh_a_daily 返回列名：date, open, high, low, close, volume, amount等
            # 列名映射：AKShare 列名 -> 数据库字段
            column_rename = {
                'amount': 'turnover'  # 成交额
            }
            df.rename(columns=column_rename, inplace=True)

            # 过滤日期范围
            start_date_obj = pd.to_datetime(start_date.replace('-', ''), format='%Y%m%d')
            end_date_obj = pd.to_datetime(end_date.replace('-', ''), format='%Y%m%d')
            df['date'] = pd.to_datetime(df['date'])
            df = df[(df['date'] >= start_date_obj) & (df['date'] <= end_date_obj)]

            if df.empty:
                logger.warning(f"No data in date range for {stock_code}")
                return {"success": False, "data": [], "count": 0}

            # 补充必要字段
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['freq'] = 'D'  # 日线
            df['data_source'] = self.source
            df['date'] = df['date'].dt.date

            # 数值类型确保
            numeric_cols = ['open', 'close', 'high', 'low', 'volume', 'turnover']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 写入数据库
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'stock_zh_a_daily', df, source=self.source, target_table='kline_data'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }

        except Exception as e:
            logger.error(f"Failed to ingest AKShare kline for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_info(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票基础信息并写入股票基础表。

        官方文档:
            - stock_individual_info_em: https://akshare.akfamily.xyz/data/stock/stock.html#id73

        Args:
            stock_code: 股票代码。

        Returns:
            返回字典 {"success": bool, "data": 数据列表, "count": 数据行数}；异常返回 None。
        """
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)

            logger.info(f"Fetching AKShare stock info for {symbol}")

            # 调用 AKShare 个股信息接口
            df = await self._run_in_executor(
                ak.stock_individual_info_em,
                symbol=symbol
            )

            if df is None or df.empty:
                logger.warning(f"No stock info returned from AKShare for {stock_code}")
                return {"success": False, "data": [], "count": 0}

            # AKShare 返回的是 key-value 格式，需要转换
            info_dict = dict(zip(df['item'], df['value']))

            # 构造标准格式
            result_df = pd.DataFrame([{
                'stock_code': StockCodeStandardizer.standardize(stock_code),
                'name': info_dict.get('股票简称', ''),
                'industry': info_dict.get('行业', ''),
                'area': info_dict.get('地域', ''),
                'market': StockCodeStandardizer.get_market(stock_code),
                'list_date': None,  # AKShare 个股信息接口不直接提供上市日期
                'data_source': self.source
            }])

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'stock_individual_info', result_df, source=self.source, target_table='stock_basic'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }

        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock info for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_all_stock_basic(self) -> Optional[dict]:
        """
        全量采集 A 股基础信息并写入股票基础表。

        官方文档:
            - stock_info_a_code_name: https://akshare.akfamily.xyz/data/stock/stock.html#id2

        Returns:
            返回字典 {"success": bool, "data": 数据列表, "count": 数据行数}；异常返回 None。
        """
        try:
            logger.info("Fetching all A-share basic info from AKShare")

            # 调用 AKShare 接口获取所有 A 股代码和名称
            df = await self._run_in_executor(ak.stock_info_a_code_name)

            if df is None or df.empty:
                logger.warning("No stock basic data returned from AKShare")
                return {"success": False, "data": [], "count": 0}

            # AKShare 返回列：code, name
            df.rename(columns={'code': 'stock_code', 'name': 'name'}, inplace=True)

            # 标准化股票代码（添加市场后缀）
            df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)
            df['market'] = df['stock_code'].apply(StockCodeStandardizer.get_market)
            df['data_source'] = self.source

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'stock_info_a_code_name', df, source=self.source, target_table='stock_basic'
            )

            logger.info(f"Successfully synced {len(df)} stocks basic info from AKShare")
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }

        except Exception as e:
            logger.error(f"Failed to ingest all stock basic from AKShare: {e}")
            return None

    async def fetch_and_ingest_realtime_market(self, stock_code: str = None) -> Optional[dict]:
        """
        采集全市场实时行情并写入实时行情表。

        注意：此方法总是获取全市场数据（约5500只股票），耗时约20秒。
        stock_code 参数已废弃，保留仅为兼容性，实际会忽略并获取全市场数据。

        建议使用方式：
        1. 定时任务每分钟调用一次，更新全市场数据
        2. 查询时直接从数据库读取，无需重复调用此接口

        官方文档:
            - stock_zh_a_spot: https://akshare.akfamily.xyz/data/stock/stock.html#id4

        Args:
            stock_code: 已废弃，保留仅为兼容性。传入任何值都会获取全市场数据。

        Returns:
            采集并写入成功返回字典 {"success": bool, "data": list, "count": int}；异常返回 None。
        """
        try:
            if stock_code:
                logger.warning(
                    f"fetch_and_ingest_realtime_market called with stock_code={stock_code}, "
                    "but this parameter is deprecated. Fetching full market data instead."
                )

            logger.info("Fetching AKShare full market realtime data (~5500 stocks, ~20s)")

            # 调用 AKShare 接口获取全市场实时行情
            df = await self._run_in_executor(ak.stock_zh_a_spot)

            if df is None or df.empty:
                logger.warning("No realtime data returned from AKShare")
                return {"success": False, "data": [], "count": 0}

            # 列名映射
            # AKShare 返回：代码, 名称, 最新价, 涨跌额, 涨跌幅, 买入, 卖出, 昨收, 今开, 最高, 最低, 成交量, 成交额, 时间戳
            column_rename = {
                '代码': 'stock_code',
                '名称': 'name',
                '最新价': 'current_price',
                '涨跌幅': 'change_percent',
                '涨跌额': 'change_amount',
                '成交量': 'volume',
                '成交额': 'turnover',
                '最高': 'high',
                '最低': 'low',
                '今开': 'open',
                '昨收': 'pre_close',
                '买入': 'bid',
                '卖出': 'ask'
            }
            df.rename(columns=column_rename, inplace=True)

            # 标准化股票代码（添加市场后缀）
            df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            # 补充字段
            df['data_source'] = self.source
            df['timestamp'] = pd.Timestamp.now()

            # 数值转换
            numeric_cols = ['current_price', 'change_percent', 'change_amount', 'volume',
                          'turnover', 'high', 'low', 'open', 'pre_close', 'bid', 'ask']
            for col in numeric_cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # 过滤无效价格（保留有效数据）
            original_count = len(df)
            if 'current_price' in df.columns:
                df = df[df['current_price'] > 0].copy()
                filtered_count = original_count - len(df)
                if filtered_count > 0:
                    logger.info(f"Filtered {filtered_count} stocks with invalid price")

            if df.empty:
                logger.warning("All stocks have invalid price in AKShare realtime data")
                return {"success": False, "data": [], "count": 0}

            # 写入数据库（全量）
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'stock_zh_a_spot', df, source=self.source, target_table='data.stock_realtime_market'
            )

            logger.info(f"Successfully ingested {len(df)} stocks realtime market data from AKShare")

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }

        except Exception as e:
            logger.error(f"Failed to ingest AKShare realtime market for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_index_daily(
            self,
            index_code: str,
            start_date: str,
            end_date: str) -> Optional[dict]:
        """
        采集大盘指数日线行情并写入指数日线表。

        官方文档:
            - stock_zh_index_daily: https://akshare.akfamily.xyz/data/index/index.html#id5

        Args:
            index_code: 指数代码。
            start_date: 开始日期，支持 YYYY-MM-DD 或 YYYYMMDD。
            end_date: 结束日期，支持 YYYY-MM-DD 或 YYYYMMDD。

        Returns:
            返回字典 {"success": bool, "data": 数据列表, "count": 数据行数}；异常返回 None。
        """
        try:
            # AKShare 的指数接口使用指数代码（如 sh000001）
            # 标准化为带市场前缀的代码
            symbol = StockCodeStandardizer.to_standard_index(index_code)

            logger.info(f"Fetching AKShare index daily for {symbol}")

            # 调用 AKShare 接口
            df = await self._run_in_executor(
                ak.stock_zh_index_daily,
                symbol=symbol
            )

            if df is None or df.empty:
                logger.warning(f"No index daily data returned from AKShare for {index_code}")
                return {"success": False, "data": [], "count": 0}

            # 日期过滤
            start_dt = pd.to_datetime(start_date.replace('-', ''))
            end_dt = pd.to_datetime(end_date.replace('-', ''))
            df['date'] = pd.to_datetime(df['date'])
            df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]

            if df.empty:
                logger.warning(f"No index data in date range for {index_code}")
                return {"success": False, "data": [], "count": 0}

            # 列名映射
            df.rename(columns={
                'date': 'trade_date',
                'open': 'open',
                'close': 'close',
                'high': 'high',
                'low': 'low',
                'volume': 'volume',
                'amount': 'turnover'
            }, inplace=True)

            # 补充字段
            df['index_code'] = symbol
            df['data_source'] = self.source
            df['trade_date'] = df['trade_date'].dt.date

            # 写入数据库
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'stock_zh_index_daily', df, source=self.source, target_table='index_daily'
            )

            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }

        except Exception as e:
            logger.error(f"Failed to ingest AKShare index daily for {index_code}: {e}")
            return None

    async def fetch_and_ingest_financial_indicators(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> Optional[dict]:
        """
        获取单只股票财务指标。

        官方文档:
            - stock_financial_analysis_indicator: https://akshare.akfamily.xyz/data/stock/stock.html#id118

        Args:
            stock_code: 股票代码。
            start_date: 开始日期（可选）。
            end_date: 结束日期（可选）。

        Returns:
            返回字典 {"success": bool, "data": 数据列表, "count": 数据行数}；异常返回 None。
        """
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)

            logger.info(f"Fetching AKShare financial indicators for {symbol}")

            # 使用传入的 start_date，如果没有则默认最近 1 年
            if start_date:
                try:
                    parsed_start = datetime.strptime(start_date.replace("-", ""), "%Y%m%d")
                    start_year = str(parsed_start.year)
                except (ValueError, AttributeError):
                    start_year = str((datetime.now().date() - timedelta(days=365)).year)
            else:
                start_year = str((datetime.now().date() - timedelta(days=365)).year)

            # 调用 AKShare 接口（财务数据缓存 1 小时）
            df = await self._run_in_executor(
                ak.stock_financial_analysis_indicator,
                symbol=symbol,
                start_year=start_year,
                cache_ttl=3600,
            )

            if df is None or df.empty:
                logger.warning(f"No financial indicators returned from AKShare for {stock_code}")
                return {"success": False, "data": [], "count": 0}

            records = self._normalize_financial_indicator_records(
                df,
                stock_code,
                start_date,
                end_date,
            )
            return {
                "success": bool(records),
                "data": records,
                "count": len(records)
            }

        except Exception as e:
            logger.error(f"Failed to ingest AKShare financial indicators for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_valuation(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> Optional[dict]:
        """采集单只股票每日估值与基础行情指标并写入估值历史表。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock valuation for {symbol}")
            # AKShare 没有直接对应的每日估值接口，返回 False
            logger.warning("AKShare does not have direct stock valuation API")
            return {"success": False, "data": [], "count": 0}
        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock valuation for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_northbound(self, stock_code: str) -> Optional[dict]:
        """采集单只股票沪深港股通持股明细。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare northbound for {symbol}")
            # 使用 stock_hsgt_individual_em (个股接口)
            df = await self._run_in_executor(ak.stock_hsgt_individual_em, symbol=symbol)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '持股日期': 'date',
                '当日收盘价': 'close_price',
                '当日涨跌幅': 'change_percent',
                '持股数量': 'hold_shares',
                '持股市值': 'hold_value',
                '持股数量占A股百分比': 'hold_ratio',
                '今日增持股数': 'net_buy_volume',
                '今日增持资金': 'net_buy_amount',
                '今日持股市值变化': 'hold_value_change'
            }
            df.rename(columns=column_rename, inplace=True)

            # 补充必需字段
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'northbound', df, source=self.source, target_table='northbound_data'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare northbound for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_company_profile(self, stock_code: str) -> Optional[dict]:
        """采集上市公司基础资料。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare company profile for {symbol}")
            # AKShare 使用 stock_individual_info_em
            df = await self._run_in_executor(ak.stock_individual_info_em, symbol=symbol)
            if df is None or df.empty:
                return None
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'stock_individual_info', df, source=self.source
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare company profile for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_dragon_tiger(self, start_date: str, end_date: str = None) -> Optional[dict]:
        """按日期范围采集龙虎榜每日统计数据。"""
        try:
            logger.info(f"Fetching AKShare dragon tiger for {start_date} to {end_date}")
            date_str = start_date.replace('-', '')
            df = await self._run_in_executor(ak.stock_lhb_detail_daily_sina, date=date_str)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            # AKShare 返回：序号, 股票代码, 股票名称, 收盘价, 对应值, 成交量, 成交额, 指标
            # 数据库期望：stock_code, stock_name, trade_date, close_price, net_buy_amount,
            #            buy_amount, sell_amount, listing_reason 等
            column_rename = {
                '序号': 'sequence_number',
                '股票代码': 'stock_code',
                '股票名称': 'stock_name',
                '收盘价': 'close_price',
                '对应值': 'indicator_value',
                '成交量': 'volume',
                '成交额': 'total_trade_amount',
                '指标': 'listing_reason'
            }
            df.rename(columns=column_rename, inplace=True)

            # 补充必需字段
            df['trade_date'] = pd.to_datetime(date_str, format='%Y%m%d').date()
            df['data_source'] = self.source

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'dragon_tiger', df, source=self.source, target_table='dragon_tiger_data'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare dragon tiger: {e}")
            return None

    async def fetch_and_ingest_board_industry(self) -> Optional[dict]:
        """采集外部行业板块数据。"""
        try:
            logger.info("Fetching AKShare board industry")
            df = await self._run_in_executor(ak.stock_board_industry_name_em)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '排名': 'rank',
                '板块名称': 'board_name',
                '板块代码': 'board_code',
                '最新价': 'latest_price',
                '涨跌额': 'change_amount',
                '涨跌幅': 'change_percent',
                '总市值': 'total_market_cap',
                '换手率': 'turnover_rate',
                '上涨家数': 'rising_stocks_count',
                '下跌家数': 'falling_stocks_count',
                '领涨股票': 'leading_stock_name',
                '领涨股票-涨跌幅': 'leading_stock_change_percent'
            }
            df.rename(columns=column_rename, inplace=True)

            df['data_source'] = self.source
            df['timestamp'] = pd.Timestamp.now()
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'board_industry', df, source=self.source, target_table='industry_data'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare board industry: {e}")
            return None

    async def fetch_and_ingest_stock_money_flow(self, stock_code: str) -> Optional[dict]:
        """采集单只股票资金流向。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock money flow for {symbol}")
            df = await self._run_in_executor(ak.stock_individual_fund_flow, stock=symbol, market="sh" if symbol.startswith('6') else "sz")
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '日期': 'trade_date',
                '收盘价': 'close_price',
                '涨跌幅': 'pct_change',
                '主力净流入-净额': 'net_inflow_main',
                '主力净流入-净占比': 'net_inflow_main_pct',
                '超大单净流入-净额': 'net_inflow_huge',
                '超大单净流入-净占比': 'net_inflow_huge_pct',
                '大单净流入-净额': 'net_inflow_large',
                '大单净流入-净占比': 'net_inflow_large_pct',
                '中单净流入-净额': 'net_inflow_medium',
                '中单净流入-净占比': 'net_inflow_medium_pct',
                '小单净流入-净额': 'net_inflow_small',
                '小单净流入-净占比': 'net_inflow_small_pct'
            }
            df.rename(columns=column_rename, inplace=True)

            # 单位转换：AKShare 返回的是元，数据库存储单位是万元
            amount_columns = [
                'net_inflow_main', 'net_inflow_huge', 'net_inflow_large',
                'net_inflow_medium', 'net_inflow_small'
            ]
            for col in amount_columns:
                if col in df.columns:
                    df[col] = df[col] / 10000

            # 补充必需字段
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'money_flow', df, source=self.source, target_table='stock_money_flow'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock money flow for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_shareholder_count(self, stock_code: str) -> Optional[dict]:
        """采集单只股票股东人数变动。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock shareholder count for {symbol}")
            df = await self._run_in_executor(ak.stock_zh_a_gdhs, symbol=symbol)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '代码': 'stock_code',
                '名称': 'stock_name',
                '最新价': 'price_at_end',
                '涨跌幅': 'price_change_ratio',
                '股东户数-本次': 'holder_count',
                '股东户数-上次': 'holder_count_prev',
                '股东户数-增减': 'holder_count_change',
                '股东户数-增减比例': 'holder_count_change_ratio',
                '区间涨跌幅': 'interval_return',
                '股东户数统计截止日-本次': 'end_date',
                '股东户数统计截止日-上次': 'prev_end_date',
                '户均持股市值': 'avg_hold_value',
                '户均持股数量': 'avg_hold_shares',
                '总市值': 'total_mv',
                '总股本': 'total_share',
                '公告日期': 'ann_date'
            }
            df.rename(columns=column_rename, inplace=True)

            # 补充必需字段
            df['stock_code'] = df['stock_code'].apply(lambda x: StockCodeStandardizer.standardize(x))
            df['data_source'] = self.source

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'shareholder_count', df, source=self.source, target_table='stock_shareholder_count'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock shareholder count for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_pledge_risk(self, stock_code: str) -> Optional[dict]:
        """采集单只股票股权质押明细数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock pledge risk for {symbol}")
            # 使用 stock_gpzy_pledge_ratio_detail_em (个股接口)
            df = await self._run_in_executor(ak.stock_gpzy_pledge_ratio_detail_em, symbol=symbol)
            if df is None or df.empty:
                logger.warning(f"No pledge data found for {stock_code}")
                return {"success": False, "data": [], "count": 0}
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'pledge_risk', df, source=self.source, target_table='stock_pledge_risk'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock pledge risk for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_all_pledge_summary(self, stock_code: str = None) -> Optional[dict]:
        """采集全市场股权质押汇总数据。注意：此接口返回全市场数据，不支持单股查询。"""
        try:
            if stock_code:
                logger.warning(f"AKShare pledge summary API does not support single stock query, will fetch all market data")
            logger.info("Fetching AKShare pledge summary for all market")

            # 使用 stock_gpzy_pledge_ratio_em（更快更全面，替代旧的 stock_gpzy_profile_em）
            df = await self._run_in_executor(ak.stock_gpzy_pledge_ratio_em)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '序号': 'sequence_number',
                '股票代码': 'stock_code',
                '股票简称': 'stock_name',
                '交易日期': 'trade_date',
                '所属行业': 'industry_name',
                '质押比例': 'pledge_ratio',
                '质押股数': 'pledged_shares',
                '质押市值': 'pledge_market_value',
                '质押笔数': 'pledge_count',
                '无限售股质押数': 'unrestricted_pledged_shares',
                '限售股质押数': 'restricted_pledged_shares',
                '近一年涨跌幅': 'yearly_return',
                '所属行业代码': 'industry_code'
            }
            df.rename(columns=column_rename, inplace=True)

            # 标准化股票代码
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'pledge_summary', df, source=self.source, target_table='stock_pledge_summary'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare pledge summary: {e}")
            return None

    async def fetch_and_ingest_stock_lockup_release(self, stock_code: str) -> Optional[dict]:
        """采集单只股票未来限售股解禁数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock lockup release for {symbol}")
            # 使用 stock_restricted_release_queue_em (个股接口)
            df = await self._run_in_executor(ak.stock_restricted_release_queue_em, symbol=symbol)
            if df is None or df.empty:
                logger.warning(f"No lockup release data found for {stock_code}")
                return {"success": False, "data": [], "count": 0}

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '解禁时间': 'release_date',
                '解禁数量': 'release_shares_original',
                '实际解禁数量': 'release_shares',
                '未解禁数量': 'unreleased_shares',
                '实际解禁数量市值': 'release_market_value',
                '占总市值比例': 'ratio_to_total',
                '占流通市值比例': 'ratio_to_float',
                '限售股类型': 'release_type'
            }
            df.rename(columns=column_rename, inplace=True)

            # 补充必需字段
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'lockup_release', df, source=self.source, target_table='stock_lockup_release'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock lockup release for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_earnings_forecast(self, stock_code: str = None) -> Optional[dict]:
        """采集业绩预告数据。

        注意：使用 stock_yjyg_em 接口获取全市场业绩预告数据。
        如果提供 stock_code，则筛选该股票的数据。
        """
        try:
            logger.info(f"Fetching AKShare earnings forecast")
            # 使用新接口 stock_yjyg_em 获取业绩预告数据
            df = await self._run_in_executor(ak.stock_yjyg_em)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '序号': 'sequence_number',
                '股票代码': 'stock_code',
                '股票简称': 'stock_name',
                '预测指标': 'forecast_indicator',
                '业绩变动': 'forecast_description',
                '预测数值': 'forecast_value',
                '业绩变动幅度': 'change_percent',
                '业绩变动原因': 'forecast_content',
                '预告类型': 'forecast_type',
                '上年同期值': 'prev_year_value',
                '公告日期': 'ann_date'
            }
            df.rename(columns=column_rename, inplace=True)

            # 标准化股票代码
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            # 单位转换：元 → 万元
            amount_columns = ['forecast_value', 'prev_year_value']
            for col in amount_columns:
                if col in df.columns:
                    df[col] = df[col] / 10000

            # 如果提供了股票代码，筛选该股票数据
            if stock_code:
                standardized_code = StockCodeStandardizer.standardize(stock_code)
                df = df[df['stock_code'] == standardized_code]
                if df.empty:
                    return None

            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'earnings_forecast', df, source=self.source, target_table='stock_earnings_forecast'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock earnings forecast for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_margin_data(self, stock_code: str) -> Optional[dict]:
        """采集单只股票融资融券明细数据。

        注意：AKShare 融资融券接口返回全市场数据，此方法会采集全市场数据后筛选指定股票。
        数据通常为 T-1 日数据（当日数据需要下一交易日才能获取）。
        """
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock margin data for {symbol}")

            # AKShare 融资融券接口按日期查询全市场数据
            # 上交所: stock_margin_detail_sse(date)
            # 深交所: stock_margin_detail_szse(date)
            # 使用昨天的日期（融资融券数据通常是 T-1）
            from datetime import datetime, timedelta
            date_str = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')

            if symbol.startswith('6'):
                # 上交所
                df = await self._run_in_executor(ak.stock_margin_detail_sse, date=date_str)
                # 列名映射
                column_rename = {
                    '信用交易日期': 'trade_date',
                    '标的证券代码': 'stock_code',
                    '标的证券简称': 'stock_name',
                    '融资余额': 'margin_balance',
                    '融资买入额': 'margin_buy_amount',
                    '融资偿还额': 'margin_repay_amount',
                    '融券余量': 'short_volume',
                    '融券卖出量': 'short_sell_volume',
                    '融券偿还量': 'short_repay_volume'
                }
            else:
                # 深交所
                df = await self._run_in_executor(ak.stock_margin_detail_szse, date=date_str)
                # 列名映射
                column_rename = {
                    '证券代码': 'stock_code',
                    '证券简称': 'stock_name',
                    '融资买入额': 'margin_buy_amount',
                    '融资余额': 'margin_balance',
                    '融券卖出量': 'short_sell_volume',
                    '融券余量': 'short_volume',
                    '融券余额': 'short_balance',
                    '融资融券余额': 'margin_short_balance'
                }
                df['trade_date'] = date_str

            if df is None or df.empty:
                return None

            df.rename(columns=column_rename, inplace=True)

            # 筛选指定股票
            df = df[df['stock_code'] == symbol].copy()
            if df.empty:
                logger.warning(f"No margin data found for {stock_code}")
                return {"success": False, "data": [], "count": 0}

            # 标准化股票代码
            df['stock_code'] = df['stock_code'].apply(lambda x: StockCodeStandardizer.standardize(x))
            df['data_source'] = self.source

            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'margin_data', df, source=self.source, target_table='stock_margin_data'
            )

            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare stock margin data for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_limit_up_pool(self, trade_date: str = None) -> Optional[dict]:
        """采集每日涨停池数据。"""
        try:
            date_str = trade_date.replace('-', '') if trade_date else datetime.now().strftime('%Y%m%d')
            logger.info(f"Fetching AKShare limit up pool for {date_str}")
            df = await self._run_in_executor(ak.stock_zt_pool_em, date=date_str)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '序号': 'sequence_number',
                '代码': 'stock_code',
                '名称': 'stock_name',
                '涨跌幅': 'pct_chg',
                '最新价': 'limit_up_price',
                '成交额': 'turnover',
                '流通市值': 'circ_mv',
                '总市值': 'total_mv',
                '换手率': 'turnover_rate',
                '封板资金': 'fund_amount',
                '首次封板时间': 'first_limit_up_time',
                '最后封板时间': 'last_limit_up_time',
                '炸板次数': 'open_times',
                '涨停统计': 'limit_up_stats',
                '连板数': 'limit_up_days',
                '所属行业': 'limit_up_reason'
            }
            df.rename(columns=column_rename, inplace=True)

            # 标准化股票代码
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            df['data_source'] = self.source
            df['update_date'] = pd.to_datetime(date_str).date()
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'limit_up_pool', df, source=self.source, target_table='stock_limit_up_pool'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare limit up pool: {e}")
            return None

    async def fetch_and_ingest_stock_limit_down_pool(self, trade_date: str = None) -> Optional[dict]:
        """采集每日跌停池数据。"""
        try:
            date_str = trade_date.replace('-', '') if trade_date else datetime.now().strftime('%Y%m%d')
            logger.info(f"Fetching AKShare limit down pool for {date_str}")
            df = await self._run_in_executor(ak.stock_zt_pool_dtgc_em, date=date_str)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '序号': 'sequence_number',
                '代码': 'stock_code',
                '名称': 'stock_name',
                '涨跌幅': 'pct_chg',
                '最新价': 'limit_down_price',
                '成交额': 'turnover',
                '流通市值': 'circ_mv',
                '总市值': 'total_mv',
                '动态市盈率': 'dynamic_pe',
                '换手率': 'turnover_rate',
                '封单资金': 'fund_amount',
                '最后封板时间': 'last_limit_down_time',
                '板上成交额': 'board_turnover',
                '连续跌停': 'limit_down_days',
                '开板次数': 'open_times',
                '所属行业': 'limit_down_reason'
            }
            df.rename(columns=column_rename, inplace=True)

            # 标准化股票代码
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            df['data_source'] = self.source
            df['update_date'] = pd.to_datetime(date_str).date()
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'limit_down_pool', df, source=self.source, target_table='stock_limit_down_pool'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare limit down pool: {e}")
            return None

    async def fetch_and_ingest_stock_zhaban_pool(self, trade_date: str = None) -> Optional[dict]:
        """采集每日炸板池数据。"""
        try:
            date_str = trade_date.replace('-', '') if trade_date else datetime.now().strftime('%Y%m%d')
            logger.info(f"Fetching AKShare zhaban pool for {date_str}")
            df = await self._run_in_executor(ak.stock_zt_pool_zbgc_em, date=date_str)
            if df is None or df.empty:
                return None

            # 列名映射：AKShare 中文列名 -> 数据库英文字段
            column_rename = {
                '序号': 'sequence_number',
                '代码': 'stock_code',
                '名称': 'stock_name',
                '涨跌幅': 'pct_chg',
                '最新价': 'latest_price',
                '涨停价': 'limit_up_price',
                '成交额': 'turnover',
                '流通市值': 'circ_mv',
                '总市值': 'total_mv',
                '换手率': 'turnover_rate',
                '涨速': 'speed_increase',
                '首次封板时间': 'first_limit_up_time',
                '炸板次数': 'open_times',
                '涨停统计': 'limit_up_stats',
                '振幅': 'swing',
                '所属行业': 'limit_up_reason'
            }
            df.rename(columns=column_rename, inplace=True)

            # 标准化股票代码
            if 'stock_code' in df.columns:
                df['stock_code'] = df['stock_code'].apply(StockCodeStandardizer.standardize)

            df['data_source'] = self.source
            df['update_date'] = pd.to_datetime(date_str).date()
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'zhaban_pool', df, source=self.source, target_table='stock_zhaban_pool'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare zhaban pool: {e}")
            return None

    async def fetch_and_ingest_stock_insider_trading(self, stock_code: str) -> Optional[dict]:
        """采集高管及相关人员持股变动数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare insider trading for {symbol}")
            # 使用 stock_shareholder_change_ths (同花顺股东变动)
            df = await self._run_in_executor(ak.stock_shareholder_change_ths, symbol=symbol)
            if df is None or df.empty:
                return None
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'insider_trading', df, source=self.source, target_table='stock_insider_trading'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare insider trading for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_block_trade(
        self, stock_code: str, start_date: str = None, end_date: str = None
    ) -> Optional[dict]:
        """采集个股或全市场大宗交易数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code) if stock_code else None
            logger.info(f"Fetching AKShare block trade for {symbol or 'all'}")
            df = await self._run_in_executor(ak.stock_dzjy_mrmx, symbol=symbol or "全部")
            if df is None or df.empty:
                return None
            if stock_code:
                df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'block_trade', df, source=self.source, target_table='stock_block_trade'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare block trade: {e}")
            return None

    async def fetch_and_ingest_sector_money_flow(self, stock_code: str) -> Optional[dict]:
        """采集股票所属行业的板块资金流。"""
        try:
            logger.info(f"Fetching AKShare sector money flow for {stock_code}")
            df = await self._run_in_executor(ak.stock_sector_fund_flow_rank, indicator="今日")
            if df is None or df.empty:
                return None
            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'sector_money_flow', df, source=self.source, target_table='sector_money_flow'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare sector money flow: {e}")
            return None

    async def fetch_and_ingest_stock_top_holders(self, stock_code: str) -> Optional[dict]:
        """采集股票前十大股东数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare top holders for {symbol}")
            df = await self._run_in_executor(ak.stock_gdfx_holding_analyse, symbol=symbol)
            if df is None or df.empty:
                return None
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'top_holders', df, source=self.source, target_table='stock_top_holders'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare top holders for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_stock_interactive_qa(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Optional[dict]:
        """采集上证或深证互动问答数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare interactive QA for {symbol}")
            # 使用 stock_irm_cninfo (互动易)
            df = await self._run_in_executor(ak.stock_irm_cninfo, symbol=symbol)
            if df is None or df.empty:
                return None
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
            df['data_source'] = self.source
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'interactive_qa', df, source=self.source, target_table='stock_interactive_qa'
            )
            
            # 返回字典格式
            return {
                "success": True,
                "data": df.to_dict('records'),
                "count": len(df)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare interactive QA for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_income_statement(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> Optional[dict]:
        """采集单只股票利润表。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare income statement for {symbol}")
            df = await self._run_in_executor(
                ak.stock_financial_benefit_ths,
                symbol=symbol,
                indicator="按报告期",
                cache_ttl=3600,
            )
            if df is None or df.empty:
                logger.warning(f"No income statement found for {stock_code}")
                return {"success": False, "data": [], "count": 0}
            records = self._normalize_ths_statement_records(
                df,
                stock_code,
                start_date,
                end_date,
                report_type="akshare_ths_income_statement",
            )
            return {
                "success": bool(records),
                "data": records,
                "count": len(records)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare income statement for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_balance_sheet(
            self,
            stock_code: str,
            start_date: str,
            end_date: str) -> Optional[dict]:
        """采集单只股票资产负债表。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare balance sheet for {symbol}")
            df = await self._run_in_executor(
                ak.stock_financial_debt_ths,
                symbol=symbol,
                indicator="按报告期",
                cache_ttl=3600,
            )
            if df is None or df.empty:
                logger.warning(f"No balance sheet found for {stock_code}")
                return {"success": False, "data": [], "count": 0}
            records = self._normalize_ths_statement_records(
                df,
                stock_code,
                start_date,
                end_date,
                report_type="akshare_ths_balance_sheet",
            )
            return {
                "success": bool(records),
                "data": records,
                "count": len(records)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare balance sheet for {stock_code}: {e}")
            return None

    async def fetch_and_ingest_cashflow_statement(
            self,
            stock_code: str,
            start_date: str,
            end_date: str) -> Optional[dict]:
        """采集单只股票现金流量表。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare cashflow statement for {symbol}")
            df = await self._run_in_executor(
                ak.stock_financial_cash_ths,
                symbol=symbol,
                indicator="按报告期",
                cache_ttl=3600,
            )
            if df is None or df.empty:
                logger.warning(f"No cashflow statement found for {stock_code}")
                return {"success": False, "data": [], "count": 0}
            records = self._normalize_ths_statement_records(
                df,
                stock_code,
                start_date,
                end_date,
                report_type="akshare_ths_cashflow_statement",
            )
            return {
                "success": bool(records),
                "data": records,
                "count": len(records)
            }
        except Exception as e:
            logger.error(f"Failed to ingest AKShare cashflow statement for {stock_code}: {e}")
            return None
