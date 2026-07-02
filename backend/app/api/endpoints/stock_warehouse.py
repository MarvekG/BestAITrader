from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict, Any

from app.core.database import get_async_db
from app.crud.stock_warehouse import (
    create_stock_warehouse,
    delete_stock_warehouse,
    get_stock_warehouse_by_code,
    get_stock_warehouses,
    initialize_shanghai_50,
    update_stock_warehouse,
)
from app.schemas.stock_warehouse import (
    StockWarehouse as StockWarehouseSchema,
    StockWarehouseCreate,
    StockWarehouseUpdate
)
from app.core.security import get_current_user
from app.models.user import User
from app.core.i18n import i18n_service
from app.core.utils.formatters import StockCodeStandardizer
from app.models.data_storage import StockBasic

router = APIRouter()


@router.post("/init-shanghai50", status_code=status.HTTP_201_CREATED)
async def init_shanghai_50_stocks(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """初始化上证50成分股到股票仓库"""
    try:
        count = await initialize_shanghai_50(db, current_user.id)
        return {
            "message": f"Successfully initialized {count} "
                       "SSE 50 index stocks to the warehouse",
            "count": count
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Initialization failed: {str(e)}"
        )


@router.get("/", response_model=List[Dict[str, Any]])
async def get_stock_warehouses_list(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """获取股票仓库列表，包含从 StockBasic 表获取的 industry 和 market"""
    stocks = await get_stock_warehouses(db, current_user.id, skip=skip, limit=limit)

    # 获取所有股票代码对应的基本信息
    stock_codes = [StockCodeStandardizer.standardize(s.stock_code) for s in stocks]
    if stock_codes:
        stock_basics_result = await db.execute(
            select(StockBasic).where(StockBasic.stock_code.in_(stock_codes))
        )
        stock_basics = stock_basics_result.scalars().all()
    else:
        stock_basics = []

    # 创建股票代码到基本信息的映射
    basics_map = {sb.stock_code: sb for sb in stock_basics}

    # 构建返回结果，包含 industry 和 market
    result = []
    for stock in stocks:
        formatted_code = StockCodeStandardizer.standardize(stock.stock_code)
        basic = basics_map.get(formatted_code)
        result.append({
            "id": stock.id,
            "stock_code": stock.stock_code,
            "stock_name": (
                basic.name if basic and basic.name else "Unknown"
            ),
            "industry": basic.industry if basic else None,
            "market": basic.market if basic else None,
            "added_at": stock.added_at,
            "is_active": stock.is_active,
            "is_default": stock.is_default,
            "auto_analysis_enabled": stock.auto_analysis_enabled,
            "auto_analysis_frequency": stock.auto_analysis_frequency,
            "auto_analysis_time": stock.auto_analysis_time,
            "auto_analysis_trading_frequency": stock.auto_analysis_trading_frequency,
            "auto_analysis_trading_strategy": stock.auto_analysis_trading_strategy,
            "auto_analysis_run_immediately": stock.auto_analysis_run_immediately,
            "last_auto_analysis_at": stock.last_auto_analysis_at,
            "last_auto_analysis_session_id": stock.last_auto_analysis_session_id,
            "last_auto_analysis_task_id": stock.last_auto_analysis_task_id,
            "last_auto_analysis_error": stock.last_auto_analysis_error,
            "user_id": stock.user_id
        })

    return result


@router.get("/{stock_code}", response_model=StockWarehouseSchema)
async def get_stock_warehouse(
    stock_code: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """根据股票代码获取仓库中的股票"""
    stock = await get_stock_warehouse_by_code(
        db, stock_code=stock_code, user_id=current_user.id
    )
    if not stock:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock {stock_code} is not in the warehouse"
        )

    # 动态注入股票名称
    stock_name_result = await db.execute(select(StockBasic.name).where(StockBasic.stock_code == stock.stock_code))
    stock_name = stock_name_result.scalar_one_or_none()
    stock.stock_name = stock_name or "Unknown"

    return stock


@router.post(
    "/", response_model=StockWarehouseSchema,
    status_code=status.HTTP_201_CREATED
)
async def add_stock_to_warehouse(
    stock: StockWarehouseCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """添加股票到仓库"""
    from app.data.ingestors.manager import ingestor_manager
    from app.core.logger import get_logger

    logger = get_logger(__name__)

    # 检查股票是否已存在于仓库（使用标准化后的代码）
    standard_code = StockCodeStandardizer.standardize(stock.stock_code)
    existing_stock = await get_stock_warehouse_by_code(
        db, stock_code=standard_code, user_id=current_user.id
    )
    if existing_stock:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stock {standard_code} (input: {stock.stock_code}) "
                   "is already in the warehouse"
        )

    # 更新 schema 对象中的代码为标准化代码
    stock.stock_code = standard_code

    # 验证股票代码是否存在并获取最新基本信息（仅作为校验，不存储名称）
    stock_basic_result = await db.execute(select(StockBasic).where(StockBasic.stock_code == standard_code))
    stock_basic = stock_basic_result.scalar_one_or_none()

    if not stock_basic:
        # 尝试采集股票基本信息
        try:
            success = await ingestor_manager.fetch_and_ingest_stock_info(standard_code)
            if success:
                stock_basic_result = await db.execute(select(StockBasic).where(StockBasic.stock_code == standard_code))
                stock_basic = stock_basic_result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Failed to get stock info during warehouse add: {e}")

    if not stock_basic or not stock_basic.name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock {stock.stock_code} not found, please check the code"
        )

    return await create_stock_warehouse(db, stock=stock, user_id=current_user.id)


@router.put("/{stock_code}", response_model=StockWarehouseSchema)
async def update_stock_warehouse_item(
    stock_code: str,
    stock: StockWarehouseUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """更新仓库中的股票信息"""
    existing_stock = await get_stock_warehouse_by_code(
        db, stock_code=stock_code, user_id=current_user.id
    )
    if not existing_stock:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock {stock_code} is not in the warehouse"
        )

    return await update_stock_warehouse(db, existing_stock, stock)


@router.delete("/{stock_code}", status_code=status.HTTP_200_OK)
async def remove_stock_from_warehouse(
    stock_code: str,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    """从仓库中删除股票"""
    existing_stock = await get_stock_warehouse_by_code(
        db, stock_code=stock_code, user_id=current_user.id
    )
    if not existing_stock:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=i18n_service.t('warehouse.stock_not_in_warehouse').format(stock_code=stock_code)
        )

    # 检查是否有持仓
    from app.models.position import Position
    from app.models.account import Account

    active_position_result = await db.execute(
        select(Position)
        .join(Account)
        .where(
            Account.user_id == current_user.id,
            Position.stock_code == stock_code,
            Position.total_shares > 0,
        )
    )
    active_position = active_position_result.scalar_one_or_none()

    if active_position:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=i18n_service.t('warehouse.stock_has_position').format(
                stock_code=stock_code,
                shares=active_position.total_shares
            )
        )

    await delete_stock_warehouse(db, existing_stock)
    return {
        "message": i18n_service.t('warehouse.stock_removed_success').format(stock_code=stock_code)
    }
