from abc import ABC
from typing import Any, List, Optional
import asyncio
import hashlib
import io
import json
import http.client
import pandas as pd
import requests
import urllib3
from app.core.config import settings
from app.core.logger import get_logger
from app.core.utils.backoff import backoff
from app.data.ingestors.rate_limiter import LeakyBucketRateLimiter

logger = get_logger(__name__)


class BaseIngestor(ABC):
    """
    数据采集基类，定义所有数据源必须实现的接口。
    Base class for data ingestion, defining interfaces that all data sources must implement.
    All methods are ASYNC to ensure non-blocking execution in the main thread.
    """

    source_name: str = ""
    display_name: str = ""
    required_settings: tuple[str, ...] = ()

    @classmethod
    def get_source_name(cls) -> str:
        """
        获取标准化后的数据源名称。

        Returns:
            小写数据源名称。
        """
        if cls.source_name:
            return cls.source_name.lower()
        return cls.__name__.replace("Ingestor", "").lower()

    @classmethod
    def get_display_name(cls) -> str:
        """
        获取展示用数据源名称。

        Returns:
            展示名称。
        """
        return cls.display_name or cls.get_source_name()

    def validate_config(self) -> List[str]:
        """
        校验当前插件所需配置是否完整。

        Returns:
            缺失的配置项列表。
        """
        missing_settings: List[str] = []
        for setting_name in self.required_settings:
            if getattr(settings, setting_name, None):
                continue
            missing_settings.append(setting_name)
        return missing_settings

    def is_available(self) -> bool:
        """
        判断当前插件在运行时是否可用。

        Returns:
            配置完整时返回 True。
        """
        return not self.validate_config()

    def get_metadata(self) -> dict[str, Any]:
        """
        获取数据源插件元数据。

        Returns:
            可用于 API 返回和管理界面的元数据字典。
        """
        return {
            "source_name": self.get_source_name(),
            "display_name": self.get_display_name(),
            "required_settings": list(self.required_settings),
            "missing_settings": self.validate_config(),
            "available": self.is_available(),
        }

    def _get_func_name(self, func) -> str:
        """
        获取函数的名称，支持 functools.partial 对象。
        Get the name of the function, supporting functools.partial objects.
        """
        if hasattr(func, '__name__'):
            return func.__name__
        if hasattr(func, 'func') and hasattr(func.func, '__name__'):
            # 处理 functools.partial
            return func.func.__name__
        return str(func)

    def _generate_cache_key(self, func, args, kwargs) -> str:
        """
        生成缓存键
        Generate cache key
        """
        # Use JSON dump with sort_keys=True for deterministic hashing of dicts
        try:
             # Helper to handle non-serializable objects (like functions/classes)
            def default_serializer(obj):
                if hasattr(obj, '__name__'):
                    return obj.__name__
                return str(obj)

            arg_data = {
                "args": args,
                "kwargs": kwargs
            }
            
            # If func is a partial, include its bound arguments in the cache key
            if hasattr(func, 'func') and hasattr(func, 'args') and hasattr(func, 'keywords'):
                arg_data['partial_args'] = func.args
                arg_data['partial_kwargs'] = func.keywords
                
            arg_str = json.dumps(arg_data, sort_keys=True, default=default_serializer, ensure_ascii=False)
        except Exception:
            # Fallback to string representation if json fails
            arg_str = f"{args}-{kwargs}"
        
        arg_hash = hashlib.md5(arg_str.encode('utf-8')).hexdigest()
        source_name = getattr(self, 'source', 'unknown')
        func_name = self._get_func_name(func)
        return f"{source_name}:{func_name}:{arg_hash}"

    @staticmethod
    def _build_record_hash(parts: List[Optional[str]]) -> str:
        """Build a stable business hash for dedup/upsert keys."""
        normalized = []
        for part in parts:
            if part is None:
                normalized.append("")
                continue
            if isinstance(part, float) and pd.isna(part):
                normalized.append("")
                continue
            normalized.append(str(part).strip())
        payload = "||".join(normalized)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @backoff(max_tries=5, base_delay=2.0, max_delay=60.0, backoff_type='exponential',
             retry_on=(requests.exceptions.RequestException,
                       requests.exceptions.SSLError,
                       requests.exceptions.ChunkedEncodingError,
                       requests.exceptions.ConnectionError,
                       requests.exceptions.Timeout,
                       urllib3.exceptions.ProtocolError,
                       urllib3.exceptions.HTTPError,
                       urllib3.exceptions.MaxRetryError,
                       urllib3.exceptions.IncompleteRead,
                       http.client.RemoteDisconnected,
                       http.client.IncompleteRead,
                       ConnectionError,
                       TimeoutError))
    async def _run_in_executor(self, func, *args, use_cache: bool = True, cache_ttl: int = 60, **kwargs):
        """
        Helper to run blocking API calls in a thread pool with optional Redis caching.
        在线程池中运行阻塞式 API 调用，支持可选的 Redis 缓存。

        子类可以通过重写此方法来添加限流逻辑。

        Args:
            func: The blocking function to execute (阻塞函数)
            *args: Positional arguments (位置参数)
            use_cache: Whether to use Redis cache (是否使用 Redis 缓存, default: True)
            cache_ttl: Cache time to live in seconds (缓存过期时间，默认 60 秒)
            **kwargs: Keyword arguments (关键字参数)

        Returns:
            Function result (usually pd.DataFrame) (函数结果，通常为 DataFrame)
        """
        from app.core.redis_client import redis_client

        # 1. Try to get from cache
        cache_key = None
        if use_cache:
            try:
                cache_key = self._generate_cache_key(func, args, kwargs)

                cached_val = await redis_client.get(cache_key)
                if cached_val:
                    try:
                        # Try to parse as structured cache object
                        cache_obj = json.loads(cached_val)
                        
                        # Check validity of structure
                        if isinstance(cache_obj, dict) and "type" in cache_obj and "data" in cache_obj:
                            data_type = cache_obj["type"]
                            data_content = cache_obj["data"]

                            if data_type == "dataframe":
                                logger.info(f"Hit cache (DataFrame) for {self._get_func_name(func)}")
                                # data_content is the serialized DF string
                                return pd.read_json(io.StringIO(data_content), orient='split', convert_dates=False)
                            
                            elif data_type == "full_response":
                                logger.info(f"Hit cache (Response) for {self._get_func_name(func)}")
                                # data_content is the JSON dict
                                # Reconstruct a requests.Response object
                                resp = requests.Response()
                                resp.status_code = 200
                                # Set _content to bytes of JSON string
                                resp._content = json.dumps(data_content).encode('utf-8')
                                return resp
                        else:
                            # Accessing legacy string cache (Backward compatibility or ignore)
                            # Let's try to interpret as DataFrame for legacy cache if it looks like one, 
                            # or just ignore to enforce new structure. 
                            # User said "explicitly determine data type", so better to stick to new format.
                            pass

                    except Exception:
                        # JSON decode error or other issue
                        pass

            except Exception as e:
                logger.warning(f"Cache read failed for {self._get_func_name(func)}: {e}")

        # 2. Run actual function
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(None, lambda: func(*args, **kwargs))
        except ValueError as e:
            # Handle common "no data" error which manifests as a Length mismatch
            # when libraries try to assign columns to an empty DataFrame internally.
            if "Length mismatch" in str(e) and "0 elements" in str(e):
                logger.warning(f"API interface {self._get_func_name(func)} returned no data (Handled Length mismatch)")
                return pd.DataFrame()
            raise

        # 3. Cache the result if enabled
        if use_cache and cache_key:
            try:
                cache_obj = None

                # Case A: DataFrame
                if isinstance(result, pd.DataFrame) and not result.empty:
                    # Serialize DataFrame to JSON string
                    df_json_str = result.to_json(orient='split', date_format='iso')
                    cache_obj = {
                        "type": "dataframe",
                        "data": df_json_str
                    }
                
                # Case B: requests.Response
                elif isinstance(result, requests.Response):
                    if result.ok:
                        try:
                            json_data = result.json()
                            if json_data:
                                cache_obj = {
                                    "type": "full_response",
                                    "data": json_data
                                }
                        except Exception:
                            pass
                
                if cache_obj:
                    # Store as JSON string
                    await redis_client.set(cache_key, json.dumps(cache_obj), expire=cache_ttl)
                    logger.debug(
                        "cached executor result",
                        extra={
                            "cache_type": cache_obj["type"],
                            "cache_func": self._get_func_name(func),
                            "cache_ttl_seconds": cache_ttl,
                        },
                    )

            except Exception as e:
                logger.warning(f"Cache write failed for {self._get_func_name(func)}: {e}")

        return result

    async def fetch_and_ingest_stock_kline(
            self,
            stock_code: str,
            start_date: str,
            end_date: str,
            period: str = "daily",
            adjust: str = "qfq") -> bool:
        """
        采集日线行情
        Collect daily stock quotes
        """
        return False

    async def fetch_and_ingest_index_daily(
            self,
            index_code: str,
            start_date: str,
            end_date: str) -> bool:
        """
        采集指数日线行情
        Collect daily index quotes
        """
        return False

    async def fetch_and_ingest_stock_info(self, stock_code: str) -> bool:
        """
        采集股票基本信息
        Collect stock basic information
        """
        return False

    async def fetch_and_ingest_all_stock_basic(self) -> bool:
        """全量同步 A 股基础信息。"""
        return False

    async def fetch_and_ingest_stock_valuation(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> bool:
        """
        采集估值数据 (PE/PB等历史)
        Collect valuation data (PE/PB history, etc.)
        """
        return False

    async def fetch_and_ingest_realtime_market(self, stock_code: str) -> bool:
        """
        采集实时行情
        Collect real-time market data
        """
        return False

    async def fetch_and_ingest_financial_indicators(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> bool:
        """
        采集财务指标
        Collect financial indicators
        """
        return False

    async def fetch_and_ingest_income_statement(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        """采集利润表。"""
        return False

    async def fetch_and_ingest_balance_sheet(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        """采集资产负债表。"""
        return False

    async def fetch_and_ingest_cashflow_statement(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        """采集现金流量表。"""
        return False

    async def fetch_and_ingest_northbound(self, stock_code: str) -> bool:
        """
        采集北向资金数据
        Collect Northbound capital data
        """
        return False

    async def fetch_and_ingest_company_profile(self, stock_code: str) -> bool:
        """
        采集公司资料
        Collect company profile
        """
        return False

    # --- 以下为更多特定数据接口，可选实现或默认返回 False ---

    async def fetch_and_ingest_stock_limit_up_pool(self, date: Optional[str] = None) -> bool:
        """采集涨停池"""
        return False

    async def fetch_and_ingest_stock_limit_down_pool(self, date: Optional[str] = None) -> bool:
        """采集跌停池"""
        return False

    async def fetch_and_ingest_stock_zhaban_pool(self, date: Optional[str] = None) -> bool:
        """采集炸板池"""
        return False

    async def fetch_and_ingest_stock_money_flow(self, stock_code: str) -> bool:
        """采集资金流向"""
        return False

    async def fetch_and_ingest_sector_money_flow(self, stock_code: str) -> bool:
        """采集板块资金流向"""
        return False

    async def fetch_and_ingest_stock_shareholder_count(self, stock_code: str) -> bool:
        """采集股东户数"""
        return False

    async def fetch_and_ingest_stock_pledge_risk(self, stock_code: str) -> bool:
        """采集股权质押风险"""
        return False

    async def fetch_and_ingest_all_pledge_summary(self, stock_code: Optional[str] = None) -> bool:
        """同步股权质押汇总数据"""
        return False

    async def fetch_and_ingest_stock_insider_trading(self, stock_code: str) -> bool:
        """采集内部交易"""
        return False

    async def fetch_and_ingest_stock_lockup_release(self, stock_code: str) -> bool:
        """采集限售解禁"""
        return False

    async def fetch_and_ingest_stock_margin_data(self, stock_code: str) -> bool:
        """采集融资融券"""
        return False

    async def fetch_and_ingest_stock_block_trade(
        self, stock_code: str, start_date: str = None, end_date: str = None
    ) -> bool:
        """采集大宗交易 (Fetch block trade data)"""
        return False

    async def fetch_and_ingest_board_industry(self) -> bool:
        """采集行业板块"""
        return False

    async def fetch_and_ingest_dragon_tiger(self, start_date: str, end_date: Optional[str] = None) -> bool:
        """采集龙虎榜数据"""
        return False

    async def fetch_and_ingest_stock_top_holders(self, stock_code: str) -> bool:
        """同步股票十大股东数据"""
        return False

    async def fetch_and_ingest_stock_announcements(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        """同步股票公告数据。"""
        return False
