from datetime import date, datetime
from typing import Any

from sqlalchemy.sql.sqltypes import Date as SQLDate, DateTime as SQLDateTime


def coerce_filter_value_for_column(column: Any, value: Any) -> Any:
    """根据 SQLAlchemy 字段类型转换过滤值，避免 Date 字段与字符串直接比较。"""
    column_type = column.type
    if isinstance(value, list):
        return [coerce_filter_value_for_column(column, item) for item in value]
    if not isinstance(value, str):
        return value

    normalized_value = value.strip()
    if not normalized_value:
        return value

    if isinstance(column_type, SQLDateTime):
        iso_value = normalized_value.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_value)
    if isinstance(column_type, SQLDate):
        date_part = normalized_value.split("T", 1)[0].split(" ", 1)[0]
        return date.fromisoformat(date_part)
    return value
