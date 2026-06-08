from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from app.core.utils.converters import safe_date, safe_float, safe_isoformat, safe_string, safe_uuid


def test_safe_float_handles_defaults_and_non_finite_values():
    """验证浮点转换在默认值和非有限值场景下行为稳定。"""

    assert safe_float(Decimal("12.34")) == 12.34
    assert safe_float("bad") is None
    assert safe_float(None, 0.0) == 0.0
    assert math.isnan(safe_float("nan"))
    assert safe_float("nan", 1.0, allow_non_finite=False) == 1.0
    assert safe_float("inf", 1.0, allow_non_finite=False) == 1.0


def test_safe_isoformat_handles_dates_and_plain_values():
    """验证日期时间和普通文本的 ISO 文本转换行为。"""

    value = datetime(2026, 6, 8, 9, 30)
    assert safe_isoformat(value) == "2026-06-08T09:30:00"
    assert safe_isoformat("2026-06-08") == "2026-06-08"
    assert safe_isoformat(None) is None


def test_safe_date_extracts_date_from_common_inputs():
    """验证日期对象可从常见输入中安全提取。"""

    assert safe_date(datetime(2026, 6, 8, 9, 30)) == date(2026, 6, 8)
    assert safe_date(date(2026, 6, 8)) == date(2026, 6, 8)
    assert safe_date("2026-06-08T09:30:00") == date(2026, 6, 8)
    assert safe_date("bad") is None


def test_safe_string_and_uuid_normalize_inputs():
    """验证字符串和 UUID 的宽容规范化行为。"""

    uuid_value = UUID("12345678-1234-5678-1234-567812345678")
    assert safe_string("  text  ") == "text"
    assert safe_string("  ") is None
    assert safe_uuid(uuid_value) == uuid_value
    assert safe_uuid(str(uuid_value)) == uuid_value
    assert safe_uuid("bad") is None
