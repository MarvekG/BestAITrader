from app.core.config import settings
from app.crud.system_setting import system_setting


SYSTEM_LANGUAGE_SETTING_KEY = "system:language"
SUPPORTED_SYSTEM_LANGUAGES = ("zh", "en")
DEFAULT_SYSTEM_LANGUAGE = "zh"


def normalize_system_language(language: str | None) -> str:
    """
    Normalize and validate a system language code.

    Args:
        language: Raw language code from settings, database, or user input.

    Returns:
        Normalized language code.

    Raises:
        ValueError: If the language code is not supported.
    """
    raw_language = "" if language is None else str(language)
    normalized = raw_language.strip().lower().replace("_", "-")
    if normalized.startswith("zh"):
        return "zh"
    if normalized.startswith("en"):
        return "en"

    supported = ", ".join(SUPPORTED_SYSTEM_LANGUAGES)
    raise ValueError(f"Unsupported system language: {language}. Supported languages: {supported}")


def get_runtime_system_language() -> str:
    """
    Get the current runtime system language.

    Returns:
        Normalized runtime system language, falling back to the default language when settings are invalid.
    """
    try:
        return normalize_system_language(settings.SYSTEM_LANGUAGE)
    except ValueError:
        return DEFAULT_SYSTEM_LANGUAGE


async def get_persisted_system_language() -> str:
    """
    Read the persisted system language.

    Args:
    Returns:
        Normalized persisted system language, falling back to the runtime setting.
    """
    stored_language = await system_setting.get_value(
        SYSTEM_LANGUAGE_SETTING_KEY,
        settings.SYSTEM_LANGUAGE,
    )
    try:
        return normalize_system_language(stored_language)
    except ValueError:
        return get_runtime_system_language()


async def load_persisted_system_language() -> str:
    """
    Load persisted system language into runtime settings.

    Args:
    Returns:
        Normalized language applied to runtime settings.
    """
    language = await get_persisted_system_language()
    settings.SYSTEM_LANGUAGE = language
    return language


async def set_system_language(language: str) -> str:
    """
    Persist and apply the system language.

    Args:
        language: Requested language code.

    Returns:
        Normalized language applied to runtime settings.

    Raises:
        ValueError: If the language code is not supported.
    """
    normalized_language = normalize_system_language(language)
    await system_setting.set_value(
        SYSTEM_LANGUAGE_SETTING_KEY,
        normalized_language,
        description="System language for UI, backend messages, field labels, and AI prompts.",
    )
    settings.SYSTEM_LANGUAGE = normalized_language
    return normalized_language
