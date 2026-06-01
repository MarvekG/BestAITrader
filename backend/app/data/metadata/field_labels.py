import json
import logging
from pathlib import Path
from typing import Any, Dict

from app.core.config import settings

logger = logging.getLogger(__name__)

_table_field_label_config: Dict[str, Any] | None = None


def _load_table_field_label_config() -> Dict[str, Any]:
    """
    加载表字段展示标签配置。

    Returns:
        表字段标签配置。
    """
    global _table_field_label_config
    if _table_field_label_config is not None:
        return _table_field_label_config

    config_path = Path(__file__).resolve().parent / "table_field_labels.json"
    try:
        _table_field_label_config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error("Failed to load table_field_labels.json: %s", exc)
        _table_field_label_config = {}
    return _table_field_label_config


def get_table_field_label(table: str, standard_key: str) -> str:
    """
    获取指定表字段的展示标签。

    Args:
        table: 表名。
        standard_key: 标准字段名。

    Returns:
        当前系统语言下的字段展示标签；未配置时返回原字段名。
    """
    config = _load_table_field_label_config()
    target_lang = (settings.SYSTEM_LANGUAGE or "zh").lower()
    table_config = config.get(table, {})
    lang_config = table_config.get(target_lang, {})
    return lang_config.get(standard_key, standard_key)
