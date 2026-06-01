from collections.abc import Callable
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.i18n import i18n_service
from app.core.logger import logger
from app.core.request_context import get_or_create_request_id
from app.ai.llm_engine.context.service import AIContextService
from app.data.metadata.field_labels import get_table_field_label
from app.data.storage import data_storage_service
from app.core.utils.formatters import StockCodeStandardizer
from app.core.utils.json_utils import sanitize_for_json


def _submit_async_task(
    executor: Any,
    *,
    task_id: str,
    task_func: Callable[..., Any],
    task_args: tuple = (),
    task_kwargs: dict[str, Any] | None = None,
    task_name: str | None = None,
) -> bool:
    """
    提交后台异步任务并保留当前请求上下文。

    Args:
        executor: 异步任务运行器实例。
        task_id: 任务 ID。
        task_func: 任务函数。
        task_args: 任务位置参数。
        task_kwargs: 任务关键字参数。
        task_name: 可选任务展示名称。

    Returns:
        是否提交成功。
    """
    return executor.submit_task(
        task_id=task_id,
        task_func=task_func,
        task_args=task_args,
        task_kwargs=task_kwargs,
        task_name=task_name,
        request_id=get_or_create_request_id(),
    )

router = APIRouter()


@router.get("/stocks/{stock_code}")
async def get_stock_data(
    stock_code: str
):
    """Get stock data"""
    try:
        # Use DataStorageService to get data from DB
        stock_data = data_storage_service.get_stock_data_from_db(stock_code)
        if not stock_data:
            raise HTTPException(status_code=404, detail=f"Stock code {stock_code} not found")
        return stock_data
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/stock/name/{stock_code}", response_model=Dict[str, str])
async def get_stock_name(stock_code: str):
    """Get stock name by code"""
    try:
        # Use DataStorageService to get basic info
        basic_info = data_storage_service.get_stock_basic(stock_code)
        if not basic_info:
             raise HTTPException(status_code=404, detail=f"Stock code {stock_code} not found")

        return {
            "stock_code": basic_info["stock_code"],
            "stock_name": basic_info["name"]
        }
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/ai-context/{stock_code}")
async def get_ai_context(stock_code: str):
    """
    获取股票完整的 AI 上下文数据，用于 AI 决策参考。
    (Fetch the full AI context data for a stock, used for AI decision reference.)
    """
    try:
        context = await AIContextService().build(stock_code)
        return sanitize_for_json(context)
    except Exception as e:
        logger.error(f"Failed to fetch AI context for {stock_code}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

from app.models.data_storage import KlineData

@router.get("/kline/{stock_code}", response_model=List[Dict[str, Any]])
def get_kline_data(
    stock_code: str,
    freq: str = "D",
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get Kline data"""
    try:
        # Format stock code
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        # Query database
        klines = db.query(KlineData).filter(
            KlineData.stock_code == formatted_code,
            KlineData.freq == freq
        ).order_by(
            KlineData.date.desc()
        ).limit(limit).all()

        # Convert data format (return to frontend in chronological order)
        result = []
        for k in reversed(klines):
            result.append({
                "date": k.date.isoformat(),
                "open": k.open,
                "close": k.close,
                "high": k.high,
                "low": k.low,
                "volume": k.volume,
                "turnover": k.turnover,
                "change_percent": k.change_percent
            })

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/db/stocks")
async def get_db_stocks(
    stock_code: Optional[str] = None,
    query: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get basic stock information from database"""
    from app.models.data_storage import StockBasic
    from sqlalchemy import or_

    db_query = db.query(StockBasic)
    if stock_code:
        db_query = db_query.filter(StockBasic.stock_code == StockCodeStandardizer.standardize(stock_code))
    if query:
        db_query = db_query.filter(
            or_(
                StockBasic.stock_code.ilike(f"%{query}%"),
                StockBasic.name.ilike(f"%{query}%")
            )
        )

    total = db_query.count()
    items = db_query.offset(skip).limit(limit).all()

    # Transform keys to TableName.FieldName format
    transformed_items = []
    for item in items:
        item_dict = {}
        for key, value in item.__dict__.items():
            if not key.startswith('_'):
                item_dict[f"{StockBasic.__tablename__}.{key}"] = value
        transformed_items.append(item_dict)

    return {
        "total": total,
        "items": transformed_items
    }


def _get_json_report_data(
    db: Session,
    model: Any,
    target_table: str,
    stock_code: Optional[str],
    skip: int,
    limit: int,
    extra_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Helper function to fetch and process JSON-based report data with efficient pagination"""
    import math

    try:
        query = db.query(model)

        if stock_code:
            formatted_code = StockCodeStandardizer.standardize(stock_code)
            if formatted_code:
                query = query.filter(model.stock_code == formatted_code)

        # Calculate total count
        total = query.count()

        # Fetch paginated items, sorted by report_date dec
        items_db = query.order_by(model.report_date.desc(), model.stock_code).offset(skip).limit(limit).all()

        if not items_db:
            return {
                "total": total,
                "items": []
            }

        items = []
        for item in items_db:
            # Base data
            result_item = {
                "id": str(item.id),
                "stock_code": item.stock_code,
                "report_date": str(item.report_date) if item.report_date else None,
                "announcement_date": str(item.announcement_date) if item.announcement_date else None,
                "update_date": str(item.update_date) if item.update_date else None,
                "data_source": item.data_source,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None
            }

            for field in extra_fields or []:
                if hasattr(item, field):
                    result_item[field] = getattr(item, field)

            # Flatten indicators from 'data' column
            if isinstance(item.data, dict):
                for k, v in item.data.items():
                    localized_key = get_table_field_label(target_table, k)
                    value = v
                    # Sanitize float values (NaN, Inf)
                    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                        value = None

                    # Format large numbers for display (Maintain legacy logic if needed,
                    # but maybe better to let frontend handle it)
                    # For now, let's keep it consistent with previous logic for core fields
                    if value is not None and k in ["net_profit", "total_revenue", "revenue"]:
                         # API might return formatted numbers already, but if it's raw, we handle it
                         # Note: the previous logic divided by 1e8.
                         # If we want to move this to frontend, we should.
                         # But let's keep it for now to avoid breaking existing user experience.
                         if abs(value) > 1000000: # Simple heuristic: if it looks like a large number
                             value = round(value / 100000000, 2)

                    if localized_key in result_item and localized_key != k:
                        result_item[k] = value
                    else:
                        result_item[localized_key] = value

            items.append(result_item)

        return {
            "total": total,
            "items": items
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal Server Error: {str(e)}")

@router.get("/db/data/{data_type}")
async def get_db_data(
    data_type: str,
    stock_code: Optional[str] = None,
    # Add standardized date filters
    date: Optional[str] = None,
    update_date: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    # Add data_source filter for futures data differentiation
    data_source: Optional[str] = None,
    # Add sorting support
    sort_by: Optional[str] = None,
    order: Optional[str] = 'desc',
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Generic database table query interface"""
    from app.models.data_storage import (
        StockBasic, FinancialIndicator, StockIncomeStatement, StockBalanceSheet, StockCashflowStatement, KlineData, NorthboundData,
        DragonTigerData, StockRealtimeMarket, StockValuationHistory,
        StockLimitUpPool, StockLimitDownPool, StockZhabanPool, StockMoneyFlow, SectorMoneyFlow, StockShareholder, StockPledge,
        StockInsider, StockRelease, StockForecast, StockMargin, IndexDaily,
        StockBlockTrade, StockPledgeSummary, StockTopHolders, StockInteractiveQA
    )
    from app.models.stock_indicators import StockIndicators
    from collections import defaultdict
    import math

    def sanitize_float_values(obj):
        """Convert NaN and Infinity float values to None for JSON serialization"""
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    obj[key] = None
        elif hasattr(obj, '__dict__'):
            for key, value in obj.__dict__.items():
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    setattr(obj, key, None)
        return obj

    table_map = {
        "kline": KlineData,
        "financial": FinancialIndicator,
        "income_statement": StockIncomeStatement,
        "balance_sheet": StockBalanceSheet,
        "cashflow_statement": StockCashflowStatement,
        "northbound": NorthboundData,
        "dragontiger": DragonTigerData,
        "valuation": StockValuationHistory,
        "realtime": StockRealtimeMarket,
        "stock_limit_up_pool": StockLimitUpPool,
        "stock_money_flow": StockMoneyFlow,
        "stock_shareholder_count": StockShareholder,
        "stock_pledge_risk": StockPledge,
        "stock_insider_trading": StockInsider,
        "stock_lockup_release": StockRelease,
        "stock_earnings_forecast": StockForecast,
        "stock_margin_data": StockMargin,
        "stock_limit_down_pool": StockLimitDownPool,
        "stock_zhaban_pool": StockZhabanPool,
        "index_daily": IndexDaily,
        "stock_indicators": StockIndicators,
        "stock_block_trade": StockBlockTrade,
        "sector_money_flow": SectorMoneyFlow,
        "stock_block_trade": StockBlockTrade,
        "sector_money_flow": SectorMoneyFlow,
        "stock_pledge_summary": StockPledgeSummary,
        "stock_top_holders": StockTopHolders,
        "stock_interactive_qa": StockInteractiveQA
    }

    model = table_map.get(data_type.lower())
    if not model:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported data type: {data_type}"
        )

    # Use table name from model definition as prefix
    prefix = model.__tablename__

    # Special handling for JSON-based financial reports
    if data_type.lower() in {"financial", "income_statement", "balance_sheet", "cashflow_statement"}:
        extra_fields = []
        if data_type.lower() in {"income_statement", "balance_sheet", "cashflow_statement"}:
            extra_fields = ["report_type", "currency", "is_audit"]
        result = _get_json_report_data(
            db,
            model,
            f"data.{model.__tablename__}",
            stock_code,
            skip,
            limit,
            extra_fields=extra_fields,
        )
        transformed_items = []
        for item in result["items"]:
            new_item = {}
            for k, v in item.items():
                new_item[f"{prefix}.{k}"] = v
            transformed_items.append(new_item)
        result["items"] = transformed_items
        return result

    query = db.query(model)
    if stock_code:
        standardized_code = StockCodeStandardizer.standardize(stock_code)
        if hasattr(model, 'stock_code'):
            query = query.filter(model.stock_code == standardized_code)
        elif data_type.lower() == "sector_money_flow":
            from app.models.data_storage import StockBasic
            stock = db.query(StockBasic).filter(StockBasic.stock_code == standardized_code).first()
            if stock and stock.industry:
                industry = stock.industry
                mapping = {
                    '白酒': '酿酒行业',
                    '地产': '房地产开发',
                    '房地产': '房地产开发',
                    '银行': '银行行业',
                    '光伏': '光伏设备',
                }
                sector_name = mapping.get(industry, industry)
                query = query.filter(model.sector_name == sector_name)
            else:
                query = query.filter(model.sector_name == "___NOT_FOUND___")
        elif hasattr(model, 'etf_code'):
            query = query.filter(model.etf_code == standardized_code)

    # Data source filtering (for futures data differentiation)
    if data_source and hasattr(model, 'data_source'):
        query = query.filter(model.data_source == data_source)


    # Date filtering
    date_val = date or update_date
    if date_val:
        # Try to find the date column
        if hasattr(model, 'update_date'):
             query = query.filter(model.update_date == date_val)
        elif hasattr(model, 'trade_date'):
             query = query.filter(model.trade_date == date_val)
        elif hasattr(model, 'date'):
             query = query.filter(model.date == date_val)
        elif hasattr(model, 'publish_date'):
             query = query.filter(model.publish_date == date_val)
        elif hasattr(model, 'report_date'):
             query = query.filter(model.report_date == date_val)
        elif hasattr(model, 'data_date'):
             query = query.filter(model.data_date == date_val)

    # Sort processing
    if sort_by and hasattr(model, sort_by):
         # Explicit sorting
         if order and order.lower() == 'asc':
             query = query.order_by(getattr(model, sort_by).asc())
         else:
             query = query.order_by(getattr(model, sort_by).desc())
    else:
        # Default implicit sorting
        if hasattr(model, 'update_date'):
            query = query.order_by(model.update_date.desc())
        elif hasattr(model, 'trade_date'):
            query = query.order_by(model.trade_date.desc())
        elif hasattr(model, 'date'):
            query = query.order_by(model.date.desc())
        elif hasattr(model, 'publish_date'):
            query = query.order_by(model.publish_date.desc())
        elif hasattr(model, 'report_date'):
            query = query.order_by(model.report_date.desc())
        elif hasattr(model, 'data_date'):
            query = query.order_by(model.data_date.desc())
        elif hasattr(model, 'end_date'): # For Shareholder count
            query = query.order_by(model.end_date.desc())
        elif hasattr(model, 'timestamp'):
            query = query.order_by(model.timestamp.desc())

    total = query.count()
    items = query.offset(skip).limit(limit).all()

    # Sanitize float values and transform keys
    sanitized_items = []
    for item in items:
        # First sanitize
        s_item = sanitize_float_values(item)
        # Then transform to dict with prefix
        item_dict = {}
        for k, v in s_item.__dict__.items():
            if not k.startswith('_'):
                item_dict[f"{prefix}.{k}"] = v
        sanitized_items.append(item_dict)

    return {
        "total": total,
        "items": sanitized_items
    }

from typing import Optional, List
from pydantic import BaseModel

class BulkSyncRequest(BaseModel):
    tables: List[str]
    start_date: str
    end_date: str
    stock_codes: Optional[str] = None
    # 股票范围: "warehouse"（仓库股票，默认）或 "all"（stock_basic 全量）
    # Stock scope: "warehouse" (default) or "all" (all stocks in stock_basic)
    stock_scope: Optional[str] = "warehouse"

@router.post("/db/sync/bulk")
async def sync_bulk_data(
    request: BulkSyncRequest,
    db: Session = Depends(get_db)
):
    """
    一键批量同步选中数据表 (Bulk Sync Selected Tables)
    The client provides a list of table identifiers to sync.
    """
    from app.tasks.async_task_runner import async_task_runner
    from app.tasks.task_functions import sync_bulk_tables_func
    from app.tasks.task_manager import task_manager
    from app.core.i18n import i18n_service
    from fastapi import HTTPException

    try:
        if not request.tables:
            raise HTTPException(status_code=400, detail="No tables selected for sync")

        # 准备任务参数 (Prepare task parameters)
        task_name = i18n_service.t("tasks.names.data_bulk_sync") + f" ({len(request.tables)} items)"
        task_type = "bulk_data_sync"
        parameters = {"tables": request.tables}

        # 提交任务到任务管理器 (生成 task_id 并检查并发)
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            # 提交到独立进程执行 (Submit to independent process execution)
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_bulk_tables_func,
                task_kwargs={
                    "tables": request.tables,
                    "task_id": task_result["task_id"],
                    "start_date": request.start_date,
                    "end_date": request.end_date,
                    "stock_codes": request.stock_codes,
                    "stock_scope": request.stock_scope or "warehouse",
                    "allow_concurrent": False
                },
                task_name=task_name,
            )

        return task_result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit bulk data sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/db/sync")
async def sync_db_data(
    stock_code: str = Query(..., description="Stock Code (Required)"),
    start_date: Optional[str] = Query(None, description="Start Date (YYYY-MM-DD/YYYYMMDD)"),
    end_date: Optional[str] = Query(None, description="End Date (YYYY-MM-DD/YYYYMMDD)"),
    db: Session = Depends(get_db)
):
    """Manually sync database data for a single stock (Async Task)

    Args:
        stock_code: Stock code to synchronize

    Returns:
        Task submission result, including task ID and status message
    """
    from app.tasks.async_task_runner import async_task_runner
    from app.tasks.task_functions import sync_stock_data_func
    from app.tasks.task_manager import task_manager
    from app.core.i18n import i18n_service
    from fastapi import HTTPException

    try:
        if not stock_code:
            raise HTTPException(status_code=400, detail="stock_code is required")

        # Prepare task parameters
        task_name = i18n_service.t("tasks.names.data_sync") + f" ({stock_code})"
        task_type = "db_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date
        }

        # Submit task using task_manager (generate task_id and check concurrency)
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False,
            celery_task_id=None  # Do not use Celery
        )

        if task_result.get("new_task"):
            # Submit to independent process execution
            success = _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_stock_data_func,
                task_kwargs={
                    "stock_code": stock_code,
                    "task_id": task_result["task_id"],
                    "allow_concurrent": False,
                    "start_date": start_date,
                    "end_date": end_date
                },
                task_name=task_name,
            )

        return task_result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit database sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/base-info")
