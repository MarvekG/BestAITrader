from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.i18n import I18nService
from app.main import app

client = TestClient(app)


def test_i18n_service_loading():
    service = I18nService()
    # Accessing protected member for testing purpose
    assert 'zh' in service._locales
    assert 'en' in service._locales
    assert service.get_locale('zh')['common']['success'] == '操作成功'
    assert service.get_locale('en')['common']['success'] == 'Success'


def test_i18n_translate():
    service = I18nService()
    with patch("app.core.config.settings.SYSTEM_LANGUAGE", "zh"):
        assert service.t('common.success') == '操作成功'
        assert service.t('non.existent.key') == 'non.existent.key'

    with patch("app.core.config.settings.SYSTEM_LANGUAGE", "en"):
        assert service.t('common.success') == 'Success'


def test_i18n_api_endpoint():
    response = client.get("/api/v1/general/i18n/zh")
    assert response.status_code == 200
    data = response.json()
    assert data['common']['success'] == '操作成功'
    assert 'stock_basic' in data

    response = client.get("/api/v1/general/i18n/en")
    assert response.status_code == 200
    data = response.json()
    assert data['common']['success'] == 'Success'

    response = client.get("/api/v1/general/i18n/fr")  # Non-existent
    assert response.status_code == 200
    # Should fallback to default 'zh' logic
    data = response.json()
    assert 'common' in data
    assert data['common']['success'] == '操作成功'


def test_system_language_api_reads_and_updates_runtime_language(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM_LANGUAGE", "zh")

    response = client.get("/api/v1/general/language", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {
        "language": "zh",
        "supported_languages": ["zh", "en"],
    }

    response = client.put("/api/v1/general/language", json={"language": "en"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["language"] == "en"
    assert settings.SYSTEM_LANGUAGE == "en"

    response = client.get("/api/v1/general/language", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["language"] == "en"


def test_system_language_api_rejects_unsupported_language(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "SYSTEM_LANGUAGE", "zh")

    response = client.put("/api/v1/general/language", json={"language": "fr"}, headers=auth_headers)
    assert response.status_code == 400
    assert "Unsupported system language" in response.json()["detail"]
    assert settings.SYSTEM_LANGUAGE == "zh"
