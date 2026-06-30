from datetime import datetime, timedelta

from sqlalchemy import and_

from app.models.data_storage import StockBasic


def get_basic_stock_filter_conds():
    """构建基础 A 股同步过滤条件。

    Returns:
        SQLAlchemy 过滤表达式，用于排除 ST、退市、非核心交易板块和上市未满半年的股票。
    """
    six_months_ago = (datetime.now() - timedelta(days=180)).date()
    return and_(
        ~StockBasic.name.like("%ST%"),
        ~StockBasic.name.like("%退%"),
        StockBasic.market.in_(["主板", "中小板", "创业板", "科创板"]),
        StockBasic.list_date <= six_months_ago,
    )
