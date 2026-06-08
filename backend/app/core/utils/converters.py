from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, overload
from uuid import UUID


@overload
def safe_float(value: Any, default: None = None, *, allow_non_finite: bool = True) -> float | None:
    ...


@overload
def safe_float(value: Any, default: float, *, allow_non_finite: bool = True) -> float:
    ...


def safe_float(value: Any, default: float | None = None, *, allow_non_finite: bool = True) -> float | None:
    """安全转换输入值为浮点数。

    Args:
        value: 可能来自数据库、接口或模型输出的原始数值。
        default: 输入为空或无法转换时返回的默认值。
        allow_non_finite: 是否允许 ``NaN`` 和无穷大这类非有限浮点值。

    Returns:
        转换后的浮点数；无法转换时返回 ``default``。
    """
    if value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not allow_non_finite and (math.isnan(number) or math.isinf(number)):
        return default
    return number


def safe_isoformat(value: Any) -> str | None:
    """安全格式化具备日期时间语义的值。

    Args:
        value: 日期、时间、具备 ``isoformat`` 方法的对象或普通文本值。

    Returns:
        ISO 格式文本；输入为空时返回 ``None``。
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def safe_date(value: Any) -> date | None:
    """安全提取日期对象。

    Args:
        value: 日期、日期时间或可按 ISO 日期前缀解析的字符串。

    Returns:
        解析得到的日期对象；输入为空或无法解析时返回 ``None``。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def safe_string(value: Any) -> str | None:
    """安全转换输入值为去空白字符串。

    Args:
        value: 任意可字符串化的原始值。

    Returns:
        去空白后的字符串；输入为空或结果为空字符串时返回 ``None``。
    """
    text = str(value or "").strip()
    return text or None


def safe_uuid(value: Any) -> UUID | None:
    """安全转换输入值为 UUID。

    Args:
        value: UUID 对象或可解析为 UUID 的字符串。

    Returns:
        UUID 对象；输入为空或无法解析时返回 ``None``。
    """
    if isinstance(value, UUID):
        return value
    text = safe_string(value)
    if not text:
        return None
    try:
        return UUID(text)
    except ValueError:
        return None
