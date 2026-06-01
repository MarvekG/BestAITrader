from datetime import datetime, timedelta

from sqlalchemy import and_

from app.models.data_storage import StockBasic


def get_basic_stock_filter_conds():
    """Shared base universe filter for A-share stock selection related flows."""
    six_months_ago = (datetime.now() - timedelta(days=180)).date()
    return and_(
        ~StockBasic.name.like("%ST%"),
        ~StockBasic.name.like("%退%"),
        StockBasic.market.in_(["主板", "中小板", "创业板", "科创板"]),
        StockBasic.list_date <= six_months_ago,
    )
