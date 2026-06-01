from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.i18n import i18n_service
from app.core.security import get_current_user
from app.core.system_language import (
    SUPPORTED_SYSTEM_LANGUAGES,
    get_persisted_system_language,
    set_system_language,
)
from app.models.user import User

router = APIRouter()


class SystemLanguageUpdate(BaseModel):
    """System language update payload."""

    language: str


class SystemLanguageResponse(BaseModel):
    """System language response payload."""

    language: str
    supported_languages: list[str]


@router.get("/i18n/{lang}", response_model=Dict[str, Any])
async def get_translations(lang: str) -> Dict[str, Any]:
    """
    Get all translations for a specific language.
    Used by frontend to load i18n resources.
    """
    translations = i18n_service.get_locale(lang)
    if not translations:
        # If requested language is not found, try 'en' or 'zh' as fallback,
        # or return empty dict to let frontend handle it.
        # Here we return empty dict if strictly not found to distinguish
        # (though get_locale defaults to zh usually, let's keep it safe)
        if lang == 'dev':  # i18next sometimes requests 'dev'
            return i18n_service.get_locale('en')
        return i18n_service.get_locale('zh')

    return translations


@router.get("/language", response_model=SystemLanguageResponse)
async def get_system_language(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SystemLanguageResponse:
    """
    Get the current system language.

    Args:
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Current language and supported language list.
    """
    del current_user
    return SystemLanguageResponse(
        language=get_persisted_system_language(db),
        supported_languages=list(SUPPORTED_SYSTEM_LANGUAGES),
    )


@router.put("/language", response_model=SystemLanguageResponse)
async def update_system_language(
    payload: SystemLanguageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SystemLanguageResponse:
    """
    Update the system language.

    Args:
        payload: Language update payload.
        db: Database session.
        current_user: Authenticated user.

    Returns:
        Updated language and supported language list.
    """
    del current_user
    try:
        language = set_system_language(db, payload.language)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return SystemLanguageResponse(
        language=language,
        supported_languages=list(SUPPORTED_SYSTEM_LANGUAGES),
    )
