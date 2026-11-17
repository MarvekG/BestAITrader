"""
Pydantic Settings 启动时非空校验单测。

注：conftest.py 会注入测试安全值；本测试通过 monkeypatch 重置 env 触发校验失败。
"""
import importlib
import sys

import pytest
from pydantic import ValidationError


def _reload_config():
    sys.modules.pop("app.core.config", None)
    return importlib.import_module("app.core.config")


def test_secret_key_empty_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "")
    with pytest.raises(ValidationError) as ei:
        _reload_config()
    assert "SECRET_KEY" in str(ei.value)


def test_superuser_password_empty_rejected(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "")
    with pytest.raises(ValidationError) as ei:
        _reload_config()
    assert "FIRST_SUPERUSER_PASSWORD" in str(ei.value)


def test_valid_values_pass(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "test-password")
    module = _reload_config()
    assert module.settings.SECRET_KEY == "test-secret-key"
    assert module.settings.FIRST_SUPERUSER_PASSWORD == "test-password"


def test_context_window_non_positive_rejected(monkeypatch):
    """上下文窗口必须大于 0。"""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "test-password")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "0")
    with pytest.raises(ValidationError) as ei:
        _reload_config()
    assert "LLM_CONTEXT_WINDOW_TOKENS" in str(ei.value)


def test_context_compaction_trigger_ratio_out_of_range_rejected(monkeypatch):
    """压缩触发比例必须严格位于 0 和 1 之间。"""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "test-password")
    monkeypatch.setenv("LLM_CONTEXT_COMPACTION_TRIGGER_RATIO", "1")
    with pytest.raises(ValidationError) as ei:
        _reload_config()
    assert "LLM_CONTEXT_COMPACTION_TRIGGER_RATIO" in str(ei.value)


def test_context_summary_output_non_positive_rejected(monkeypatch):
    """摘要输出上限必须大于 0。"""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "test-password")
    monkeypatch.setenv("LLM_CONTEXT_COMPACTION_SUMMARY_OUTPUT_TOKENS", "0")
    with pytest.raises(ValidationError) as ei:
        _reload_config()
    assert "LLM_CONTEXT_COMPACTION_SUMMARY_OUTPUT_TOKENS" in str(ei.value)


def test_context_summary_output_must_be_less_than_window(monkeypatch):
    """摘要输出上限必须小于上下文窗口。"""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("FIRST_SUPERUSER_PASSWORD", "test-password")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW_TOKENS", "1000")
    monkeypatch.setenv("LLM_CONTEXT_COMPACTION_SUMMARY_OUTPUT_TOKENS", "1000")
    with pytest.raises(ValidationError) as ei:
        _reload_config()
    assert "LLM_CONTEXT_COMPACTION_SUMMARY_OUTPUT_TOKENS" in str(ei.value)
