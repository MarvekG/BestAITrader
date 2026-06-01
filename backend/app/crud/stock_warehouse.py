from sqlalchemy.orm import Session
from typing import List, Optional

from app.models.stock_warehouse import StockWarehouse
from app.schemas.stock_warehouse import (
    StockWarehouseCreate, StockWarehouseUpdate
)
from app.core.logger import get_logger
from app.core.utils.formatters import StockCodeStandardizer
from sqlalchemy.exc import IntegrityError


# 获取日志记录器
logger = get_logger(__name__)

# 上证50成分股（默认股票）
SHANGHAI_50_STOCKS = [
    {"stock_code": "600000"}, {"stock_code": "600009"}, {"stock_code": "600016"},
    {"stock_code": "600028"}, {"stock_code": "600036"}, {"stock_code": "600048"},
    {"stock_code": "600050"}, {"stock_code": "600089"}, {"stock_code": "600104"},
    {"stock_code": "600111"}, {"stock_code": "600196"}, {"stock_code": "600276"},
    {"stock_code": "600309"}, {"stock_code": "600340"}, {"stock_code": "600438"},
    {"stock_code": "600482"}, {"stock_code": "600519"}, {"stock_code": "600547"},
    {"stock_code": "600585"}, {"stock_code": "600637"}, {"stock_code": "600690"},
    {"stock_code": "600741"}, {"stock_code": "600887"}, {"stock_code": "600900"},
    {"stock_code": "600919"}, {"stock_code": "600958"}, {"stock_code": "600999"},
    {"stock_code": "601006"}, {"stock_code": "601088"}, {"stock_code": "601166"},
    {"stock_code": "601169"}, {"stock_code": "601186"}, {"stock_code": "601211"},
    {"stock_code": "601229"}, {"stock_code": "601288"}, {"stock_code": "601318"},
    {"stock_code": "601328"}, {"stock_code": "601336"}, {"stock_code": "601390"},
    {"stock_code": "601398"}, {"stock_code": "601601"}, {"stock_code": "601628"},
    {"stock_code": "601668"}, {"stock_code": "601688"}, {"stock_code": "601727"},
    {"stock_code": "601766"}, {"stock_code": "601857"}, {"stock_code": "601888"},
    {"stock_code": "601939"}, {"stock_code": "601988"}, {"stock_code": "601998"}
]


def get_stock_warehouse_by_code(
    db: Session, stock_code: str, user_id: int
) -> Optional[StockWarehouse]:
    """根据股票代码获取仓库中的股票（自动标准化代码）"""
    standard_code = StockCodeStandardizer.standardize(stock_code)
    return db.query(StockWarehouse).filter(
        StockWarehouse.stock_code == standard_code,
        StockWarehouse.user_id == user_id
    ).first()


def get_stock_warehouses(
    db: Session, user_id: int, skip: int = 0, limit: int = 100
) -> List[StockWarehouse]:
    """获取用户的所有仓库股票"""
    return db.query(StockWarehouse).filter(
        StockWarehouse.user_id == user_id
    ).offset(skip).limit(limit).all()


def get_active_stock_warehouses(
    db: Session, user_id: int
) -> List[StockWarehouse]:
    """获取用户的所有活跃仓库股票"""
    return db.query(StockWarehouse).filter(
        StockWarehouse.user_id == user_id,
        StockWarehouse.is_active
    ).all()


def get_default_stock_warehouses(
    db: Session, user_id: int
) -> List[StockWarehouse]:
    """获取用户的所有默认（上证50）仓库股票"""
    return db.query(StockWarehouse).filter(
        StockWarehouse.user_id == user_id,
        StockWarehouse.is_default
    ).all()


def create_stock_warehouse(
    db: Session, stock: StockWarehouseCreate, user_id: int
) -> StockWarehouse:
    """添加股票到用户的仓库（自动标准化代码）"""
    standard_code = StockCodeStandardizer.standardize(stock.stock_code)

    # 再次检查是否已存在（逻辑层双重保证）
    existing = get_stock_warehouse_by_code(db, standard_code, user_id)
    if existing:
        return existing

    db_stock = StockWarehouse(
        stock_code=standard_code,
        is_active=stock.is_active,
        is_default=stock.is_default,
        auto_analysis_enabled=bool(stock.auto_analysis_enabled),
        auto_analysis_frequency=stock.auto_analysis_frequency or "daily",
        auto_analysis_time=stock.auto_analysis_time or "09:35",
        auto_analysis_trading_frequency=stock.auto_analysis_trading_frequency or "中长线持有 (Position Trading)",
        auto_analysis_trading_strategy=stock.auto_analysis_trading_strategy or "价值投资 (Value Investing)",
        auto_analysis_run_immediately=bool(stock.auto_analysis_run_immediately),
        user_id=user_id
    )
    try:
        db.add(db_stock)
        db.commit()
        db.refresh(db_stock)
        return db_stock
    except IntegrityError:
        db.rollback()
        # 处理并发冲突，返回已存在的记录
        return get_stock_warehouse_by_code(db, standard_code, user_id)


def update_stock_warehouse(
    db: Session, db_stock: StockWarehouse, stock_update: StockWarehouseUpdate
) -> StockWarehouse:
    """更新仓库中的股票"""
    update_data = stock_update.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_stock, field, value)

    db.add(db_stock)
    db.commit()
    db.refresh(db_stock)
    return db_stock


def delete_stock_warehouse(db: Session, db_stock: StockWarehouse) -> None:
    """从仓库中删除股票"""
    db.delete(db_stock)
    db.commit()


def initialize_shanghai_50(db: Session, user_id: int) -> int:
    """初始化上证50成分股到用户的仓库"""
    created_count = 0

    for stock_info in SHANGHAI_50_STOCKS:
        existing_stock = get_stock_warehouse_by_code(
            db, stock_info["stock_code"], user_id
        )
        if not existing_stock:
            stock_create = StockWarehouseCreate(
                stock_code=stock_info["stock_code"],
                is_default=True
            )
            create_stock_warehouse(db, stock_create, user_id)
            created_count += 1

    return created_count


def clear_default_stock_warehouses(db: Session, user_id: int) -> int:
    """清空用户的所有默认股票"""
    deleted_count = db.query(StockWarehouse).filter(
        StockWarehouse.user_id == user_id,
        StockWarehouse.is_default
    ).delete()
    db.commit()
    return deleted_count
