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
