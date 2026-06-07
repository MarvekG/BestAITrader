import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

from app.core.i18n import i18n_service

logger = logging.getLogger(__name__)

__all__ = ["format_payload_values"]

_table_field_unit_config: Dict[str, Any] | None = None


def _load_table_field_unit_config() -> Dict[str, Any]:
    """加载标准表字段单位配置。

    Returns:
        标准表字段单位配置。
    """
    global _table_field_unit_config
    if _table_field_unit_config is not None:
        return _table_field_unit_config

    config_path = Path(__file__).resolve().parent / "table_field_units.json"
    try:
        _table_field_unit_config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to load table_field_units.json: %s", exc)
        _table_field_unit_config = {}
    return _table_field_unit_config


def _localized_unit(unit_key: str, *, language: str | None = None) -> str:
    """使用 i18n key 翻译数值后缀单位。

    Args:
        unit_key: ``units.*`` 格式的单位翻译键。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        对应语言下的单位后缀；缺少翻译时返回原始 key。
    """
    if language:
        return i18n_service.get(unit_key, default=unit_key, lang=language)
    return i18n_service.t(unit_key)


def _to_number(value: Any) -> float | None:
    """把原始标准值转换为浮点数。

    Args:
        value: 来自数据库或标准 payload 的原始值。

    Returns:
        转换后的浮点数；无法转换或为空时返回 None。
    """
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned or cleaned.lower() in {"none", "null", "nan"}:
            return None
        cleaned = cleaned.replace(",", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: Any, *, precision: int = 2) -> str | None:
    """把数值格式化为指定精度的字符串。

    Args:
        value: 原始数值。
        precision: 最多保留的小数位数。

    Returns:
        去掉无意义尾零后的数字字符串；无法转换时返回 None。
    """
    number = _to_number(value)
    if number is None:
        return None
    text = f"{number:.{precision}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    return text


def _format_number_with_unit(
    value: Any,
    unit_key: str,
    *,
    precision: int = 2,
    language: str | None = None,
) -> str | None:
    """把数值格式化为紧跟单位的展示字符串。

    Args:
        value: 原始数值。
        unit_key: ``units.*`` 格式的单位翻译键。
        precision: 最多保留的小数位数。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        形如 ``12.34元``、``12.34 CNY`` 或 ``5%`` 的字符串；无法转换时返回 None。
    """
    text = _format_number(value, precision=precision)
    if text is None:
        return None
    return f"{text}{_localized_unit(unit_key, language=language)}"


def _get_field_unit_config(table_name: str, field_name: str) -> dict[str, Any] | None:
    """读取标准表字段单位配置。

    Args:
        table_name: 标准表名或虚拟上下文表名。
        field_name: 标准字段名。

    Returns:
        字段单位配置；不存在时返回 None。
    """
    table_config = _load_table_field_unit_config().get(table_name, {})
    config = table_config.get(field_name)
    return dict(config) if isinstance(config, dict) else None


def _format_field_value(
    table_name: str,
    field_name: str,
    value: Any,
    *,
    language: str | None = None,
) -> Any:
    """根据标准表字段单位配置返回 LLM 展示值。

    Args:
        table_name: 标准表名或虚拟上下文表名。
        field_name: 标准字段名。
        value: 原始标准数值。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        带单位的展示值；字段未配置单位或无法转换时返回原值。
    """
    config = _get_field_unit_config(table_name, field_name)
    if not config:
        return value

    number = _to_number(value)
    if number is None:
        return value

    display_scale = config.get("display_scale", 1)
    precision = int(config.get("precision", 2))
    unit_key = config.get("unit")
    if not unit_key:
        return value

    return _format_number_with_unit(
        number * float(display_scale),
        unit_key,
        precision=precision,
        language=language,
    )


def format_payload_values(
    table_name: str,
    payload: Any,
    *,
    language: str | None = None,
) -> Any:
    """按标准表字段单位配置格式化嵌套 payload。

    Args:
        table_name: 标准表名或虚拟上下文表名。
        payload: 原始嵌套数据。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        字段值已按配置补单位的新嵌套数据。
    """
    if isinstance(payload, dict):
        result = {}
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                result[key] = format_payload_values(table_name, value, language=language)
            else:
                result[key] = _format_field_value(table_name, key, value, language=language)
        return result
    if isinstance(payload, list):
        return [format_payload_values(table_name, item, language=language) for item in payload]
    return payload
