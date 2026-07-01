import asyncio
from typing import Dict, List, Optional

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.data.ingestors.base_ingestor import BaseIngestor
from app.data.ingestors.plugin_loader import (
    instantiate_ingestor_plugins,
    validate_ingestor_registration,
)
from app.models.data_storage import StockBasic
from app.models.stock_warehouse import StockWarehouse

logger = get_logger(__name__)


class IngestorManager(BaseIngestor):

    """
    数据采集管理器，负责管理所有采集器实例，并提供统一的采集入口和灾备切换功能。
    继承 BaseIngestor，对外提供一致的接口。
    """
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(IngestorManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.ingestors: Dict[str, BaseIngestor] = {}
        # Default source is now strictly from settings (env)
        self.default_source = settings.DEFAULT_DATA_SOURCE
        logger.info(f"Loaded default data source from settings: {self.default_source}")

        self._register_ingestors()
        self._initialized = True

    def _register_ingestors(self):
        """注册所有可用的采集器"""
        ingestors = instantiate_ingestor_plugins()

        for ingestor in ingestors:
            try:
                self.register_ingestor(ingestor.get_source_name(), ingestor)
            except ValueError as e:
                logger.error("Failed to register ingestor %s: %s", ingestor.get_source_name(), e)

    def register_ingestor(self, name: str, ingestor: BaseIngestor):
        normalized_name = ingestor.get_source_name()
        validate_ingestor_registration(ingestor, set(self.ingestors))
        self.ingestors[normalized_name] = ingestor
        logger.info(f"Registered ingestor: {normalized_name}")

    def get_ingestor(self, name: str) -> Optional[BaseIngestor]:
        return self.ingestors.get(name.lower())

    def set_default_source(self, source_name: str) -> bool:
        """设置默认数据源，并更新到 .env"""
        from app.core.env_manager import env_manager

        name = source_name.lower()
        if name in self.ingestors:
            self.default_source = name

            # Persist to .env
            if env_manager.set_key("DEFAULT_DATA_SOURCE", name):
                logger.info(f"Default data source set to {name} and saved to .env")
                # Update settings in memory as well to reflect change immediately if accessed via settings
                settings.DEFAULT_DATA_SOURCE = name
                return True
            else:
                logger.error(f"Failed to save default data source {name} to .env")
                # Even if env save fails, we update runtime? No, safer to fail or warn.
                # But for now let's return True as runtime change happened, but warn.
                return True

        logger.warning(f"Data source not found: {name}")
        return False

    def list_data_sources(self) -> List[str]:
        """获取所有注册的数据源名称"""
        return list(self.ingestors.keys())

    def list_data_source_details(self) -> List[dict]:
        """获取所有注册数据源的详细元数据。"""
        return [ingestor.get_metadata() for ingestor in self.ingestors.values()]

    def get_prioritized_sources(self) -> List[str]:
        """获取按优先级排序的数据源列表"""
        priority_list = []

        if self.default_source in self.ingestors:
            priority_list.append(self.default_source)
        if "tushare" not in priority_list and "tushare" in self.ingestors:
            priority_list.append("tushare")

        for name in self.ingestors.keys():
            if name not in priority_list:
                priority_list.append(name)

        logger.debug(
            "prioritized data sources resolved",
            extra={
                "priority_list": priority_list,
                "registered_sources": list(self.ingestors.keys()),
            },
        )
        return priority_list

    async def _execute_with_failover(self, method_name: str, *args, **kwargs) -> bool:
        """
        统一的灾备执行逻辑 (Unified Failover Execution Logic)
        Combines logic for both sync and async underlying methods (though BaseIngestor is now all async).
        """
        priority_list = self.get_prioritized_sources()

        # Check if failover is enabled
        if not settings.ENABLE_DATA_SOURCE_FAILOVER:
            # If failover is disabled, only use the first prioritized source (the default one).
            # We still get the prioritized list to find which is the primary one,
            # but we truncate it to 1.
            # Assuming get_prioritized_sources puts the default source first.
            if priority_list:
                priority_list = [priority_list[0]]
                logger.debug(
                    "failover disabled; restricting to primary source",
                    extra={
                        "method_name": method_name,
                        "primary_source": priority_list[0],
                    },
                )

        context_extra = {}
        if args:
            context_extra["first_arg"] = args[0]
        elif 'stock_code' in kwargs:
            context_extra["stock_code"] = kwargs["stock_code"]

        for source_name in priority_list:
            ingestor = self.get_ingestor(source_name)
            if not ingestor:
                continue
            method = getattr(ingestor, method_name, None)
            if not method:
                logger.warning(
                    "ingestor method is not implemented",
                    extra={
                        "source_name": source_name,
                        "method_name": method_name,
                    },
                )
                continue

            logger.info(
                "attempting ingestor method",
                extra={
                    "method_name": method_name,
                    "source_name": source_name,
                    **context_extra,
                },
            )
            try:
                # Since BaseIngestor methods are now all async def, we can simply await.
                # If dynamic registration allows sync methods, we check.
                if asyncio.iscoroutinefunction(method):
                    result = await method(*args, **kwargs)
                else:
                    # Fallback for sync methods (should be rare if obeying BaseIngestor)
                    # Wrap in executor to avoid blocking main thread if it's heavy
                    loop = asyncio.get_running_loop()
                    result = await loop.run_in_executor(None, lambda: method(*args, **kwargs))

                if result is not None and result is not False:
                    logger.debug(
                        "ingestor method succeeded",
                        extra={
                            "method_name": method_name,
                            "source_name": source_name,
                        },
                    )
                    return result
                else:
                    logger.warning(f"[{method_name}] Failed using {source_name} (returned {result})")
            except Exception as e:
                logger.exception(f"[{method_name}] Exception using {source_name}: {e}")

        logger.error(f"[{method_name}] All sources failed. priority_list was: {priority_list}")
        return False

    # --- Implementing BaseIngestor Interface (All Async) ---

    async def fetch_and_ingest_stock_info(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_info', stock_code)

    async def fetch_and_ingest_stock_kline(
        self,
        stock_code: str,
        start_date: str,
        end_date: str,
        period: str = "daily",
        adjust: str = "qfq",
    ) -> bool:
        return await self._execute_with_failover(
            'fetch_and_ingest_stock_kline',
            stock_code,
            start_date,
            end_date,
            period=period,
            adjust=adjust,
        )

    async def fetch_and_ingest_stock_valuation(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_valuation', stock_code, start_date, end_date)

    async def fetch_and_ingest_realtime_market(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_realtime_market', stock_code)

    async def fetch_and_ingest_financial_indicators(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> bool:
        return await self._execute_with_failover(
            'fetch_and_ingest_financial_indicators',
            stock_code,
            start_date,
            end_date,
        )

    async def fetch_and_ingest_income_statement(
            self,
            stock_code: str,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None) -> bool:
        return await self._execute_with_failover(
            'fetch_and_ingest_income_statement',
            stock_code,
            start_date,
            end_date,
        )

    async def fetch_and_ingest_balance_sheet(
            self,
            stock_code: str,
            start_date: str,
            end_date: str) -> bool:
        return await self._execute_with_failover(
            'fetch_and_ingest_balance_sheet',
            stock_code,
            start_date,
            end_date,
        )

    async def fetch_and_ingest_cashflow_statement(
            self,
            stock_code: str,
            start_date: str,
            end_date: str) -> bool:
        return await self._execute_with_failover(
            'fetch_and_ingest_cashflow_statement',
            stock_code,
            start_date,
            end_date,
        )

    async def fetch_and_ingest_northbound(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_northbound', stock_code)

    async def fetch_and_ingest_company_profile(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_company_profile', stock_code)

    async def fetch_and_ingest_all_stock_basic(self) -> bool:
        """
        全量同步 A 股基础信息
        """
        return await self._execute_with_failover('fetch_and_ingest_all_stock_basic')

    # Optional methods

    async def fetch_and_ingest_stock_limit_up_pool(self, date: Optional[str] = None) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_limit_up_pool', date)

    async def fetch_and_ingest_stock_limit_down_pool(self, date: Optional[str] = None) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_limit_down_pool', date)

    async def fetch_and_ingest_stock_zhaban_pool(self, date: Optional[str] = None) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_zhaban_pool', date)

    async def fetch_and_ingest_all_pledge_summary(self, stock_code: Optional[str] = None) -> bool:
        """同步全量股权质押汇总数据"""
        return await self._execute_with_failover('fetch_and_ingest_all_pledge_summary', stock_code)

    async def fetch_and_ingest_stock_money_flow(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_money_flow', stock_code)

    async def fetch_and_ingest_sector_money_flow(self, stock_code: str) -> bool:
        """同步板块资金流数据"""
        return await self._execute_with_failover('fetch_and_ingest_sector_money_flow', stock_code)

    async def fetch_and_ingest_stock_shareholder_count(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_shareholder_count', stock_code)

    async def fetch_and_ingest_stock_pledge_risk(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_pledge_risk', stock_code)

    async def fetch_and_ingest_stock_insider_trading(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_insider_trading', stock_code)

    async def fetch_and_ingest_stock_lockup_release(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_lockup_release', stock_code)

    async def fetch_and_ingest_stock_margin_data(self, stock_code: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_stock_margin_data', stock_code)

    async def fetch_and_ingest_stock_block_trade(
        self, stock_code: str, start_date: str = None, end_date: str = None
    ) -> bool:
        return await self._execute_with_failover(
            'fetch_and_ingest_stock_block_trade', stock_code, start_date, end_date
        )

    async def fetch_and_ingest_dragon_tiger(self, start_date: str, end_date: Optional[str] = None) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_dragon_tiger', start_date, end_date)

    async def fetch_and_ingest_board_industry(self) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_board_industry')

    async def fetch_and_ingest_index_daily(self, index_code: str, start_date: str, end_date: str) -> bool:
        return await self._execute_with_failover('fetch_and_ingest_index_daily', index_code, start_date, end_date)

    async def fetch_and_ingest_stock_top_holders(self, stock_code: str) -> bool:
        """
        同步股票十大股东数据
        Sync top 10 shareholders data for a specific stock
        """
        return await self._execute_with_failover('fetch_and_ingest_stock_top_holders', stock_code)

    async def fetch_and_ingest_stock_announcements(
        self,
        stock_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> bool:
        """
        同步股票公告数据。
        """
        return await self._execute_with_failover(
            'fetch_and_ingest_stock_announcements',
            stock_code,
            start_date,
            end_date,
        )

    # --- High-Level Synchronization Methods (Sync All) ---

    def _get_all_stock_codes(self) -> List[str]:
        """获取股票仓库中所有活跃的股票代码"""
        with SessionLocal() as db:
            codes = db.query(StockWarehouse.stock_code)\
                .filter(StockWarehouse.is_active.is_(True))\
                .distinct().all()
            return [c.stock_code for c in codes]

    def _get_all_stock_codes_from_stock_basic(self) -> List[str]:
        """获取 stock_basic 中所有股票代码（全量，不过滤仓库）
        Get all stock codes from stock_basic (all stocks, not warehouse-filtered)
        """
        with SessionLocal() as db:
            codes = db.query(StockBasic.stock_code)\
                .distinct().all()
            return [c.stock_code for c in codes]

    async def sync_all_stock_basics(self) -> bool:
        """全量同步 A 股基础信息 (包括公司概况)"""
        logger.info("Starting Sync All: Stock Basics")
        # 1. Basic info
        await self.fetch_and_ingest_all_stock_basic()
        # 2. Company profiles and expanded info (industry/market)
        stock_codes = self._get_all_stock_codes()
        logger.info(f"Syncing info/profiles for {len(stock_codes)} warehouse stocks")
        for i, code in enumerate(stock_codes):
            # Fetch basic info (industry/market) if not fully populated by step 1
            await self.fetch_and_ingest_stock_info(code)
            # Fetch full company profile
            await self.fetch_and_ingest_company_profile(code)
            if (i + 1) % 20 == 0:
                logger.info(f"Progress: {i + 1}/{len(stock_codes)}")
        return True

    async def sync_all_realtime_market(self) -> bool:
        """全量同步股票实时行情"""
        logger.info("Starting Sync All: Real-time Market Data")
        stock_codes = self._get_all_stock_codes()
        for code in stock_codes:
            await self.fetch_and_ingest_realtime_market(code)
        return True

    async def sync_all_kline(self, period: str = "daily") -> bool:
        """全量同步所有股票的 K 线数据"""
        logger.info(f"Starting Sync All: K-line Data ({period})")
        stock_codes = self._get_all_stock_codes()

        # Determine date range based on period
        from datetime import datetime, timedelta
        end_date = datetime.now().strftime("%Y-%m-%d")
        if period == "daily":
            start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        elif period == "weekly":
            start_date = (datetime.now() - timedelta(weeks=4)).strftime("%Y-%m-%d")
        elif period == "monthly":
            start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
        else:
            start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        success_count = 0
        for i, code in enumerate(stock_codes):
            if await self.fetch_and_ingest_stock_kline(code, start_date, end_date, period=period):
                success_count += 1
            if (i + 1) % 100 == 0:
                logger.info(f"K-line progress: {i + 1}/{len(stock_codes)} stocks processed")
        logger.info(f"Sync All K-line ({period}) completed. Success: {success_count}/{len(stock_codes)}")
        return True

    async def sync_all_valuation(self) -> bool:
        """全量同步估值数据 (PE/PB等)"""
        logger.info("Starting Sync All: Valuation Data")
        stock_codes = self._get_all_stock_codes()
        for i, code in enumerate(stock_codes):
            await self.fetch_and_ingest_stock_valuation(code)
            if (i + 1) % 100 == 0:
                logger.info(f"Valuation progress: {i + 1}/{len(stock_codes)} stocks")
        return True

    async def sync_all_trading_details(self) -> bool:
        """同步交易细节 (北向、融券、龙虎榜、盘口)"""
        logger.info("Starting Sync All: Trading Details")
        stock_codes = self._get_all_stock_codes()

        # Bulk or latest data
        from datetime import datetime, timedelta
        last_week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        await self.fetch_and_ingest_dragon_tiger(start_date=last_week)

        for i, code in enumerate(stock_codes):
            await self.fetch_and_ingest_northbound(code)
            await self.fetch_and_ingest_stock_margin_data(code)
            await self.fetch_and_ingest_stock_money_flow(code)
            await self.fetch_and_ingest_stock_block_trade(code)
            await self.fetch_and_ingest_sector_money_flow(code)
            if (i + 1) % 100 == 0:
                logger.info(f"Trading details progress: {i + 1}/{len(stock_codes)} stocks")
        return True

    async def sync_all_corporate_events(self) -> bool:
        """同步公司重大事件 (股东、质押、内幕、解禁)"""
        logger.info("Starting Sync All: Corporate Events")
        stock_codes = self._get_all_stock_codes()
        for i, code in enumerate(stock_codes):
            await self.fetch_and_ingest_stock_shareholder_count(code)
            await self.fetch_and_ingest_stock_pledge_risk(code)
            await self.fetch_and_ingest_stock_insider_trading(code)
            await self.fetch_and_ingest_stock_lockup_release(code)
            if (i + 1) % 50 == 0:
                logger.info(f"Corporate events progress: {i + 1}/{len(stock_codes)} stocks")
        return True

    async def sync_all_boards_and_pools(self) -> bool:
        """
        同步行业板块与各类股票池。

        Returns:
            所有同步步骤调度完成时返回 True。
        """
        logger.info("Starting Sync All: Boards and Pools")
        await self.fetch_and_ingest_board_industry()
        await self.fetch_and_ingest_stock_limit_up_pool()
        # Assume these exist or return False if not implemented
        await self.fetch_and_ingest_stock_limit_down_pool()
        await self.fetch_and_ingest_stock_zhaban_pool()

        return True


# Global Instance
ingestor_manager = IngestorManager()
