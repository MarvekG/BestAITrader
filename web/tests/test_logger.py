import json
import logging

from app.core.logger import ContextFormatter, JsonFormatter


def test_context_formatter_appends_extra_fields() -> None:
    """验证标准日志格式器会输出 extra 上下文字段。"""
    formatter = ContextFormatter("%(message)s%(context)s")
    record = logging.LogRecord("web", logging.INFO, __file__, 1, "started", (), None)
    record.url = "https://example.com/page"
    record.duration_ms = 12

    formatted = formatter.format(record)

    assert formatted.startswith("started context=")
    assert '"url": "https://example.com/page"' in formatted
    assert '"duration_ms": 12' in formatted


def test_json_formatter_includes_extra_fields() -> None:
    """验证 JSON 日志格式器会输出 extra 上下文字段。"""
    formatter = JsonFormatter()
    record = logging.LogRecord("web", logging.INFO, __file__, 1, "completed", (), None)
    record.url = "https://example.com/page"
    record.duration_ms = 12

    payload = json.loads(formatter.format(record))

    assert payload["message"] == "completed"
    assert payload["url"] == "https://example.com/page"
    assert payload["duration_ms"] == 12
