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
from app.data.ingestors.plugins.column_mapping import ColumnMapper
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
            numeric_cols = ['open', 'close', 'high', 'low', 'volume', 'amount']
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

    async def fetch_and_ingest_realtime_market(self, stock_code: str) -> Optional[dict]:
        """
        采集单只股票实时行情并写入实时行情表。

        官方文档:
            - stock_zh_a_spot: https://akshare.akfamily.xyz/data/stock/stock.html#id4

        Args:
            stock_code: 股票代码。

        Returns:
            采集并写入有效价格行情成功返回 True；无数据或异常返回 False。
        """
        try:
            # 准备查询代码（使用 6 位数字代码）
            symbol = StockCodeStandardizer.to_number(stock_code)

            logger.info(f"Fetching AKShare realtime market for {symbol}")

            # 调用 AKShare 接口（使用已验证可用的 stock_zh_a_spot）
            df = await self._run_in_executor(ak.stock_zh_a_spot)

            if df is None or df.empty:
                logger.warning("No realtime data returned from AKShare")
                return {"success": False, "data": [], "count": 0}

            # 筛选目标股票（通过股票名称或代码匹配）
            # 尝试通过代码匹配
            target_df = df[df['代码'].str.contains(symbol, na=False)].copy()

            if target_df.empty:
                logger.warning(f"No realtime data found for {stock_code} ({symbol}) in AKShare spot data")
                return None

            # 只取第一条匹配记录
            target_df = target_df.head(1)

            # 列名映射（对齐 Tushare 的 realtime_market 字段）
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
            target_df.rename(columns=column_rename, inplace=True)

            # 标准化股票代码（添加市场后缀）
            target_df['stock_code'] = target_df['stock_code'].apply(StockCodeStandardizer.standardize)

            # 补充字段
            target_df['data_source'] = self.source
            target_df['timestamp'] = pd.Timestamp.now()

            # 数值转换
            numeric_cols = ['current_price', 'change_percent', 'change_amount', 'volume',
                          'turnover', 'high', 'low', 'open', 'pre_close', 'bid', 'ask']
            for col in numeric_cols:
                if col in target_df.columns:
                    target_df[col] = pd.to_numeric(target_df[col], errors='coerce')

            # 过滤无效价格
            if 'current_price' in target_df.columns:
                target_df = target_df[target_df['current_price'] > 0].copy()
                if target_df.empty:
                    logger.warning(f"Invalid price for {stock_code} in AKShare realtime data")
                return {"success": False, "data": [], "count": 0}

            # 使用 ColumnMapper 映射到标准表（与 Tushare 对齐）
            target_df = ColumnMapper.map_columns(
                target_df,
                'data.stock_realtime_market',
                source=self.source,
                strict=False
            )

            # 写入数据库
            await self._run_in_executor(
                self.ingestion_service.write_dataframe,
                'stock_zh_a_spot', target_df, source=self.source, target_table='data.stock_realtime_market'
            )

            logger.info(f"Successfully ingested AKShare realtime market for {stock_code}")
            
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

            # 调用 AKShare 接口
            df = await self._run_in_executor(
                ak.stock_financial_analysis_indicator,
                symbol=symbol,
                start_year=start_year,
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
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
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
            # 使用 stock_gpzy_profile_em (仅全市场)
            df = await self._run_in_executor(ak.stock_gpzy_profile_em)
            if df is None or df.empty:
                return None
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

    async def fetch_and_ingest_stock_earnings_forecast(self, stock_code: str) -> Optional[dict]:
        """采集单只股票业绩预告数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock earnings forecast for {symbol}")
            df = await self._run_in_executor(ak.stock_yysj_em, symbol=symbol)
            if df is None or df.empty:
                return None
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
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
        """采集单只股票融资融券明细数据。"""
        try:
            symbol = StockCodeStandardizer.to_number(stock_code)
            logger.info(f"Fetching AKShare stock margin data for {symbol}")
            df = await self._run_in_executor(ak.stock_margin_detail_em, symbol=symbol)
            if df is None or df.empty:
                return None
            df['stock_code'] = StockCodeStandardizer.standardize(stock_code)
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
            df = await self._run_in_executor(ak.stock_financial_benefit_ths, symbol=symbol, indicator="按报告期")
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
            df = await self._run_in_executor(ak.stock_financial_debt_ths, symbol=symbol, indicator="按报告期")
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
            df = await self._run_in_executor(ak.stock_financial_cash_ths, symbol=symbol, indicator="按报告期")
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