async def sync_base_info(
    stock_code: Optional[str] = Query(None, description="Stock code (Optional, blank for batch sync)"),
    scope: str = Query("all", description="Sync scope: 'all', 'warehouse', or 'core'"),
    resume: bool = Query(False, description="Whether to resume from last incomplete task"),
    db: Session = Depends(get_db)
):
    """
    一键同步基础信息接口 (One-click Base Information Sync API)
    同步：基础信息、日线行情、财务指标、估值数据、十大股东、实时行情、技术指标
    """
    from app.tasks.async_task_runner import async_task_runner
    from app.tasks.task_functions import sync_base_info_func
    from app.tasks.task_manager import task_manager
    from app.core.i18n import i18n_service

    try:
        # 准备任务参数 (Prepare task parameters)
        display_code = stock_code if stock_code else i18n_service.t(f"common.scope_{scope}")
        task_name = i18n_service.t("common.sync_base_info") + f"({display_code}) "
        task_type = "base_info_sync"
        parameters = {"stock_code": stock_code, "resume": resume, "scope": scope}

        # 提交任务到任务管理器 (生成 task_id 并检查并发)
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            # 提交到独立进程执行 (Submit to independent process execution)
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_base_info_func,
                task_kwargs={
                    "stock_code": stock_code,
                    "task_id": task_result["task_id"],
                    "allow_concurrent": False,
                    "resume": resume,
                    "scope": scope
                },
                task_name=task_name,
            )

        return task_result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to submit base info sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/stock-basic")
