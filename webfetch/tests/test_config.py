import pytest
from pydantic import ValidationError

from app.config import Settings


def test_cloakbrowser_license_key_is_required(monkeypatch):
    monkeypatch.delenv("CLOAKBROWSER_LICENSE_KEY", raising=False)

    with pytest.raises(ValidationError, match="CLOAKBROWSER_LICENSE_KEY"):
        Settings(_env_file=None)


def test_cloakbrowser_license_key_is_loaded_as_secret(monkeypatch):
    license_key = "runtime-test-license-key"
    monkeypatch.setenv("CLOAKBROWSER_LICENSE_KEY", license_key)

    settings = Settings(_env_file=None)

    assert settings.CLOAKBROWSER_LICENSE_KEY.get_secret_value() == license_key
    assert license_key not in repr(settings.CLOAKBROWSER_LICENSE_KEY)
