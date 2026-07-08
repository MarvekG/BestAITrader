from __future__ import annotations

from typing import Any, Sequence


def to_float(value: Any) -> float | None:
    """把数据库或上下文原始值转换为浮点数。

    Args:
        value: 原始数值，可能是 Decimal、字符串或数值类型。

    Returns:
        可参与计算的浮点数；空值、无效字符串或无法转换时返回 None。
    """
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percent_change(current_value: Any, base_value: Any) -> float | None:
    """计算同口径数值相对基期的百分比变化。

    Args:
        current_value: 当前值。
        base_value: 基期值。

    Returns:
        ``(current - base) / base * 100``；缺少数值或基期为 0 时返回 None。
    """
    current = to_float(current_value)
    base = to_float(base_value)
    if current is None or base in (None, 0):
        return None
    return round((current - base) / base * 100, 4)


def average(values: Sequence[Any]) -> float | None:
    """计算有效数值的算术平均值。

    Args:
        values: 原始值序列。

    Returns:
        有效数值平均值；没有有效数值时返回 None。
    """
    numbers = [to_float(value) for value in values]
    valid_numbers = [value for value in numbers if value is not None]
    if not valid_numbers:
        return None
    return sum(valid_numbers) / len(valid_numbers)


def value_n_records_ago(records: Sequence[Any], field_name: str, offset: int) -> Any:
    """读取按最新优先排序序列中第 N 条记录的字段值。

    Args:
        records: 最新记录在前的对象序列。
        field_name: 字段名。
        offset: 从最新记录向前偏移的记录数，0 表示最新记录。

    Returns:
        对应记录字段值；记录不足时返回 None。
    """
    if len(records) <= offset:
        return None
    return getattr(records[offset], field_name, None)