async def sync_stock_basic(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    resume: bool = Query(False, description="Whether to resume from last incomplete task"),
    db: Session = Depends(get_db)
):
    """
    手动同步股票基础信息 (Async Task)
    如果提供了 stock_code，则只同步该股票；否则全量同步。
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_all_stock_basic_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_name = i18n_service.t("tasks.names.stock_basic_sync").format(info=stock_code or 'All')
        task_type = "stock_basic_sync"
        parameters = {"stock_code": stock_code, "resume": resume}

        # Submit task
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_all_stock_basic_func,
                task_kwargs={
                    "stock_code": stock_code,
                },
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit stock basic sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/valuation")
async def sync_valuation_history(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    start_date: Optional[str] = Query(None, description="Start Date YYYYMMDD (Optional)"),
    end_date: Optional[str] = Query(None, description="End Date YYYYMMDD (Optional)"),
    db: Session = Depends(get_db)
):
    """
    手动同步估值数据 (Async Task)
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_valuation_data_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_name = i18n_service.t("tasks.names.valuation_sync").format(info=stock_code or 'All')
        task_type = "valuation_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date
        }

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_valuation_data_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit valuation sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/dragon-tiger")
async def sync_dragon_tiger_data(
    date: str = Query(None, description="Date (YYYY-MM-DD) for single day sync"),
    start_date: str = Query(None, description="Start Date (YYYY-MM-DD)"),
    end_date: str = Query(None, description="End Date (YYYY-MM-DD)"),
    allow_concurrent: bool = False,
    db: Session = Depends(get_db)
):
    """
    同步龙虎榜数据
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_dragon_tiger_data_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    # Resolve date logic: start_date takes precedence, fallback to date
    effective_start_date = start_date or date

    if not effective_start_date:
         raise HTTPException(status_code=400, detail="Either date or start_date must be provided")

    task_name = i18n_service.t("tasks.names.dragon_tiger_sync").format(dates=f"{effective_start_date} to {end_date or effective_start_date}")
    task_type = "data_sync"

    parameters = {
        "date": effective_start_date,
        "end_date": end_date,
        "task_name": task_name,
        "allow_concurrent": allow_concurrent
    }

    try:
        # Submit task using task_manager (handles concurrency check and DB record)
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=allow_concurrent
        )

        # If it is a new task (message indicates success), submit to executor
        if task_result.get("new_task"):
            success = _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_dragon_tiger_data_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

            if not success:
                # Rollback/Update status if submission failed
                task_manager.update_task_status(
                    db=db,
                    task_id=task_result["task_id"],
                    status="failed",
                    error_message="Failed to submit task to process executor"
                )
                raise HTTPException(status_code=500, detail="Failed to submit task to executor")

        return task_result

    except Exception as e:
        logger.error(f"Failed to submit dragon tiger sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/northbound")
async def sync_northbound_data(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    db: Session = Depends(get_db)
):
    """
    手动同步北向资金持股数据 (Async Task)
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_northbound_data_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_name = i18n_service.t("tasks.names.northbound_sync").format(info=stock_code or 'All')
        task_type = "northbound_sync"
        parameters = {"stock_code": stock_code}

        # Submit task
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_northbound_data_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit northbound sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/limit-up-pool")
async def sync_limit_up_pool(
    date: str = Query(None, description="Date (YYYY-MM-DD or YYYYMMDD)"),
    db: Session = Depends(get_db)
):
    """
    Manually sync daily Limit Up Pool
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_limit_up_pool_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    task_name = i18n_service.t("tasks.names.limit_up_pool_sync").format(date=date or 'Today')
    task_type = "data_sync"
    parameters = {"date": date}

    try:
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_limit_up_pool_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit limit up pool sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/limit-down-pool")
async def sync_limit_down_pool(
    date: str = Query(None, description="Date (YYYY-MM-DD or YYYYMMDD)"),
    db: Session = Depends(get_db)
):
    """
    Manually sync daily Limit Down Pool
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_limit_down_pool_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    task_name = i18n_service.t("tasks.names.limit_down_pool_sync").format(date=date or 'Today')
    task_type = "data_sync"
    parameters = {"date": date}

    try:
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_limit_down_pool_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit limit down pool sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/zhaban-pool")
async def sync_zhaban_pool(
    date: str = Query(None, description="Date (YYYY-MM-DD or YYYYMMDD)"),
    db: Session = Depends(get_db)
):
    """
    Manually sync daily Zhaban Pool (Fried Board)
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_zhaban_pool_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    task_name = i18n_service.t("tasks.names.zhaban_pool_sync").format(date=date or 'Today')
    task_type = "data_sync"
    parameters = {"date": date}

    try:
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_zhaban_pool_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit zhaban pool sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/pledge-summary")
async def sync_pledge_summary(
    stock_code: Optional[str] = Query(None, description="Stock code"),
    db: Session = Depends(get_db)
):
    """
    同步股权质押汇总数据 (TuShare pledge_stat 接口)
    Manual sync for pledge summary data. TuShare requires stock_code.
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_pledge_summary_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    task_name = i18n_service.t("tasks.names.pledge_summary_sync") or "Sync Pledge Summary"
    if stock_code:
        task_name = f"{task_name} ({stock_code})"
    task_type = "data_sync"
    parameters = {"stock_code": stock_code}

    try:
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_pledge_summary_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit pledge summary sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/granular/{data_type}")
async def sync_granular_data(
    data_type: str,
    stock_code: str = Query(..., description="Stock Code"),
    start_date: Optional[str] = Query(None, description="Start Date (Optional, YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End Date (Optional, YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """
    Manually sync specific granular data type for a stock
    data_type: money_flow, shareholders, pledge, insider, lockup, forecast, margin
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_granular_data_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    valid_types = [
        "money_flow", "shareholders", "pledge", "insider",
        "lockup", "forecast", "margin", "block_trade"
    ]
    if data_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"Invalid data type. Must be one of {valid_types}")

    task_name = i18n_service.t("tasks.names.granular_sync").format(type=data_type, stock=stock_code)
    task_type = "granular_sync"
    parameters = {
        "stock_code": stock_code,
        "data_type": data_type,
        "start_date": start_date,
        "end_date": end_date
    }

    try:
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=True
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_granular_data_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit granular sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/daily")
async def sync_daily_data(
    stock_code: str = Query(..., description="Stock Code"),
    start_date: str = Query(..., description="Start Date (YYYYMMDD)"),
    end_date: str = Query(..., description="End Date (YYYYMMDD)"),
    adjust: str = "qfq",
    db: Session = Depends(get_db)
):
    """
    手动同步个股日线数据
    Manually sync stock daily k-line data
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_stock_daily_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    task_name = i18n_service.t("tasks.names.daily_sync").format(stock=stock_code, range=f"{start_date}-{end_date}")
    task_type = "daily_sync"
    parameters = {
        "stock_code": stock_code,
        "start_date": start_date,
        "end_date": end_date,
        "adjust": adjust
    }

    try:
        # Submit task
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=True
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_stock_daily_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit daily sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/index-daily")
async def sync_index_daily(
    index_code: str = Query(..., description="Index Code"),
    start_date: str = Query(..., description="Start Date (YYYYMMDD)"),
    end_date: str = Query(..., description="End Date (YYYYMMDD)"),
    db: Session = Depends(get_db)
):
    """
    手动同步指数日线数据
    Manually sync index daily k-line data
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_index_daily_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    task_name = i18n_service.t("tasks.names.index_daily_sync").format(index_code=index_code, range=f"{start_date}-{end_date}")
    task_type = "index_daily_sync"
    parameters = {
        "index_code": index_code,
        "start_date": start_date,
        "end_date": end_date
    }

    try:
        # Submit task
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=True
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_index_daily_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit index daily sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))



