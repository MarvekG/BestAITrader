from datetime import datetime
from typing import Any, Dict, List

from app.core.logger import get_logger

logger = get_logger(__name__)


def make_json_serializable(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    if isinstance(obj, dict):
        return {key: make_json_serializable(value) for key, value in obj.items()}
    return obj


def format_error(message: str, source: str, fatal: bool = False) -> List[Dict[str, Any]]:
    """
    生成新闻插件统一错误结果。

    Args:
        message: 面向调用方展示的错误信息。
        source: 新闻插件来源标识。
        fatal: 是否为插件不可用等致命错误。

    Returns:
        单元素错误结果列表，保持与新闻搜索结果列表契约一致。
    """
    item: Dict[str, Any] = {"error": message, "source": source}
    if fatal:
        item["fatal"] = True
    return [item]


def ensure_source(results: Any, source: str) -> List[Dict[str, Any]]:
    serializable = make_json_serializable(results)
    if not isinstance(serializable, list):
        return format_error(
            f"Plugin '{source}' returned unexpected payload type: {type(serializable).__name__}",
            source,
        )

    normalized: List[Dict[str, Any]] = []
    for item in serializable:
        if isinstance(item, dict):
            row = dict(item)
            row.setdefault("source", source)
            normalized.append(row)
        else:
            normalized.append({
                "content": str(item),
                "source": source,
                "type": "raw_result",
            })
    return normalized
