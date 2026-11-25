from app.config import Settings


def test_cloakbrowser_license_key_is_optional(monkeypatch):
    monkeypatch.delenv("CLOAKBROWSER_LICENSE_KEY", raising=False)

    settings = Settings(_env_file=None)

    assert settings.CLOAKBROWSER_LICENSE_KEY is None


def test_cloakbrowser_license_key_can_be_empty(monkeypatch):
    monkeypatch.setenv("CLOAKBROWSER_LICENSE_KEY", "")

    settings = Settings(_env_file=None)

    license_key = settings.CLOAKBROWSER_LICENSE_KEY

    assert license_key is None or not license_key.get_secret_value()


def test_cloakbrowser_license_key_is_loaded_as_secret(monkeypatch):
    license_key = "runtime-test-license-key"
    monkeypatch.setenv("CLOAKBROWSER_LICENSE_KEY", license_key)

    settings = Settings(_env_file=None)

    assert settings.CLOAKBROWSER_LICENSE_KEY.get_secret_value() == license_key
    assert license_key not in repr(settings.CLOAKBROWSER_LICENSE_KEY)
