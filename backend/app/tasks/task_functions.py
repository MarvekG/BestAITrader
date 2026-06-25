"""
Data synchronization task functions

Functions executed in independent processes
"""
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import logging
import uuid

from sqlalchemy import func

from app.core.database import SessionLocal
from app.data.analytics.core_index import get_core_index_constituent_codes
from app.tasks.settlement import execute_daily_settlement


def get_sync_date_range(task_type: str = "normal") -> tuple[str, str]:
    """
    根据任务类型获取默认的日期范围 (Get default date range based on task type)
    - normal: 最近 3 天 (Last 3 days)
    - kline_base_info: 最近 365 天 (Last 365 days, for base info kline sync)
    - event_long: 前后 180 天 (Next and previous 180 days, for dividends, etc.)
    - margin: 最近 15 天 (Last 15 days, for margin and flow data)
    """
    now = datetime.now()
    if task_type == "event_long":
        start_date = (now - timedelta(days=180)).strftime("%Y-%m-%d")
        end_date = (now + timedelta(days=180)).strftime("%Y-%m-%d")
    elif task_type == "margin":
        start_date = (now - timedelta(days=15)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")
    elif task_type == "kline_base_info":
        start_date = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")
    else:  # normal
        start_date = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        end_date = now.strftime("%Y-%m-%d")
    return start_date, end_date


from app.data.market_utils import is_trading_time


logger = logging.getLogger(__name__)


def _sync_step_result(result: Any) -> bool:
    """
    从同步步骤返回值中提取最小结果，避免把接口明细数据写入任务结果。

    Args:
        result: 采集器或计算步骤返回的任意结果。

    Returns:
        步骤是否成功。
    """
    if result is None or result is False:
        return False
    if isinstance(result, dict):
        return bool(result.get("success", True))
    return True


async def sync_stock_data_func(
    stock_code: Optional[str] = None,
    task_id: Optional[str] = None,
    task_name: str = "Data Sync Task",
    allow_concurrent: bool = True,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
) -> Dict[str, Any]:
    """
    同步单只股票所需的核心行情、财务、资金流和辅助数据。

    Args:
        stock_code: 股票代码。
        task_id: 异步任务 ID，用于更新任务进度。
        task_name: 任务名称。
        allow_concurrent: 是否允许并发执行。
        start_date: 用户指定的同步起始日期，未指定时按数据类型使用默认范围。
        end_date: 用户指定的同步结束日期，未指定时按数据类型使用默认范围。

    Returns:
        包含任务状态、消息、各步骤结果和标准化股票代码的字典。
    """
    from app.data.ingestors.manager import ingestor_manager
    from app.tasks.task_manager import task_manager
    from app.core.database import SessionLocal
    from app.core.utils.formatters import StockCodeStandardizer

    if not stock_code:
        return {"status": "failed", "message": "stock_code is required for sync_stock_data_func"}

    stock_code = StockCodeStandardizer.standardize(stock_code)

    def resolve_date_range(task_type: str = "normal") -> tuple[str, str]:
        default_start, default_end = get_sync_date_range(task_type)
        return start_date or default_start, end_date or default_end

    normal_start_date, normal_end_date = resolve_date_range("normal")
    margin_start_date, margin_end_date = resolve_date_range("margin")
    logger.info(
        f"Starting sync task for stock_code: {stock_code}, task_id: {task_id}, "
        f"normal_range=({normal_start_date}, {normal_end_date}), "
        f"margin_range=({margin_start_date}, {margin_end_date})"
    )

    from app.core.i18n import i18n_service

    steps = [
        # Phase 1
        {"name": i18n_service.t("market.data_manager.stock_basics"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_info(stock_code)},
        {"name": i18n_service.t("market.data_manager.daily_kline"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_kline(stock_code, start_date=normal_start_date, end_date=normal_end_date, adjust="")},
        {"name": i18n_service.t("common.realtime_quote"), "func": lambda: ingestor_manager.fetch_and_ingest_realtime_market(stock_code)},
        {"name": i18n_service.t("market.valuation_metrics"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_valuation(stock_code, start_date=normal_start_date, end_date=normal_end_date)},  # Note: assuming missing dates
        {"name": i18n_service.t("market.data_manager.industry"), "func": lambda: ingestor_manager.fetch_and_ingest_board_industry()},
        {"name": i18n_service.t("market.data_manager.northbound"), "func": lambda: ingestor_manager.fetch_and_ingest_northbound(stock_code)},  # Note: missing dates
        {"name": i18n_service.t("market.data_manager.dragon_tiger"), "func": lambda: ingestor_manager.fetch_and_ingest_dragon_tiger(start_date=normal_start_date, end_date=normal_end_date)},  # Assuming incremental

        # Phase 2
        {"name": i18n_service.t("market.data_manager.stock_limit_pool"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_limit_up_pool(date=normal_end_date)},
        {"name": i18n_service.t("market.data_manager.stock_money_flow"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_money_flow(stock_code)},
        {"name": i18n_service.t("market.data_manager.stock_shareholder_count"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_shareholder_count(stock_code)},

        # Phase 3
        {"name": i18n_service.t("market.data_manager.stock_pledge_risk"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_pledge_risk(stock_code)},
        {"name": i18n_service.t("market.data_manager.stock_insider_trading"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_insider_trading(stock_code)},
        {"name": i18n_service.t("market.data_manager.stock_lockup_release"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_lockup_release(stock_code)},
        {"name": i18n_service.t("market.data_manager.stock_margin_data"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_margin_data(stock_code)},
        {"name": i18n_service.t("common.stock_block_trade"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_block_trade(stock_code, start_date=normal_start_date, end_date=normal_end_date)},

        # Phase 4
        {"name": i18n_service.t("common.sector_money_flow"), "func": lambda: ingestor_manager.fetch_and_ingest_sector_money_flow(stock_code)},
        {"name": i18n_service.t("market.data_manager.stock_top_holders"), "func": lambda: ingestor_manager.fetch_and_ingest_stock_top_holders(stock_code)},
        {"name": i18n_service.t("common.technical_indicators"), "func": lambda: calculate_indicators_func(stock_code=stock_code)}
    ]

    total_steps = len(steps)
    results = {}

    with SessionLocal() as db:
        for i, step in enumerate(steps):
            step_name = step["name"]
            progress = i + 1

            # Update status
            if task_id:
                try:
                    task_manager.update_task_status(
                        db=db,
                        task_id=task_id,
                        status="running",
                        result={
                            "progress": progress,
                            "total": total_steps,
                            "current_step": f"正在同步：{step_name}"
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to update task status for step {step_name}: {e}")

            # Execute step
            try:
                logger.info(f"[{task_id}] Executing step {progress}/{total_steps}: {step_name}")
                func = step["func"]
                success = await func()
                results[step_name] = _sync_step_result(success)
            except Exception as e:
                logger.error(f"[{task_id}] Step '{step_name}' failed: {e}", exc_info=True)
                results[step_name] = False

    msg = f"Sync for {stock_code} completed. Total steps: {total_steps}."
    return {
        "status": "success",
        "message": msg,
        "details": results,
        "stock_code": stock_code
    }

def execute_daily_settlement_func(**kwargs):
    """
    执行每日 T+1 结算任务
    Execute daily T+1 settlement task
    """
    try:
        logger.info("Executing daily settlement (T+1)...")
        execute_daily_settlement()
        logger.info("Daily settlement completed")
    except Exception as e:
        logger.error(f"Failed to execute daily settlement: {e}")
        raise


async def sync_bulk_tables_func(
    tables: List[str],
    task_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    stock_codes: Optional[str] = None,
    stock_scope: str = "warehouse",
    task_name: str = "Bulk Data Sync Task",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    Bulk Data sync task function (executed in independent process)

    Args:
        tables: List of table identifiers to sync
        task_id: Task ID for progress tracking
        start_date: Start date for sync (YYYY-MM-DD)
        end_date: End date for sync (YYYY-MM-DD)
        stock_codes: Comma-separated stock codes (optional, overrides stock_scope)
        stock_scope: Stock scope when stock_codes is empty: "warehouse" (default) or "all"
        task_name: Task name
        allow_concurrent: Whether to allow concurrency

    Returns:
        Task execution result
    """
    from app.data.ingestors.manager import ingestor_manager
    from app.tasks.task_manager import task_manager
    from app.core.database import SessionLocal
    from datetime import datetime, timedelta

    logger.info(f"Starting bulk sync task. Tables: {tables}, task_id: {task_id}, stock_codes: {stock_codes}, start_date: {start_date}, end_date: {end_date}")

    if not tables:
        return {"status": "success", "message": "No tables selected", "details": {}}

    # Determine stock codes to sync
    final_stock_codes = []
    if stock_codes and stock_codes.strip():
        # Parse, standardize, filter None, and deduplicate (preserving order)
        from app.core.utils.formatters import StockCodeStandardizer
        std_codes = [StockCodeStandardizer.standardize(c.strip()) for c in stock_codes.split(",") if c.strip()]
        final_stock_codes = list(dict.fromkeys(c for c in std_codes if c))

        logger.info(f"Using standardized stock codes for filtering: {final_stock_codes}")
    else:
        # 根据 stock_scope 决定股票代码来源
        # Determine stock codes based on stock_scope
        if stock_scope == "all":
            # 全量：从 stock_basic 获取所有股票代码
            # All: fetch all stock codes from stock_basic
            final_stock_codes = ingestor_manager._get_all_stock_codes_from_stock_basic()
            logger.info(f"stock_scope=all, using all {len(final_stock_codes)} codes from stock_basic")
        else:
            # 默认：从仓库获取活跃股票代码（warehouse）
            # Default: use all active codes from warehouse
            final_stock_codes = ingestor_manager._get_all_stock_codes()
            logger.info(f"No stock codes provided, using all {len(final_stock_codes)} active codes from warehouse")

    # 统一配置表：将方法引用与调用模式声明合并为单一字典
    # Unified config: method reference + dispatch mode in one structure
    # mode 取値：
    #   per_stock —— 遍历 final_stock_codes，逐只调用（需要 stock_code 参数）
    #   bulk      —— 不依赖股票列表，调用一次即可
    #   index     —— 遍历固定指数列表（000001/399001/399006/000300）
    # needs_scope: True 表示该条目受 stock_scope 选项影响（仓库 vs 全量）
    TABLE_CONFIG = {
        # --- Basics ---
        'stocks':                  {'method': ingestor_manager.fetch_and_ingest_all_stock_basic,           'mode': 'bulk'},
        'kline':                   {'method': ingestor_manager.fetch_and_ingest_stock_kline,               'mode': 'per_stock', 'needs_scope': True},
        'index_daily':             {'method': ingestor_manager.fetch_and_ingest_index_daily,               'mode': 'index'},
        'valuation':               {'method': ingestor_manager.fetch_and_ingest_stock_valuation,           'mode': 'per_stock', 'needs_scope': True},
        # --- Realtime / Quotes ---
        'realtime':                {'method': ingestor_manager.fetch_and_ingest_realtime_market,            'mode': 'per_stock', 'needs_scope': True},
        # --- Expanded Info ---
        'industry':                {'method': ingestor_manager.fetch_and_ingest_board_industry,             'mode': 'bulk'},
        # --- Trading Money Flows & Players ---
        'northbound':              {'method': ingestor_manager.fetch_and_ingest_northbound,                 'mode': 'per_stock', 'needs_scope': True},
        'dragontiger':             {'method': ingestor_manager.fetch_and_ingest_dragon_tiger,               'mode': 'bulk'},
        'stock_money_flow':        {'method': ingestor_manager.fetch_and_ingest_stock_money_flow,          'mode': 'per_stock', 'needs_scope': True},
        'sector_money_flow':       {'method': ingestor_manager.fetch_and_ingest_sector_money_flow,         'mode': 'per_stock', 'needs_scope': True},
        'stock_block_trade':       {'method': ingestor_manager.fetch_and_ingest_stock_block_trade,         'mode': 'per_stock', 'needs_scope': True},
        'stock_margin_data':       {'method': ingestor_manager.fetch_and_ingest_stock_margin_data,         'mode': 'per_stock', 'needs_scope': True},
        'stock_limit_up_pool':     {'method': ingestor_manager.fetch_and_ingest_stock_limit_up_pool,       'mode': 'bulk'},
        'stock_limit_down_pool':   {'method': ingestor_manager.fetch_and_ingest_stock_limit_down_pool,     'mode': 'bulk'},
        'stock_zhaban_pool':       {'method': ingestor_manager.fetch_and_ingest_stock_zhaban_pool,         'mode': 'bulk'},
        # --- Corporate Events / Attributes ---
        'stock_shareholder_count': {'method': ingestor_manager.fetch_and_ingest_stock_shareholder_count,   'mode': 'per_stock', 'needs_scope': True},
        'stock_pledge_risk':       {'method': ingestor_manager.fetch_and_ingest_stock_pledge_risk,         'mode': 'per_stock', 'needs_scope': True},
        'stock_pledge_summary':    {'method': ingestor_manager.fetch_and_ingest_all_pledge_summary,        'mode': 'bulk'},
        'stock_insider_trading':   {'method': ingestor_manager.fetch_and_ingest_stock_insider_trading,     'mode': 'per_stock', 'needs_scope': True},
        'stock_lockup_release':    {'method': ingestor_manager.fetch_and_ingest_stock_lockup_release,      'mode': 'per_stock', 'needs_scope': True},
        'stock_top_holders':       {'method': ingestor_manager.fetch_and_ingest_stock_top_holders,         'mode': 'per_stock', 'needs_scope': True},
    }

    # 自定义参数预填充逻辑（根据 table_key 补充日期等参数）
    # Custom argument pre-fill logic (date range, period, etc.)
    def get_method_kwargs(table_key, code=None):
        kwargs = {}
        now = datetime.now()

        # 全局日期范围默认値（未指定时回退 7 天）
        # Default date range (fall back 7 days if not specified)
        default_start = start_date if start_date else (now - timedelta(days=7)).strftime("%Y-%m-%d")
        default_end = end_date if end_date else now.strftime("%Y-%m-%d")

        if table_key == 'kline':
            kwargs.update({'start_date': default_start, 'end_date': default_end})
        elif table_key == 'dragontiger':
            kwargs.update({'start_date': default_start, 'end_date': default_end})
        elif table_key == 'stock_block_trade':
            kwargs.update({'start_date': default_start, 'end_date': default_end})
        elif table_key in {'income_statement', 'balance_sheet', 'cashflow_statement'}:
            kwargs.update({'start_date': default_start, 'end_date': default_end})
        elif table_key == 'index_daily':
            kwargs.update({'start_date': default_start, 'end_date': default_end})

        return kwargs

    total_steps = len(tables)
    results = {}

    with SessionLocal() as db:
        for i, table_key in enumerate(tables):
            progress = i + 1
            config = TABLE_CONFIG.get(table_key)

            if not config:
                logger.warning(f"[{task_id}] No mapping found for table key: {table_key}")
                results[table_key] = False
                continue

            method = config['method']
            mode = config['mode']

            # 更新任务状态
            # Update task progress status
            if task_id:
                try:
                    task_manager.update_task_status(
                        db=db,
                        task_id=task_id,
                        status="running",
                        result={
                            "progress": progress,
                            "total": total_steps,
                            "current_step": f"Syncing: {table_key}"
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to update task status for step {table_key}: {e}")

            try:
                if mode == 'per_stock':
                    # 逐股迭代模式：遍历 final_stock_codes，逐只调用
                    # Per-stock mode: iterate over final_stock_codes
                    logger.info(
                        f"[{task_id}] Executing step {progress}/{total_steps}: "
                        f"{table_key} (per_stock: {len(final_stock_codes)} stocks)"
                    )
                    success_count = 0
                    for code_idx, code in enumerate(final_stock_codes):
                        kwargs = get_method_kwargs(table_key, code)
                        kwargs['stock_code'] = code
                        success = await method(**kwargs)
                        step_result = _sync_step_result(success)
                        if step_result:
                            success_count += 1

                        if task_id and (code_idx + 1) % 5 == 0:
                            task_manager.update_task_status(
                                db=db, task_id=task_id, status="running",
                                result={
                                    "progress": progress, "total": total_steps,
                                    "current_step": f"Syncing {table_key} ({code_idx + 1}/{len(final_stock_codes)})"
                                }
                            )

                    results[table_key] = f"Success ({success_count}/{len(final_stock_codes)})"

                elif mode == 'index':
                    # 固定指数遍历模式：遍历主要指数代码 (从配置中读取)
                    # Index mode: iterate over major index codes from config
                    from app.core.config import settings
                    indices = settings.CORE_INDICES
                    logger.info(
                        f"[{task_id}] Executing step {progress}/{total_steps}: "
                        f"{table_key} (index: {len(indices)} indices)"
                    )
                    for idx_code in indices:
                        kwargs = get_method_kwargs(table_key)
                        kwargs['index_code'] = idx_code
                        await method(**kwargs)
                    results[table_key] = True

                else:
                    # bulk 模式：只调用一次，不依赖股票/品种列表
                    # Bulk mode: single call, no iteration
                    logger.info(
                        f"[{task_id}] Executing step {progress}/{total_steps}: "
                        f"{table_key} (bulk)"
                    )
                    kwargs = get_method_kwargs(table_key)
                    success = await method(**kwargs)
                    results[table_key] = _sync_step_result(success)

            except Exception as e:
                logger.error(f"[{task_id}] Step '{table_key}' failed: {e}", exc_info=True)
                results[table_key] = False

    msg = f"Bulk Sync completed for {len(tables)} tables."
    return {
        "status": "success",
        "message": msg,
        "details": results
    }


async def sync_all_stock_basic_func(
    stock_code: Optional[str] = None,
    task_name: str = "Stock Basic Info Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    全量/单只同步股票基础信息任务函数
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting {task_name} (Stock Code: {stock_code})")

    try:
        # Use simple await calls since blocking I/O is handled inside ingestors.
        if stock_code:
            # Sync single stock basic info
            success = await ingestor_manager.fetch_and_ingest_stock_info(stock_code)
        else:
            # Sync all stocks
            success = await ingestor_manager.fetch_and_ingest_all_stock_basic()

        status = "success" if success else "failed"
        return {
            "status": status,
            "message": f"Sync result: {status}",
            "stock_code": stock_code
        }

    except Exception as e:
        logger.error(f"Stock basic sync task failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def sync_valuation_data_func(
    stock_code: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    task_name: str = "Valuation Data Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步估值数据任务函数
    """
    from app.data.ingestors.manager import ingestor_manager
    from app.core.database import SessionLocal
    from app.models.stock_warehouse import StockWarehouse

    logger.info(f"Starting {task_name} (Stock Code: {stock_code})")

    try:
        # Use ingestor_manager which handles failover and abstraction
        # It already implements fetch_and_ingest_stock_valuation

        if stock_code:
            success = await ingestor_manager.fetch_and_ingest_stock_valuation(stock_code, start_date, end_date)
            msg = f"Sync result for {stock_code}: {success}"
        else:
            # Sync all warehouse stocks
            with SessionLocal() as db:
                stocks = db.query(StockWarehouse).all()
                count = 0
                for stock in stocks:
                    if await ingestor_manager.fetch_and_ingest_stock_valuation(stock.stock_code, start_date, end_date):
                        count += 1
                success = True
                msg = f"Synced valuation for {count} stocks"

        status = "success" if success else "failed"
        return {
            "status": status,
            "message": msg,
            "stock_code": stock_code
        }

    except Exception as e:
        logger.error(f"Valuation sync task failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def sync_dragon_tiger_data_func(
    date: str,
    end_date: str = None,
    task_name: str = "Dragon Tiger Sync Task",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步龙虎榜数据任务
    :param date: 同步日期 YYYY-MM-DD (start_date)
    :param end_date: 结束日期 YYYY-MM-DD (optional)
    :param task_name: 任务名称
    :param allow_concurrent: 是否允许并发
    """

    task_id = str(uuid.uuid4())
    logger.info(f"Task {task_id}: Starting {task_name} for date(s) {date} to {end_date or date}")

    try:
        from app.data.ingestors.manager import ingestor_manager

        # Use ingestor_manager to support multiple sources and failover
        success = await ingestor_manager.fetch_and_ingest_dragon_tiger(date, end_date)

        if success:
            logger.info(f"Task {task_id}: Completed successfully.")
            return {
                "task_id": task_id,
                "status": "completed",
                "message": f"Successfully synced dragon tiger data for {date}-{end_date or date}.",
            }
        else:
            logger.warning(f"Task {task_id}: Failed or no data found.")
            return {
                "task_id": task_id,
                "status": "warning",
                "message": f"No data found or sync failed for {date}",
            }

    except Exception as e:
        logger.error(f"Task {task_id}: Failed. Error: {e}", exc_info=True)
        return {
            "task_id": task_id,
            "status": "failed",
            "error_message": str(e)
        }


async def sync_limit_up_pool_func(
    date: Optional[str] = None,
    task_name: str = "Limit Up Pool Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步每日涨停池数据任务
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting limit up pool sync for date: {date or 'today'} using IngestorManager")

    try:
        success = await ingestor_manager.fetch_and_ingest_stock_limit_up_pool(date=date)

        return {
            "status": "success" if success else "failed",
            "date": date,
            "message": "Sync success" if success else "Sync failed or no data"
        }
    except Exception as e:
        logger.error(f"Limit up pool sync failed: {e}")
        return {"status": "failed", "error": str(e)}


async def sync_limit_down_pool_func(
    date: Optional[str] = None,
    task_name: str = "Limit Down Pool Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步每日跌停池数据任务
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting limit down pool sync for date: {date or 'today'} using IngestorManager")

    try:
        success = await ingestor_manager.fetch_and_ingest_stock_limit_down_pool(date=date)

        return {
            "status": "success" if success else "failed",
            "date": date,
            "message": "Sync success" if success else "Sync failed or no data"
        }
    except Exception as e:
        logger.error(f"Limit down pool sync failed: {e}")
        return {"status": "failed", "error": str(e)}


async def sync_zhaban_pool_func(
    date: Optional[str] = None,
    task_name: str = "Zhaban Pool Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步每日炸板池数据任务
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting zhaban pool sync for date: {date or 'today'} using IngestorManager")

    try:
        success = await ingestor_manager.fetch_and_ingest_stock_zhaban_pool(date=date)

        return {
            "status": "success" if success else "failed",
            "date": date,
            "message": "Sync success" if success else "Sync failed or no data"
        }
    except Exception as e:
        logger.error(f"Zhaban pool sync failed: {e}")
        return {"status": "failed", "error": str(e)}


async def sync_pledge_summary_func(
    task_name: str = "Pledge Summary Sync",
    stock_code: Optional[str] = None,
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步股权质押汇总数据任务。

    Args:
        task_name: 任务名称，用于任务系统标识。
        stock_code: 可选股票代码；TuShare pledge_stat 需要按股票代码查询。
        allow_concurrent: 是否允许并发运行同类任务。

    Returns:
        包含同步状态和提示信息的任务结果字典。
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting pledge summary sync for {stock_code or 'All'} using ingestor_manager")

    try:
        # Use ingestor_manager which handles failover between data sources
        success = await ingestor_manager.fetch_and_ingest_all_pledge_summary(stock_code=stock_code)

        return {
            "status": "success" if success else "failed",
            "message": "Sync success" if success else "Sync failed or no data"
        }
    except Exception as e:
        logger.error(f"Pledge summary sync failed: {e}")
        return {"status": "failed", "error": str(e)}


async def sync_granular_data_func(
    stock_code: str,
    data_type: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    task_name: str = "Granular Data Sync",
    allow_concurrent: bool = True
) -> Dict[str, Any]:
    """
    同步个股细分数据任务
    data_type enum: money_flow, shareholders, pledge, insider, lockup, margin
    """
    from app.data.ingestors.manager import ingestor_manager
    from app.core.utils.formatters import StockCodeStandardizer

    logger.info(f"Starting granular sync '{data_type}' for {stock_code}")

    try:
        # Apply default dates if not provided
        if not start_date or not end_date:
            date_task_type = "normal"
            if data_type == "lockup":
                date_task_type = "event_long"
            elif data_type in ["margin", "money_flow"]:
                date_task_type = "margin"
          
            d_start, d_end = get_sync_date_range(date_task_type)
            start_date = start_date or d_start
            end_date = end_date or d_end

        if stock_code:
            stock_code = StockCodeStandardizer.standardize(stock_code)
            logger.info(f"Standardized stock code to {stock_code}")

        success = False
        if data_type == 'money_flow':
            success = await ingestor_manager.fetch_and_ingest_stock_money_flow(stock_code)
        elif data_type == 'shareholders':
            success = await ingestor_manager.fetch_and_ingest_stock_shareholder_count(stock_code)
        elif data_type == 'pledge':
            success = await ingestor_manager.fetch_and_ingest_stock_pledge_risk(stock_code)
        elif data_type == 'insider':
            success = await ingestor_manager.fetch_and_ingest_stock_insider_trading(stock_code)
        elif data_type == 'lockup':
            success = await ingestor_manager.fetch_and_ingest_stock_lockup_release(stock_code)
        elif data_type == 'margin':
            success = await ingestor_manager.fetch_and_ingest_stock_margin_data(stock_code)
        elif data_type == 'block_trade':
            success = await ingestor_manager.fetch_and_ingest_stock_block_trade(stock_code, start_date, end_date)
        else:
            return {"status": "failed", "error": f"Unknown data type: {data_type}"}

        return {
            "status": "success" if success else "failed",
            "stock_code": stock_code,
            "data_type": data_type,
            "message": "Sync success" if success else "Sync failed or no data"
        }
    except Exception as e:
        logger.error(f"Granular sync '{data_type}' failed for {stock_code}: {e}")
        return {"status": "failed", "error": str(e)}


async def sync_stock_daily_func(
    stock_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    adjust: str = "qfq",
    task_name: str = "Daily Data Sync",
    allow_concurrent: bool = True
) -> Dict[str, Any]:
    """
    同步个股日线数据任务
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting daily sync for {stock_code} ({start_date}-{end_date})")

    try:
        if not start_date or not end_date:
            d_start, d_end = get_sync_date_range("normal")
            start_date = start_date or d_start
            end_date = end_date or d_end
          
        success = await ingestor_manager.fetch_and_ingest_stock_kline(
            stock_code=stock_code,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust
        )

        return {
            "status": "success" if success else "failed",
            "stock_code": stock_code,
            "message": "Sync success" if success else "Sync failed"
        }
    except Exception as e:
        logger.error(f"Daily sync failed for {stock_code}: {e}")
        return {"status": "failed", "error": str(e)}


async def sync_realtime_market_func(
    stock_code: str,
    task_name: str = "Realtime Market Data Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    实时行情数据同步任务函数 (仅支持单股)
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting {task_name} for stock_code: {stock_code}")

    try:
        # Sync single stock
        success = await ingestor_manager.fetch_and_ingest_realtime_market(stock_code)

        status = "success" if success else "failed"
        return {
            "status": status,
            "message": f"Sync result for {stock_code}: {status}",
            "stock_code": stock_code
        }

    except Exception as e:
        logger.error(f"Realtime market sync task failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def sync_industry_data_func(
    task_name: str = "Industry Data Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    行业板块数据同步任务函数
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting {task_name}")

    try:
        # Use ingestor_manager which handles failover and default source
        success = await ingestor_manager.fetch_and_ingest_board_industry()

        status = "success" if success else "failed"
        return {
            "status": status,
            "message": f"Sync result: {status}"
        }

    except Exception as e:
        logger.error(f"Industry data sync task failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def sync_sector_money_flow_func(
    stock_code: str,
    task_name: str = "Sector Money Flow Sync",
    allow_concurrent: bool = True
) -> Dict[str, Any]:
    """
    板块资金流数据同步任务函数
    根据股票代码同步其所属行业的资金流数据
    """
    from app.data.ingestors.manager import ingestor_manager
    from app.core.utils.formatters import StockCodeStandardizer

    logger.info(f"Starting {task_name} for stock {stock_code}")

    try:
        # Standardize stock code
        stock_code = StockCodeStandardizer.standardize(stock_code)

        # Call ingestor with stock_code directly
        # The ingestor will determine the sector from the stock code
        success = await ingestor_manager.fetch_and_ingest_sector_money_flow(stock_code)

        status = "success" if success else "failed"
        message = f"Synced sector money flow for stock {stock_code}"
        logger.info(f"Task completed: {message}")

        return {
            "status": status,
            "message": message,
            "stock_code": stock_code
        }

    except Exception as e:
        logger.error(f"Sector money flow sync task failed for {stock_code}: {e}", exc_info=True)
        return {"status": "failed", "error": str(e), "stock_code": stock_code}


async def sync_index_daily_func(
    index_code: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    task_name: str = "Index Daily Sync",
    allow_concurrent: bool = True
) -> Dict[str, Any]:
    """
    同步指数日线数据任务
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting index daily sync for {index_code} ({start_date or 'recent'}-{end_date or 'now'})")

    try:
        if not start_date or not end_date:
            d_start, d_end = get_sync_date_range("normal")
            start_date = start_date or d_start
            end_date = end_date or d_end
          
        success = await ingestor_manager.fetch_and_ingest_index_daily(
            index_code=index_code,
            start_date=start_date,
            end_date=end_date
        )

        return {
            "status": "success" if success else "failed",
            "index_code": index_code,
            "message": "Sync success" if success else "Sync failed"
        }
    except Exception as e:
        logger.error(f"Index daily sync failed for {index_code}: {e}")
        return {"status": "failed", "error": str(e)}


async def sync_northbound_data_func(
    stock_code: Optional[str] = None,
    task_name: str = "Northbound Data Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步北向资金持股数据任务函数
    """
    from app.data.ingestors.manager import ingestor_manager
    from app.core.database import SessionLocal
    from app.models.stock_warehouse import StockWarehouse

    logger.info(f"Starting {task_name} (Stock Code: {stock_code})")

    try:
        if stock_code:
            # Sync single stock
            success = await ingestor_manager.fetch_and_ingest_northbound(stock_code)
            msg = f"Sync result for {stock_code}: {success}"
        else:
            # Sync all warehouse stocks
            with SessionLocal() as db:
                stocks = db.query(StockWarehouse).all()
                count = 0
                for stock in stocks:
                    if await ingestor_manager.fetch_and_ingest_northbound(stock.stock_code):
                        count += 1
                success = True
                msg = f"Synced northbound data for {count} stocks"

        status = "success" if success else "failed"
        return {
            "status": status,
            "message": msg,
            "stock_code": stock_code
        }

    except Exception as e:
        logger.error(f"Northbound sync task failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def calculate_indicators_func(
    stock_code: Optional[str] = None,
    task_name: str = "Calculate Indicators",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    计算技术指标任务函数
    """
    from app.data.analytics.indicators import indicator_service
    from app.core.database import SessionLocal
    from app.models.stock_warehouse import StockWarehouse

    logger.info(f"Starting {task_name} (Stock Code: {stock_code})")

    try:
        with SessionLocal() as db:
            if stock_code:
                indicator_service.process_stock(db, stock_code)
                msg = f"Calculated indicators for {stock_code}"
            else:
                # Calculate for all stocks in warehouse
                stocks = db.query(StockWarehouse).all()
                count = 0
                for stock in stocks:
                    try:
                        indicator_service.process_stock(db, stock.stock_code)
                        count += 1
                        if count % 10 == 0:
                            logger.info(f"Calculated indicators for {count} stocks...")
                    except Exception as e:
                        logger.error(f"Failed to calculate for {stock.stock_code}: {e}")

                msg = f"Calculated indicators for {count} stocks"

        return {
            "status": "success",
            "message": msg,
            "stock_code": stock_code
        }

    except Exception as e:
        logger.error(f"Calculate indicators task failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def sync_top_holders_func(
    stock_code: str,
    task_name: str = "Top Holders Sync",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    同步十大股东数据任务函数
    Sync top 10 holders data for a specific stock
    """
    from app.data.ingestors.manager import ingestor_manager

    logger.info(f"Starting top holders sync for {stock_code}")

    try:
        # 使用 ingestor_manager，它会选择 Tushare 数据源
        success = await ingestor_manager.fetch_and_ingest_stock_top_holders(stock_code)

        status = "success" if success else "failed"
        return {
            "status": status,
            "message": f"Sync result: {status}",
            "stock_code": stock_code
        }

    except Exception as e:
        logger.error(f"Top holders sync failed for {stock_code}: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def update_warehouse_stocks_realtime_quotes(
    task_name: str = "Warehouse Stocks Realtime Quotes Update",
    allow_concurrent: bool = False
) -> Dict[str, Any]:
    """
    定时更新股票仓库中股票的实时行情
    Periodically update realtime quotes for stocks in the stock warehouse
    """
    # 1. 检查是否为交易时间
    if not is_trading_time():
        logger.info("Not in trading time, skipping realtime quotes update.")
        return {
            "status": "skipped",
            "message": "Not in trading time",
            "timestamp": datetime.now().isoformat()
        }

    logger.info(f"Starting {task_name}")

    try:
        from app.models.stock_warehouse import StockWarehouse
        from app.data.ingestors.manager import ingestor_manager

        # 2. 获取股票仓库中的所有活跃股票
        with SessionLocal() as db:
            stocks = db.query(StockWarehouse).filter(StockWarehouse.is_active.is_(True)).all()
            stock_codes = [s.stock_code for s in stocks]

        if not stock_codes:
            logger.info("No active stocks in warehouse, skipping update.")
            return {
                "status": "success",
                "message": "No active stocks to update",
                "count": 0
            }

        logger.info(f"Updating realtime quotes for {len(stock_codes)} stocks in warehouse.")

        # 3. 逐个更新行情
        success_count = 0
        failed_stocks = []

        for code in stock_codes:
            try:
                # 调用 ingestor 更新实时行情
                success = await ingestor_manager.fetch_and_ingest_realtime_market(code)
                if success:
                    success_count += 1
                else:
                    failed_stocks.append(code)
            except Exception as e:
                logger.error(f"Failed to update realtime quote for {code}: {e}")
                failed_stocks.append(code)

        status = "success" if success_count > 0 else "failed"
        return {
            "status": status,
            "message": f"Updated {success_count}/{len(stock_codes)} stocks.",
            "success_count": success_count,
            "failed_count": len(failed_stocks),
            "failed_stocks": failed_stocks,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Warehouse stocks realtime quotes update failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def cleanup_stock_realtime_market_history(task_name: str | None = None) -> Dict[str, Any]:
    """
    清理股票实时行情历史记录，仅保留最近 24 小时数据。

    Args:
        task_name: 调度器传入的任务展示名称，当前仅用于兼容进程执行器的任务参数注入。

    Returns:
        包含删除记录数和清理截止时间的结果。
    """
    _ = task_name
    import pytz

    from app.models.data_storage import StockRealtimeMarket

    retention_hours = 24

    try:
        with SessionLocal() as db:
            latest_timestamp = db.query(func.max(StockRealtimeMarket.timestamp)).scalar()
            if latest_timestamp is None:
                return {
                    "status": "success",
                    "deleted_count": 0,
                    "cutoff": None,
                    "retention_hours": retention_hours,
                    "timestamp": datetime.now().isoformat(),
                }

            cutoff_time = latest_timestamp - timedelta(hours=retention_hours)
            deleted_count = (
                db.query(StockRealtimeMarket)
                .filter(StockRealtimeMarket.timestamp < cutoff_time)
                .delete(synchronize_session=False)
            )
            db.commit()
        logger.info("Deleted %s stale StockRealtimeMarket records before %s", deleted_count, cutoff_time.isoformat())
        return {
            "status": "success",
            "deleted_count": deleted_count,
            "cutoff": cutoff_time.isoformat(),
            "retention_hours": retention_hours,
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        logger.error(f"StockRealtimeMarket cleanup failed: {e}", exc_info=True)
        return {"status": "failed", "error": str(e)}


async def _process_single_stock(single_code: str, current_task_id: str) -> Dict[str, Any]:
    from app.data.ingestors.manager import ingestor_manager
    from app.core.utils.formatters import StockCodeStandardizer
    from app.core.i18n import i18n_service
    single_code = StockCodeStandardizer.standardize(single_code)
    kline_start_date, kline_end_date = get_sync_date_range("kline_base_info")

    # 定义同步步骤 (Define sync steps)
    steps = [
        {"name": i18n_service.t("market.data_manager.stock_basics"), "key": "stock_basic",
         "func": lambda: ingestor_manager.fetch_and_ingest_stock_info(single_code)},
        {"name": i18n_service.t("market.data_manager.daily_kline"), "key": "kline_data",
         "func": lambda: ingestor_manager.fetch_and_ingest_stock_kline(
             single_code,
             start_date=kline_start_date,
             end_date=kline_end_date,
             adjust=""
         )},
        {"name": i18n_service.t("market.valuation_metrics"), "key": "valuation",
         "func": lambda: ingestor_manager.fetch_and_ingest_stock_valuation(single_code)},
        {"name": i18n_service.t("market.data_manager.stock_top_holders"), "key": "top_holders",
         "func": lambda: ingestor_manager.fetch_and_ingest_stock_top_holders(single_code)},
        {"name": i18n_service.t("market.data_manager.stock_realtime_market"), "key": "realtime_market",
         "func": lambda: ingestor_manager.fetch_and_ingest_realtime_market(single_code)},
        {"name": i18n_service.t("common.technical_indicators"), "key": "stock_indicators",
         "func": lambda: calculate_indicators_func(stock_code=single_code)},
    ]

    total_steps = len(steps)
    results = {}
    success_count = 0

    for i, step in enumerate(steps):
        step_name = step["name"]
        step_key = step["key"]
        progress = i + 1

        # 执行具体同步逻辑 (Execute sync logic)
        try:
            logger.info(f"[{current_task_id}] Executing step {progress}/{total_steps}: {step_name} for {single_code}")
            func = step["func"]
            success = await func()
            results[step_key] = _sync_step_result(success)
            if results[step_key]:
                success_count += 1
        except Exception as e:
            logger.error(f"[{current_task_id}] Step '{step_name}' failed: {e}", exc_info=True)
            results[step_key] = False

    final_status = "success" if success_count == total_steps else "warning" if success_count > 0 else "failed"
    msg = f"Base info sync for {single_code} completed. {success_count}/{total_steps} steps succeeded."

    return {
        "status": final_status,
        "message": msg,
        "details": results,
        "stock_code": single_code
    }


async def sync_base_info_func(
    task_id: str,
    stock_code: Optional[str] = None,
    task_name: str = "Base Information Sync",
    allow_concurrent: bool = False,
    resume: bool = False,
    scope: str = "all"
) -> Dict[str, Any]:
    """
    一键同步基础信息任务函数 (One-click Base Information Sync)
    同步内容：股票基础信息、日线行情、财务指标、估值数据、十大股东、实时行情、技术指标
    """
    from app.data.ingestors.manager import ingestor_manager
    from app.tasks.task_manager import task_manager
    from app.core.database import SessionLocal

    logger.info(f"Starting base info sync for {stock_code if stock_code else 'ALL (Filtered)'} (scope: {scope}), task_id: {task_id}")
    # 如果没有指定股票代码，则执行全量过滤同步 (Batch sync if no stock_code)
    if not stock_code:
        with SessionLocal() as db:
            from app.models.data_storage import StockBasic
            from app.models.async_task import AsyncTask
            from app.models.stock_warehouse import StockWarehouse
            from app.ai.stock_picker.universe import get_basic_stock_filter_conds

            stock_list = []
            start_index = 0

            # 尝试从上次任务续传 (Try resume from last task)
            if resume:
                # ... [Keep resume logic the same, it depends on task_type] ...
                last_task = db.query(AsyncTask).filter(
                    AsyncTask.task_type == "base_info_sync",
                    AsyncTask.status.in_(["failed", "warning", "running"]),
                    AsyncTask.task_id != task_id
                ).order_by(AsyncTask.created_at.desc()).first()

                if last_task and last_task.result and "stock_list" in last_task.result:
                    stock_list = last_task.result["stock_list"]
                    start_index = last_task.result.get("last_processed_index", -1) + 1
                    logger.info(f"Resuming task {last_task.task_id} from index {start_index}")

            if not stock_list:
                # 1. 先同步一次 A 股基础列表 (Update master list first)
                logger.info("Updating master stock list from ingestor...")
                await ingestor_manager.fetch_and_ingest_all_stock_basic()

                # 2. 获取过滤后的股票列表 (Get filtered list)
                if scope == "warehouse":
                    logger.info("Sync scope: warehouse. Fetching active warehouse stocks...")
                    warehouse_stocks = db.query(StockWarehouse.stock_code).filter(StockWarehouse.is_active == True).all()
                    stock_list = [s[0] for s in warehouse_stocks]
                elif scope == "core":
                    logger.info("Sync scope: core. Fetching core index constituents via Tushare...")
                    stock_list = get_core_index_constituent_codes()
                else:
                    logger.info("Sync scope: all. Fetching filtered basic stocks...")
                    filtered_conds = get_basic_stock_filter_conds()
                    stocks_to_sync = db.query(StockBasic.stock_code).filter(filtered_conds).all()
                    stock_list = [s[0] for s in stocks_to_sync]
                
                start_index = 0

            total_stocks = len(stock_list)
            logger.info(f"Filtered {total_stocks} stocks for batch base info sync (Start at: {start_index})")

            # 初始化当前任务状态 (Initialize current task result with stock_list)
            task_manager.update_task_status(
                db=db,
                task_id=task_id,
                status="running",
                result={
                    "progress": start_index,
                    "total": total_stocks,
                    "stock_list": stock_list,
                    "last_processed_index": start_index - 1
                }
            )

            if total_stocks == 0 or start_index >= total_stocks:
                return {"status": "success", "message": "No stocks match the filter criteria or already completed."}

            # 3. 循环调用自身同步每只股票 (Recurse for each stock)
            success_count = 0
            for idx in range(start_index, total_stocks):
                code = stock_list[idx]
                try:
                    # 每同步 3 只或最后一只更新总进度 (Update overall progress every 3 stocks or at the end)
                    if task_id and (idx % 3 == 0 or idx == total_stocks - 1):
                        # 获取当前任务结果以获取最新的 stock_list (虽然 stock_list 不变)
                        current_task = db.query(AsyncTask).filter(AsyncTask.task_id == task_id).first()
                        task_result = (current_task.result or {}).copy() if current_task else {}

                        task_result.update({
                            "progress": idx + 1,
                            "total": total_stocks,
                            "current_step": f"Updating {code}",
                            "last_processed_index": idx,
                            "stock_list": stock_list
                        })

                        task_manager.update_task_status(
                            db=db,
                            task_id=task_id,
                            status="running",
                            result=task_result
                        )

                    # 执行单股同步步骤
                    res = await _process_single_stock(code, task_id)
                    if res.get("status") == "success":
                        success_count += 1
                except Exception as e:
                    logger.error(f"Failed to sync stock {code} in batch: {e}")

            return {
                "status": "success" if success_count > 0 else "failed",
                "message": (
                    f"Batch sync completed. {success_count}/{total_stocks - start_index} "
                    f"stocks succeeded in this session."
                ),
                "total": total_stocks,
                "success_count": success_count,
                "resumed_from": start_index
            }

    return await _process_single_stock(stock_code, task_id)
