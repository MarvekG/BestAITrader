"""数据源配置缓存。"""

from threading import Lock
from typing import Any

from app.core.data_source_settings import (
    NEWS_API_KEY_SETTING_KEY,
    TAVILY_API_KEY_SETTING_KEY,
    TUSHARE_API_SETTING_KEY,
    TUSHARE_TOKEN_SETTING_KEY,
)
from app.crud.system_setting import read_system_setting


DATA_SOURCE_SETTING_KEYS = (
    TUSHARE_API_SETTING_KEY,
    TUSHARE_TOKEN_SETTING_KEY,
    TAVILY_API_KEY_SETTING_KEY,
    NEWS_API_KEY_SETTING_KEY,
)

_data_source_config_cache: dict[str, str | list[str]] | None = None
_data_source_config_lock = Lock()


def _normalize_string_value(value: Any) -> str:
    """
    将 system_settings 中的值规范化为字符串配置。

    Args:
        value: system_settings 读取到的原始值。

    Returns:
        去除首尾空白后的字符串；非字符串返回空字符串。
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def _normalize_list_value(value: Any) -> list[str]:
    """
    将 system_settings 中的值规范化为字符串列表配置。

    Args:
        value: system_settings 读取到的原始值。

    Returns:
        去除空白和空项后的字符串列表。
    """
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return []


def _normalize_setting_value(key: str, value: Any) -> str | list[str]:
    """
    按配置 key 规范化 system_settings 值。

    Args:
        key: system_settings 配置 key。
        value: system_settings 读取到的原始值。

    Returns:
        字符串配置或字符串列表配置。
    """
    if key in {TAVILY_API_KEY_SETTING_KEY, NEWS_API_KEY_SETTING_KEY}:
        return _normalize_list_value(value)
    return _normalize_string_value(value)


async def get_data_source_config() -> dict[str, str | list[str]]:
    """
    读取数据源配置，优先返回进程内缓存。

    Returns:
        以 system_settings key 为键的配置字典。
    """
    global _data_source_config_cache
    if _data_source_config_cache is not None:
        return dict(_data_source_config_cache)

    with _data_source_config_lock:
        if _data_source_config_cache is not None:
            return dict(_data_source_config_cache)

    values = {
        key: _normalize_setting_value(key, await read_system_setting(key, default=""))
        for key in DATA_SOURCE_SETTING_KEYS
    }
    with _data_source_config_lock:
        if _data_source_config_cache is None:
            _data_source_config_cache = values
        return dict(_data_source_config_cache)


async def get_data_source_config_value(key: str) -> str:
    """
    从缓存配置中读取单个数据源配置值。

    Args:
        key: system_settings 配置 key。

    Returns:
        配置值；未配置时返回空字符串。
    """
    value = (await get_data_source_config()).get(key, "")
    return value if isinstance(value, str) else ""


async def get_data_source_config_list(key: str) -> list[str]:
    """
    从缓存配置中读取单个列表型数据源配置值。

    Args:
        key: system_settings 配置 key。

    Returns:
        配置列表；未配置时返回空列表。
    """
    value = (await get_data_source_config()).get(key, [])
    return list(value) if isinstance(value, list) else []


def invalidate_data_source_config_cache() -> None:
    """
    令数据源配置缓存失效。
    """
    global _data_source_config_cache
    with _data_source_config_lock:
        _data_source_config_cache = None