@router.get("/db/stock_detail/{stock_code}")
async def get_db_stock_detail(
    stock_code: str,
    db: Session = Depends(get_db)
):
    """直接从数据库获取股票详情"""
    from app.data.storage import data_storage_service
    from app.core.utils.formatters import StockCodeStandardizer

    try:
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        # 从存储服务获取数据
        stock_data = data_storage_service.get_stock_data_from_db(formatted_code)

        if not stock_data:
            detail_msg = f"Stock code {stock_code} not found or no data available"
            raise HTTPException(status_code=404, detail=detail_msg)


        # Calculate technical indicators if kline data is available
        if "_kline_data" in stock_data and stock_data["_kline_data"]:
            try:

                # Calculate indicators using the utility method
                from app.data.analytics.technical_indicators import calculate_technical_indicators

                stock_data["technical_indicators"] = calculate_technical_indicators(
                    stock_data["_kline_data"]
                )
            except Exception as e:
                # Log error but don't fail the request, just leave indicators empty

                logger.error(
                    f"Failed to calculate technical indicators for {stock_code}: {e}"
                )

        # Remove internal data key
        stock_data.pop("_kline_data", None)

        # Ensure fundamentals are populated (at least with market data) to avoid frontend issues
        # Re-applying the safe fallback for fundamentals
        market_data = stock_data.get("market_data", {})
        fundamentals = stock_data.get("fundamentals", {})
        if not isinstance(fundamentals, dict):
            fundamentals = {}

        # Helper to safely get float
        def safe_float(val):
            try:
                if val is None: return 0.0
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        # Create defaults from market_data
        defaults = {
            "pe_ttm": safe_float(market_data.get("pe_ttm")),
            "pb": safe_float(market_data.get("pb")),
            "total_market_value": safe_float(market_data.get("market_cap")),
        }

        # Try to get growth rates from financial_indicators
        financials = stock_data.get("financial_indicators", [])
        rev_growth = 0.0
        profit_growth = 0.0

        if isinstance(financials, list) and financials:
             # Financials is a list of indicator objects {indicator_name, indicator_value, ...}
             # We need to find the latest value for revenue growth and profit growth
             for item in financials:
                 if not isinstance(item, dict): continue

                 name = item.get("indicator_name", "")
                 val = safe_float(item.get("indicator_value", 0))

                 # Check for revenue growth
                 if rev_growth == 0.0 and name == "total_revenue_yoy":
                     rev_growth = val

                 # Check for profit growth
                 if profit_growth == 0.0 and name == "net_profit_yoy":
                     profit_growth = val

                 if rev_growth != 0.0 and profit_growth != 0.0:
                     break

        elif isinstance(financials, dict):
            rev_growth = safe_float(financials.get("total_revenue_yoy", 0))
            profit_growth = safe_float(financials.get("net_profit_yoy", 0))

        defaults["total_revenue_yoy"] = rev_growth
        defaults["net_profit_yoy"] = profit_growth

        # Update fundamentals with defaults only if keys missing
        for k, v in defaults.items():
            if k not in fundamentals or fundamentals[k] is None:
                fundamentals[k] = v

        stock_data["fundamentals"] = fundamentals

        # Additional safe guard: ensure fundamentals has these keys even if they were 0
        if "total_revenue_yoy" not in stock_data["fundamentals"]:
            stock_data["fundamentals"]["total_revenue_yoy"] = rev_growth
        if "net_profit_yoy" not in stock_data["fundamentals"]:
            stock_data["fundamentals"]["net_profit_yoy"] = profit_growth

        # Ensure return data structure matches DetailedSnapshot interface required by frontend as much as possible,
        # or frontend needs to adapt.
        # The storage service returns data sufficiently close to what frontend expects for 'snapshot'.

        # Sanitize entire response to prevent JSON serialization errors with NaN/Infinity
        import math
        def sanitize_for_json(obj):
            if isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            elif isinstance(obj, dict):
                return {k: sanitize_for_json(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [sanitize_for_json(i) for i in obj]
            return obj

        return sanitize_for_json(stock_data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/realtime")
async def get_realtime_market(
    skip: int = 0,
    limit: int = 100,
    stock_code: Optional[str] = None,
    sort_by: Optional[str] = None,
    order: str = "desc",
    db: Session = Depends(get_db)
):
    """Get real-time market data from database

    Args:
        skip: Pagination offset
        limit: Number of records to return
        stock_code: Filter by stock code (optional)
        sort_by: Sort field (e.g., 'change_percent', 'turnover', 'latest_price')
        order: Sort order ('asc' or 'desc')

    Returns:
        Paginated real-time market data
    """
    from app.models.data_storage import StockRealtimeMarket
    import math

    try:
        query = db.query(StockRealtimeMarket)

        # Filter by stock code if provided
        if stock_code:
            formatted_code = StockCodeStandardizer.standardize(stock_code)
            query = query.filter(StockRealtimeMarket.stock_code == formatted_code)

        # Apply sorting
        if sort_by:
            sort_column = getattr(StockRealtimeMarket, sort_by, None)
            if sort_column is not None:
                if order.lower() == 'asc':
                    query = query.order_by(sort_column.asc())
                else:
                    query = query.order_by(sort_column.desc())
        else:
            # Default sort by timestamp desc
            query = query.order_by(StockRealtimeMarket.timestamp.desc())

        total = query.count()
        items = query.offset(skip).limit(limit).all()

        # Sanitize float values and add prefix
        prefix = StockRealtimeMarket.__tablename__
        def sanitize_item(item):
            result = {}
            for key, value in item.__dict__.items():
                if key.startswith('_'):
                    continue
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    result[f"{prefix}.{key}"] = None
                else:
                    result[f"{prefix}.{key}"] = value
            return result

        sanitized_items = [sanitize_item(item) for item in items]

        return {
            "total": total,
            "items": sanitized_items
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/valuation/{stock_code}")
async def get_stock_valuation(
    stock_code: str,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get stock valuation history from database

    Args:
        stock_code: Stock code
        skip: Pagination offset
        limit: Number of records to return

    Returns:
        Paginated valuation history data
    """
    from app.models.data_storage import StockValuationHistory
    import math

    try:
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        query = db.query(StockValuationHistory).filter(StockValuationHistory.stock_code == formatted_code)

        total = query.count()
        items = query.order_by(StockValuationHistory.data_date.desc()).offset(skip).limit(limit).all()

        # Sanitize float values and add prefix
        prefix = StockValuationHistory.__tablename__
        def sanitize_item(item):
            result = {}
            for key, value in item.__dict__.items():
                if key.startswith('_'):
                    continue
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    result[f"{prefix}.{key}"] = None
                else:
                    result[f"{prefix}.{key}"] = value
            return result

        return {
            "total": total,
            "items": [sanitize_item(item) for item in items]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/market/sync/realtime/{stock_code}")
async def sync_realtime_market(
    stock_code: str,
    db: Session = Depends(get_db)
):
    """Sync real-time market data for a specific stock to database

    Args:
        stock_code: Stock code to sync

    Returns:
        Sync result with task ID
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.async_task_runner import async_task_runner
    from app.tasks.task_functions import sync_realtime_market_func
    from app.core.utils.formatters import StockCodeStandardizer

    try:
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        task_id = str(uuid.uuid4())
        task_name = f"Realtime Market Sync: {formatted_code}"

        task_manager.submit_task(
            db=db,
            celery_task_id=task_id,
            task_name=task_name,
            task_type="realtime_market_sync",
            parameters={"stock_code": formatted_code}
        )

        _submit_async_task(async_task_runner,
            task_id=task_id,
            task_func=sync_realtime_market_func,
            task_kwargs={
                "stock_code": formatted_code,
                "task_name": task_name
            },
        )

        return {
            "success": True,
            "message": i18n_service.get("tasks.submission_success", "Async task submitted successfully").format(task_id=task_id),
            "task_id": task_id
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/market/sync/valuation/{stock_code}")
async def sync_stock_valuation(
    stock_code: str,
    db: Session = Depends(get_db)
):
    """Sync stock valuation history from the active data source to database

    Args:
        stock_code: Stock code

    Returns:
        Sync result with count of records updated
    """
    from app.data.ingestors.manager import ingestor_manager

    try:
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        # Use IngestorManager to fetch and ingest valuation history
        success = await ingestor_manager.fetch_and_ingest_stock_valuation(formatted_code)

        if not success:
             raise HTTPException(status_code=404, detail=f"No valuation data found or failed to ingest for {stock_code}")

        return {
            "success": True,
            "message": f"Successfully synced valuation data for {stock_code}",
            "count": 1 # Count unknown as it is handled internally
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/market/industry")
async def get_industry_market(
    skip: int = 0,
    limit: int = 100,
    sort_by: Optional[str] = None,
    order: str = "desc",
    db: Session = Depends(get_db)
):
    """Get industry market data"""
    from app.models.data_storage import IndustryData
    import math

    try:
        query = db.query(IndustryData)

        # Apply sorting
        if sort_by:
            sort_column = getattr(IndustryData, sort_by, None)
            if sort_column is not None:
                if order.lower() == 'asc':
                    query = query.order_by(sort_column.asc())
                else:
                    query = query.order_by(sort_column.desc())
        else:
            query = query.order_by(IndustryData.change_percent.desc())

        total = query.count()
        items = query.offset(skip).limit(limit).all()

        # Sanitize item and add prefix
        prefix = IndustryData.__tablename__

        def sanitize_item(item):
            result = {}
            for key, value in item.__dict__.items():
                if key.startswith('_'):
                    continue
                if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
                    result[f"{prefix}.{key}"] = None
                else:
                    result[f"{prefix}.{key}"] = value
            return result

        return {
            "total": total,
            "items": [sanitize_item(item) for item in items]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/market/sync/industry")
async def sync_industry_market(
    db: Session = Depends(get_db)
):
    """Sync industry market data (Async Task)"""
    from app.tasks.task_manager import task_manager
    from app.tasks.async_task_runner import async_task_runner
    from app.tasks.task_functions import sync_industry_data_func
    from app.core.i18n import i18n_service

    try:
        task_id = str(uuid.uuid4())
        task_name = i18n_service.t("tasks.names.industry_sync") or "Industry Data Sync"

        task_manager.submit_task(
            db=db,
            celery_task_id=task_id,
            task_name=task_name,
            task_type="industry_sync",
            parameters={}
        )

        _submit_async_task(async_task_runner,
            task_id=task_id,
            task_func=sync_industry_data_func,
            task_kwargs={"task_name": task_name},
        )

        return {
            "success": True,
            "message": i18n_service.get("tasks.submission_success", "Async task submitted successfully").format(task_id=task_id),
            "task_id": task_id
        }
    except Exception as e:
        logger.error(f"Failed to submit industry sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/market/sync/sector-money-flow")
async def sync_sector_money_flow(
    stock_code: str,
    db: Session = Depends(get_db)
):
    """同步板块资金流数据 (Async Task)"""
    from app.tasks.task_manager import task_manager
    from app.tasks.async_task_runner import async_task_runner
    from app.tasks.task_functions import sync_sector_money_flow_func
    from app.core.i18n import i18n_service

    try:
        task_id = str(uuid.uuid4())
        task_name = i18n_service.t("tasks.names.sector_money_flow_sync") or "Sector Money Flow Sync"

        task_manager.submit_task(
            db=db,
            celery_task_id=task_id,
            task_name=task_name,
            task_type="sector_money_flow_sync",
            parameters={"stock_code": stock_code}
        )

        _submit_async_task(async_task_runner,
            task_id=task_id,
            task_func=sync_sector_money_flow_func,
            task_kwargs={"stock_code": stock_code, "task_name": task_name},
        )

        return {
            "success": True,
            "message": i18n_service.get("tasks.submission_success", "Async task submitted successfully").format(task_id=task_id),
            "task_id": task_id
        }
    except Exception as e:
        logger.error(f"Failed to submit sector money flow sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/db/stock/{stock_code}")
async def delete_stock_data(
    stock_code: str,
    db: Session = Depends(get_db)
):
    """
    Cascade delete ALL data related to a specific stock code.
    Warning: This includes trading history, orders, positions, and analysis data.
    """
    from app.models.data_storage import (
        StockBasic, KlineData, FinancialIndicator, NorthboundData,
        DragonTigerData, StockValuationHistory, StockRealtimeMarket,
        CommonData, StockInteractiveQA
    )
    from app.models.stock_warehouse import StockWarehouse
    from app.models.stock_analysis import StockAnalysisResult
    from app.models.trade_record import TradeRecord
    from app.models.order import Order
    from app.models.position import Position
    from app.models.session import Session

    from app.core.utils.formatters import StockCodeStandardizer

    try:
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        # Comprehensive list of models to delete from
        models = [
            # Data Storage
            StockBasic, KlineData, FinancialIndicator, NorthboundData,
            DragonTigerData, StockValuationHistory, StockRealtimeMarket,
            CommonData, StockInteractiveQA,

            # User/App Data
            StockWarehouse, StockAnalysisResult,

            # Trading Records (Destructive)
            TradeRecord, Order, Position, Session
        ]

        deleted_counts = {}

        for model in models:
            # Check if model has stock_code column (they all should per verification)
            if hasattr(model, 'stock_code'):
                # Note: For Session table, we might be deleting sessions that *contain* this stock?
                # Actually Session model has stock_code if it's a single-stock session.
                # If it's a multi-stock session, it might not have stock_code or logic differs.
                # Checking Session model:
                # class Session(Base):
                #     ...
                #     stock_code = Column(String(10), index=True)
                # So yes, we delete sessions specific to this stock.

                count = db.query(model).filter(model.stock_code == formatted_code).delete()
                if count > 0:
                    deleted_counts[model.__tablename__] = count

        db.commit()

        return {
            "message": f"Successfully deleted ALL data for {stock_code}",
            "deleted_counts": deleted_counts
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete stock data: {str(e)}"
        )


@router.post("/db/sync/financial")
async def sync_financial_data(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    start_date: str = Query(..., description="Start Date YYYYMMDD"),
    end_date: str = Query(..., description="End Date YYYYMMDD"),
    db: Session = Depends(get_db)
):
    """
    手动同步财务指标 (Async Task)
    如果提供了 stock_code，则只同步该股票；否则全量同步。
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_financial_indicator_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_info = stock_code or 'All'
        task_name = i18n_service.t("tasks.names.financial_sync").format(info=task_info)
        task_type = "financial_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
        }

        # Submit task
        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_financial_indicator_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit financial sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/income-statement")
async def sync_income_statement_data(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    start_date: str = Query(..., description="Start Date YYYYMMDD"),
    end_date: str = Query(..., description="End Date YYYYMMDD"),
    db: Session = Depends(get_db)
):
    """
    手动同步利润表 (Async Task)
    如果提供了 stock_code，则只同步该股票；否则同步股票仓中的股票。
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_income_statement_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_info = stock_code or 'Warehouse'
        task_name = i18n_service.t("tasks.names.income_statement_sync").format(info=task_info)
        task_type = "income_statement_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
        }

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_income_statement_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit income statement sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/balance-sheet")
async def sync_balance_sheet_data(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    start_date: str = Query(..., description="Start Date YYYYMMDD"),
    end_date: str = Query(..., description="End Date YYYYMMDD"),
    db: Session = Depends(get_db)
):
    """
    手动同步资产负债表 (Async Task)
    如果提供了 stock_code，则只同步该股票；否则同步股票仓中的股票。
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_balance_sheet_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_info = stock_code or 'Warehouse'
        task_name = i18n_service.t("tasks.names.balance_sheet_sync").format(info=task_info)
        task_type = "balance_sheet_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
        }

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_balance_sheet_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit balance sheet sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/cashflow-statement")
async def sync_cashflow_statement_data(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    start_date: str = Query(..., description="Start Date YYYYMMDD"),
    end_date: str = Query(..., description="End Date YYYYMMDD"),
    db: Session = Depends(get_db)
):
    """
    手动同步现金流量表 (Async Task)
    如果提供了 stock_code，则只同步该股票；否则同步股票仓中的股票。
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_cashflow_statement_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_info = stock_code or 'Warehouse'
        task_name = i18n_service.t("tasks.names.cashflow_statement_sync").format(info=task_info)
        task_type = "cashflow_statement_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
        }

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_cashflow_statement_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit cashflow statement sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/db/tables")
async def get_db_tables(
    db: Session = Depends(get_db)
):
    """
    Get list of all database tables
    """
    from app.core.database import engine
    inspector = inspect(engine)
    return inspector.get_table_names()


from pydantic import BaseModel

class ClearTableRequest(BaseModel):
    table_name: str
    confirmation_text: str


@router.post("/db/clear")
async def clear_db_table(
    request: ClearTableRequest,
    db: Session = Depends(get_db)
):
    """
    Clear data from a specific table or all tables.
    Requires confirmation text: 'confirm' or '确认'.
    """
    from app.core.database import engine

    # 1. Validation
    if request.confirmation_text.strip().lower() not in ["confirm", "确认"]:
        raise HTTPException(status_code=400, detail="Invalid confirmation text. Please type 'confirm' or '确认'.")

    # 2. Get available tables from both public and data schemas
    inspector = inspect(engine)
    public_tables = inspector.get_table_names(schema='public')
    data_tables = inspector.get_table_names(schema='data')
    
    # Store table names with schema prefix
    schema_table_map = {} # { "table_name": "schema.table_name" }
    for t in public_tables:
        schema_table_map[t] = f"public.{t}"
        schema_table_map[f"public.{t}"] = f"public.{t}"
    for t in data_tables:
        schema_table_map[t] = f"data.{t}"
        schema_table_map[f"data.{t}"] = f"data.{t}"

    target_tables = []
    if request.table_name == 'all':
        target_tables = list(set(schema_table_map.values()))
    elif request.table_name in schema_table_map:
        target_tables = [schema_table_map[request.table_name]]
    else:
        raise HTTPException(
            status_code=404, 
            detail=f"Table '{request.table_name}' not found in public or data schemas."
        )

    if not target_tables:
        raise HTTPException(status_code=400, detail="No tables to clear.")

    # 3. Execute Clear
    # We use raw connection execution similar to scripts/clear_tables.py
    try:
        # We need to execute strictly, so we use engine connect
        with engine.connect() as conn:
            # Check dialect
            is_sqlite = 'sqlite' in engine.dialect.name

            for table in target_tables:
                 if is_sqlite:
                     conn.execute(text(f"DELETE FROM {table}"))
                 else:
                     # PostgreSQL: TRUNCATE with CASCADE is faster and handles FKs better if needed
                     conn.execute(text(f"TRUNCATE TABLE {table} CASCADE"))

            conn.commit()

        return {
            "status": "success",
            "message": f"Successfully cleared {len(target_tables)} table(s)"
        }

    except Exception as e:
        logger.error(f"Failed to clear tables: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/db/calculate/indicators")
async def calculate_indicators(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    db: Session = Depends(get_db)
):
    """
    手动触发指标计算 (Async Task)
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import calculate_indicators_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_name = i18n_service.t("tasks.names.calculate_indicators").format(info=stock_code or 'All')
        task_type = "calculate_indicators"
        parameters = {"stock_code": stock_code}

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=calculate_indicators_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit calculate indicators task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/interactive-qa")
async def sync_stock_interactive_qa(
    stock_code: str = Query(..., description="Stock Code (Required)"),
    start_date: Optional[str] = Query(None, description="Start Date YYYYMMDD (Optional)"),
    end_date: Optional[str] = Query(None, description="End Date YYYYMMDD (Optional)"),
    db: Session = Depends(get_db)
):
    """
    手动同步互动问答数据 (Async Task)
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_stock_interactive_qa_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_info = stock_code if not start_date else f"{stock_code} ({start_date}-{end_date})"
        task_name = i18n_service.t("tasks.names.interactive_qa_sync").format(info=task_info)
        task_type = "interactive_qa_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
        }

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_stock_interactive_qa_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit interactive QA sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/valuation")
async def sync_valuation_data(
    stock_code: Optional[str] = Query(None, description="Stock Code (Optional)"),
    start_date: Optional[str] = Query(None, description="Start Date (Optional)"),
    end_date: Optional[str] = Query(None, description="End Date (Optional)"),
    db: Session = Depends(get_db)
):
    """
    Manually sync valuation data (Async Task)
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_valuation_data_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_name = i18n_service.t("tasks.names.valuation_sync").format(info=stock_code or 'All')
        task_type = "valuation_sync"
        parameters = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date
        }

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_valuation_data_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit valuation sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/sync/top_holders")
async def sync_top_holders(
    stock_code: str = Query(..., description="Stock Code (Required)"),
    db: Session = Depends(get_db)
):
    """
    手动同步十大股东数据 (Async Task)
    Manually sync top 10 holders data for a specific stock
    """
    from app.tasks.task_manager import task_manager
    from app.tasks.task_functions import sync_top_holders_func
    from app.tasks.async_task_runner import async_task_runner
    from app.core.i18n import i18n_service

    try:
        task_name = i18n_service.t("tasks.names.top_holders_sync").format(stock_code=stock_code)
        task_type = "top_holders_sync"
        parameters = {"stock_code": stock_code}

        task_result = task_manager.submit_task(
            db=db,
            task_name=task_name,
            task_type=task_type,
            parameters=parameters,
            allow_concurrent=False
        )

        if task_result.get("new_task"):
            _submit_async_task(async_task_runner,
                task_id=task_result["task_id"],
                task_func=sync_top_holders_func,
                task_kwargs=parameters,
                task_name=task_name,
            )

        return task_result
    except Exception as e:
        logger.error(f"Failed to submit top holders sync task: {e}")
        raise HTTPException(status_code=500, detail=str(e))
