import json
import logging

from app.core.logger import JsonFormatter, REDACTED_VALUE, get_logger, redact_sensitive_data, redact_sensitive_text


def test_redact_sensitive_data_recursively():
    """Sensitive fields must be redacted throughout nested log payloads."""
    payload = {
        "token": "jwt",
        "nested": {
            "api_key": "key",
            "items": [
                {"Authorization": "Bearer secret"},
                {"safe": "value"},
            ],
        },
        "password_hash": "hash",
        "safe": "visible",
    }

    redacted = redact_sensitive_data(payload)

    assert redacted["token"] == REDACTED_VALUE
    assert redacted["nested"]["api_key"] == REDACTED_VALUE
    assert redacted["nested"]["items"][0]["Authorization"] == REDACTED_VALUE
    assert redacted["nested"]["items"][1]["safe"] == "value"
    assert redacted["password_hash"] == REDACTED_VALUE
    assert redacted["safe"] == "visible"


def test_context_logger_adapter_redacts_extra_fields(caplog):
    """Text log output from ContextLoggerAdapter must redact sensitive extra fields."""
    caplog.set_level(logging.INFO, logger="tests.logger_redaction")
    logger = get_logger("tests.logger_redaction")

    logger.info(
        "login payload",
        extra={
            "token": "jwt",
            "provider_payload": {
                "api_key": "provider-key",
                "safe": "visible",
            },
        },
    )

    message = caplog.records[0].getMessage()
    assert "jwt" not in message
    assert "provider-key" not in message
    assert f"token={REDACTED_VALUE}" in message
    assert "visible" in message


def test_redact_sensitive_text_key_value_pairs():
    """Sensitive key-value pairs embedded in plain messages must be redacted."""
    message = redact_sensitive_text("provider failed api_key=provider-key token: jwt safe=value")

    assert "provider-key" not in message
    assert "jwt" not in message
    assert f"api_key={REDACTED_VALUE}" in message
    assert f"token: {REDACTED_VALUE}" in message
    assert "safe=value" in message


def test_json_formatter_redacts_log_record_extra_fields():
    """JSON logs must redact sensitive fields added through LogRecord extra."""
    record = logging.LogRecord(
        name="tests.logger_redaction",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="provider call",
        args=(),
        exc_info=None,
    )
    record.msg = "provider call api_key=provider-key"
    record.provider_payload = {
        "secret": "provider-secret",
        "safe": "visible",
    }

    formatted = JsonFormatter().format(record)
    payload = json.loads(formatted)

    assert payload["provider_payload"]["secret"] == REDACTED_VALUE
    assert payload["provider_payload"]["safe"] == "visible"
    assert "provider-secret" not in formatted
    assert "provider-key" not in formatted
