import logging
import json
import re
from collections.abc import Mapping

from app.core.request_context import get_request_id


_original_record_factory = logging.getLogRecordFactory()
REDACTED_VALUE = "[REDACTED]"
SENSITIVE_FIELD_FRAGMENTS = (
    "token",
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "cookie",
    "credential",
)
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)\b(token|api_key|apikey|authorization|password|secret|cookie|credential)"
    r"(\s*[:=]\s*)"
    r"([^,\s&|}]+)"
)


def _record_factory(*args, **kwargs):
    record = _original_record_factory(*args, **kwargs)
    if not getattr(record, "request_id", None):
        record.request_id = get_request_id() or "-"
    if not getattr(record, "source", None):
        record.source = "backend"
    return record


logging.setLogRecordFactory(_record_factory)


def _is_sensitive_key(key: object) -> bool:
    key_text = str(key).lower()
    return any(fragment in key_text for fragment in SENSITIVE_FIELD_FRAGMENTS)


def redact_sensitive_text(value: str) -> str:
    """Redact obvious sensitive key-value pairs embedded in log messages."""
    return SENSITIVE_TEXT_PATTERN.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED_VALUE}", value)


def redact_sensitive_data(value):
    """Recursively redact sensitive values from log payloads."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED_VALUE if _is_sensitive_key(key) else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, set):
        return {redact_sensitive_data(item) for item in value}
    return value


def _format_log_value(value):
    value = redact_sensitive_data(value)
    if isinstance(value, str):
        return value if " " not in value and "=" not in value else json.dumps(value, ensure_ascii=False)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _format_extra_fields(extra: dict) -> str:
    return " ".join(
        f"{key}={_format_log_value(value)}"
        for key, value in redact_sensitive_data(extra).items()
        if key != "request_id"
    )


class ContextLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = kwargs.pop("extra", None) or {}
        merged = {**(self.extra or {}), **extra}
        if merged:
            msg = f"{msg} | {_format_extra_fields(merged)}"
        return msg, kwargs


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": redact_sensitive_text(record.getMessage()),
            "module": record.module,
            "func": record.funcName,
            "line": record.lineno,
            "process_id": record.process,
            "source": getattr(record, "source", "backend"),  # 默认为 backend
        }

        # 记录异常堆栈
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # 合并额外的字段 (extra={...})
        # 排除标准 LogRecord 属性，只保留 extra 传入的
        standard_attrs = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())
        extra_attrs = {k: v for k, v in record.__dict__.items() if k not in standard_attrs and k not in log_obj}
        if extra_attrs:
            log_obj.update(redact_sensitive_data(extra_attrs))

        return json.dumps(log_obj, ensure_ascii=False)


def get_logger(name: str = "trading_system"):
    """获取后端日志记录器"""
    return ContextLoggerAdapter(logging.getLogger(name), {})


# 为了兼容旧代码，直接实例化并导出
logger = get_logger()
