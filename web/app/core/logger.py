import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any


_RESERVED_LOG_RECORD_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__
) | {"asctime", "context", "message"}


def _serialize_log_value(value: Any) -> Any:
    """
    将日志上下文字段转换为 JSON 可序列化值。

    Args:
        value: 原始日志字段值。

    Returns:
        可被 JSON 序列化的日志字段值。
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _serialize_log_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_serialize_log_value(item) for item in value]
    return str(value)


def _collect_extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """
    提取通过 logging extra 传入的上下文字段。

    Args:
        record: 标准日志记录。

    Returns:
        已过滤并序列化的上下文字段。
    """
    return {
        key: _serialize_log_value(value)
        for key, value in record.__dict__.items()
        if key not in _RESERVED_LOG_RECORD_FIELDS and not key.startswith("_")
    }


class ContextFormatter(logging.Formatter):
    """在标准日志末尾追加结构化上下文字段。"""

    def format(self, record: logging.LogRecord) -> str:
        """
        将日志记录格式化为带上下文的可读字符串。

        Args:
            record: 标准日志记录。

        Returns:
            可读日志字符串。
        """
        extra_fields = _collect_extra_fields(record)
        record.context = ""
        if extra_fields:
            record.context = f" context={json.dumps(extra_fields, ensure_ascii=False, sort_keys=True)}"
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """Web 服务 JSON 日志格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        """
        将日志记录格式化为 JSON 字符串。

        Args:
            record: 标准日志记录。

        Returns:
            JSON 格式日志。
        """
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "process_id": record.process,
            "source": getattr(record, "source", "web"),
        }
        log_obj.update(_collect_extra_fields(record))
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)


def get_logger(name: str = "web_fetch") -> logging.Logger:
    """
    获取 Web 服务日志记录器。

    Args:
        name: 日志记录器名称。

    Returns:
        标准日志记录器。
    """
    return logging.getLogger(name)


logger = get_logger()
