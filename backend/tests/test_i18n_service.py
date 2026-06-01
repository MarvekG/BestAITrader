from unittest.mock import patch

from app.core.i18n import I18nService


def test_t_uses_system_language_without_lang_argument():
    service = I18nService()

    with patch("app.core.config.settings.SYSTEM_LANGUAGE", "zh"):
        assert service.t("common.success") == "操作成功"

    with patch("app.core.config.settings.SYSTEM_LANGUAGE", "en"):
        assert service.t("common.success") == "Success"


def test_t_supports_double_brace_parameter_instantiation():
    service = I18nService()

    with patch("app.core.config.settings.SYSTEM_LANGUAGE", "zh"):
        assert service.t("experience.clear_runs_success", count=3) == "已清空 3 条经验分析任务。"


def test_t_supports_single_brace_parameter_instantiation():
    service = I18nService()

    with patch("app.core.config.settings.SYSTEM_LANGUAGE", "en"):
        assert service.t("testing.redis_failed", error="boom") == "Redis connection failed: boom"


def test_t_falls_back_to_key_when_missing():
    service = I18nService()

    with patch("app.core.config.settings.SYSTEM_LANGUAGE", "zh"):
        assert service.t("non.existent.key") == "non.existent.key"
