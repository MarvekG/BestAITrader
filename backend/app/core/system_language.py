from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.system_setting import system_setting


SYSTEM_LANGUAGE_SETTING_KEY = "system:language"
SUPPORTED_SYSTEM_LANGUAGES = ("zh", "en")


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
        Normalized runtime system language, falling back to Chinese when settings are invalid.
    """
    try:
        return normalize_system_language(settings.SYSTEM_LANGUAGE)
    except ValueError:
        return "zh"


def get_persisted_system_language(db: Session) -> str:
    """
    Read the persisted system language.

    Args:
        db: Database session.

    Returns:
        Normalized persisted system language, falling back to the runtime setting.
    """
    stored_language = system_setting.get_value(db, SYSTEM_LANGUAGE_SETTING_KEY, settings.SYSTEM_LANGUAGE)
    try:
        return normalize_system_language(stored_language)
    except ValueError:
        return get_runtime_system_language()


def load_persisted_system_language(db: Session) -> str:
    """
    Load persisted system language into runtime settings.

    Args:
        db: Database session.

    Returns:
        Normalized language applied to runtime settings.
    """
    language = get_persisted_system_language(db)
    settings.SYSTEM_LANGUAGE = language
    return language


def set_system_language(db: Session, language: str) -> str:
    """
    Persist and apply the system language.

    Args:
        db: Database session.
        language: Requested language code.

    Returns:
        Normalized language applied to runtime settings.

    Raises:
        ValueError: If the language code is not supported.
    """
    normalized_language = normalize_system_language(language)
    system_setting.set_value(
        db,
        key=SYSTEM_LANGUAGE_SETTING_KEY,
        value=normalized_language,
        description="System language for UI, backend messages, field labels, and AI prompts.",
    )
    settings.SYSTEM_LANGUAGE = normalized_language
    return normalized_language
