import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict

from app.core.i18n import i18n_service

logger = logging.getLogger(__name__)

__all__ = ["format_payload_values", "get_field_unit_metadata", "get_table_unit_metadata"]

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


def _localized_unit(unit_key: Any, *, language: str | None = None) -> str:
    """使用 i18n key 翻译数值后缀单位。

    Args:
        unit_key: ``units.*`` 格式的单位翻译键，或单位翻译键列表。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        对应语言下的单位后缀；缺少翻译时返回原始 key。
    """
    if isinstance(unit_key, list):
        return "".join(_localized_unit(item, language=language) for item in unit_key)
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
    unit_key: Any,
    *,
    precision: int = 2,
    language: str | None = None,
) -> str | None:
    """把数值格式化为紧跟单位的展示字符串。

    Args:
        value: 原始数值。
        unit_key: ``units.*`` 格式的单位翻译键，或单位翻译键列表。
        precision: 最多保留的小数位数。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        形如 ``12.34元``、``12.34 CNY`` 或 ``5%`` 的字符串；无法转换时返回 None。
    """
    text = _format_number(value, precision=precision)
    if text is None:
        return None
    return f"{text}{_localized_unit(unit_key, language=language)}"


def _get_target_language(language: str | None = None) -> str:
    """获取当前展示语言代码。

    Args:
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        当前展示语言代码。
    """
    from app.core.config import settings

    return (language or settings.SYSTEM_LANGUAGE or "zh").lower()


def _pick_language_config(config: dict[str, Any], *, language: str | None = None) -> dict[str, Any] | None:
    """按语言从字段单位配置中选择展示配置。

    Args:
        config: 字段单位配置。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        当前语言下的展示配置；不存在时返回 None。
    """
    target_language = _get_target_language(language)
    language_config = config.get(target_language)
    return language_config if isinstance(language_config, dict) else None


def _resolve_scale(scale: Any) -> float:
    """把展示倍率配置解析为浮点数。

    Args:
        scale: 数字倍率。
    Returns:
        当前语言下应使用的展示倍率。
    """
    try:
        return float(scale)
    except (TypeError, ValueError):
        return 1.0


def _resolve_field_display(config: dict[str, Any], *, language: str | None = None) -> tuple[Any, float]:
    """解析字段在当前语言下的单位和展示倍率。

    Args:
        config: 字段单位配置。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        当前语言下的单位翻译键和展示倍率。
    """
    language_config = _pick_language_config(config, language=language)
    if not language_config:
        return None, 1.0

    unit_key = language_config.get("unit")
    scale = language_config.get("scale", 1)
    return unit_key, _resolve_scale(scale)


def get_field_unit_metadata(
    table_name: str,
    field_name: str,
    *,
    language: str | None = None,
) -> dict[str, Any] | None:
    """读取字段单位展示元数据。

    Args:
        table_name: 标准表名或虚拟上下文表名。
        field_name: 标准字段名。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        包含单位文案、单位 i18n key、展示倍率和精度的元数据；字段未配置单位时返回 None。
    """
    config = _get_field_unit_config(table_name, field_name)
    if not config:
        return None

    unit_key, scale = _resolve_field_display(config, language=language)
    if not unit_key:
        return None

    return {
        "unit": _localized_unit(unit_key, language=language),
        "unit_key": unit_key,
        "scale": scale,
        "precision": int(config.get("precision", 2)),
    }


def get_table_unit_metadata(table_name: str, *, language: str | None = None) -> dict[str, dict[str, Any]]:
    """读取标准表已显式配置的字段单位元数据。

    Args:
        table_name: 标准表名或虚拟上下文表名。
        language: 可选语言代码；为空时使用系统语言。

    Returns:
        以字段名为 key 的单位元数据映射；未配置单位的表返回空字典。
    """
    table_config = _load_table_field_unit_config().get(table_name, {})
    if not isinstance(table_config, dict):
        return {}

    result = {}
    default_ref = table_config.get("$default_ref")
    if isinstance(default_ref, str):
        default_metadata = get_field_unit_metadata(table_name, "$default", language=language)
        if default_metadata:
            result["$default"] = {"unit": default_metadata["unit"]}

    for field_name in table_config:
        if field_name.startswith("$"):
            continue
        metadata = get_field_unit_metadata(table_name, field_name, language=language)
        if metadata:
            result[field_name] = {"unit": metadata["unit"]}
    return result


def _get_field_unit_config(table_name: str, field_name: str) -> dict[str, Any] | None:
    """读取标准表字段单位配置。

    单位配置支持三类复用语义：
    - ``$default_ref``：表级默认单位。字段没有单独配置时，默认引用 ``common_units`` 中的单位，
      例如财报三表默认使用 ``cny_to_hundred_million``。
    - ``$exclude_fields``：表级排除列表。字段在列表中时不补单位，直接返回原值，
      例如 ``report_date``、``currency``、``data_source``。
    - ``$ref``：字段级公共单位引用。字段不必重复写完整 ``zh``、``en``、``precision`` 配置，
      可直接引用 ``common_units``，例如 ``basic_eps`` 使用 ``cny``、``total_share`` 使用 ``shares``。

    Args:
        table_name: 标准表名或虚拟上下文表名。
        field_name: 标准字段名。

    Returns:
        字段单位配置；不存在时返回 None。
    """
    all_config = _load_table_field_unit_config()
    table_config = all_config.get(table_name, {})
    if not isinstance(table_config, dict):
        return None

    excluded_fields = table_config.get("$exclude_fields", [])
    if isinstance(excluded_fields, list) and field_name in excluded_fields:
        return None

    config = table_config.get(field_name)
    if not isinstance(config, dict):
        default_ref = table_config.get("$default_ref")
        if not isinstance(default_ref, str):
            return None
        config = {"$ref": default_ref}

    ref_name = config.get("$ref")
    if isinstance(ref_name, str):
        ref_config = all_config.get("common_units", {}).get(ref_name)
        if isinstance(ref_config, dict):
            merged = dict(ref_config)
            merged.update({key: value for key, value in config.items() if key != "$ref"})
            return merged
    return dict(config)


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

    unit_key, scale = _resolve_field_display(config, language=language)
    precision = int(config.get("precision", 2))
    if not unit_key:
        return value

    return _format_number_with_unit(
        number * scale,
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
