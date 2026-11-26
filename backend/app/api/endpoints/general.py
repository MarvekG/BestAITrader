from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.i18n import i18n_service
from app.core.proxy import (
    PROXY_TEST_DEFAULT_TARGET_URL,
    ProxySettings,
    ProxyTestResult,
    get_proxy_settings,
    normalize_proxy_test_target_url,
    test_proxy_connection,
    update_proxy_settings,
)
from app.core.runtime_settings import RuntimeSettings, get_runtime_settings, update_runtime_settings
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


class RuntimeSettingsUpdate(RuntimeSettings):
    """Runtime settings update payload."""


class ProxySettingsTestRequest(BaseModel):
    """代理连通性测试请求。"""

    url: str = Field(default="", max_length=2048)
    target_url: str = Field(default=PROXY_TEST_DEFAULT_TARGET_URL, max_length=2048)


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
    current_user: User = Depends(get_current_user),
) -> SystemLanguageResponse:
    """
    Get the current system language.

    Args:
        current_user: Authenticated user.

    Returns:
        Current language and supported language list.
    """
    del current_user
    return SystemLanguageResponse(
        language=await get_persisted_system_language(),
        supported_languages=list(SUPPORTED_SYSTEM_LANGUAGES),
    )


@router.put("/language", response_model=SystemLanguageResponse)
async def update_system_language(
    payload: SystemLanguageUpdate,
    current_user: User = Depends(get_current_user),
) -> SystemLanguageResponse:
    """
    Update the system language.

    Args:
        payload: Language update payload.
        current_user: Authenticated user.

    Returns:
        Updated language and supported language list.
    """
    del current_user
    try:
        language = await set_system_language(payload.language)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return SystemLanguageResponse(
        language=language,
        supported_languages=list(SUPPORTED_SYSTEM_LANGUAGES),
    )


@router.get("/proxy-settings", response_model=ProxySettings)
async def read_proxy_settings(
    current_user: User = Depends(get_current_user),
) -> ProxySettings:
    """Get the global outbound proxy settings.

    Args:
        current_user: Authenticated user.

    Returns:
        Current global proxy settings.
    """
    del current_user
    return await get_proxy_settings(refresh=True)


@router.put("/proxy-settings", response_model=ProxySettings)
async def save_proxy_settings(
    payload: ProxySettings,
    current_user: User = Depends(get_current_user),
) -> ProxySettings:
    """Update the global outbound proxy settings.

    Args:
        payload: Proxy settings update payload.
        current_user: Authenticated user.

    Returns:
        Updated global proxy settings.
    """
    del current_user
    return await update_proxy_settings(payload)


@router.post("/proxy-settings/test", response_model=ProxyTestResult)
async def test_proxy_settings(
    payload: ProxySettingsTestRequest,
    current_user: User = Depends(get_current_user),
) -> ProxyTestResult:
    """测试通过指定代理访问外部网址的连通性。

    Args:
        payload: 待测试的代理地址和目标网址。
        current_user: 当前登录用户。

    Returns:
        代理连通性测试结果。

    Raises:
        HTTPException: 代理地址或目标网址不合法。
    """
    del current_user
    try:
        proxy_url = ProxySettings(url=payload.url).url
        if not proxy_url:
            raise ValueError("proxy url is required")
        target_url = normalize_proxy_test_target_url(payload.target_url)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    return await test_proxy_connection(proxy_url, target_url)


@router.get("/runtime-settings", response_model=RuntimeSettings)
async def read_runtime_settings(
    current_user: User = Depends(get_current_user),
) -> RuntimeSettings:
    """Get system runtime settings.

    Args:
        current_user: Authenticated user.

    Returns:
        Current system runtime settings.
    """
    del current_user
    return await get_runtime_settings()


@router.put("/runtime-settings", response_model=RuntimeSettings)
async def save_runtime_settings(
    payload: RuntimeSettingsUpdate,
    current_user: User = Depends(get_current_user),
) -> RuntimeSettings:
    """Update system runtime settings.

    Args:
        payload: Runtime settings update payload.
        current_user: Authenticated user.

    Returns:
        Updated system runtime settings.
    """
    del current_user
    settings = await update_runtime_settings(payload)
    from app.ai.llm_engine.debate_concurrency import dispatch_pending_debate_tasks_safely

    await dispatch_pending_debate_tasks_safely()
    return settings
