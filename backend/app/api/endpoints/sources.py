from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.ai.agentic.tooling.news_plugins import newsapi, tavily
from app.core.data_source_config_cache import (
    get_data_source_config as get_cached_data_source_config,
    invalidate_data_source_config_cache,
)
from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
from app.data.ingestors.manager import ingestor_manager
from app.core.data_source_settings import (
    NEWS_API_KEY_SETTING_KEY,
    TAVILY_API_KEY_SETTING_KEY,
    TUSHARE_API_SETTING_KEY,
    TUSHARE_TOKEN_SETTING_KEY,
)
from app.crud.system_setting import save_system_setting
from app.core.i18n import i18n_service

router = APIRouter()


@router.get("/", response_model=Dict[str, Any])
async def list_data_sources():
    """获取所有已注册的数据源及当前默认数据源"""
    try:
        sources = ingestor_manager.list_data_sources()
        source_details = ingestor_manager.list_data_source_details()
        default_source = ingestor_manager.default_source
        prioritized = ingestor_manager.get_prioritized_sources()
        return {
            "status": "success",
            "sources": sources,
            "source_details": source_details,
            "default_source": default_source,
            "priority_order": prioritized,
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{i18n_service.t('sources.get_list_failed')}: {str(e)}"
        )


@router.post("/default", response_model=Dict[str, Any])
async def set_default_data_source(source_name: str):
    """设置默认数据源"""
    if ingestor_manager.set_default_source(source_name):
        return {
            "status": "success",
            "message": i18n_service.t("sources.default_set_success").format(source_name=source_name),
            "default_source": ingestor_manager.default_source
        }
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=i18n_service.t("sources.not_found").format(source_name=source_name)
        )


class DataSourceConfigUpdate(BaseModel):
    tushare_token: Optional[str] = None
    tushare_api_url: Optional[str] = None
    tavily_api_key: Optional[list[str]] = None
    news_api_key: Optional[list[str]] = None


@router.get("/config", response_model=Dict[str, Any])
async def get_data_source_config():
    """获取数据源配置，敏感值仅返回脱敏结果。"""
    try:
        data_source_config = get_cached_data_source_config()
        tushare_api_url = data_source_config.get(TUSHARE_API_SETTING_KEY, "")
        tushare_token = data_source_config.get(TUSHARE_TOKEN_SETTING_KEY, "")
        return {
            "status": "success",
            "config": {
                "tushare_api_url": tushare_api_url,
                "tushare_token": _mask_secret(tushare_token),
                "tavily_api_key": _mask_secret_list(data_source_config.get(TAVILY_API_KEY_SETTING_KEY, [])),
                "news_api_key": _mask_secret_list(data_source_config.get(NEWS_API_KEY_SETTING_KEY, [])),
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{i18n_service.t('sources.get_config_failed')}: {str(e)}"
        )


@router.post("/config", response_model=Dict[str, Any])
async def update_data_source_config(config: DataSourceConfigUpdate):
    """保存数据源配置到 system_settings。"""
    try:
        data_source_config = get_cached_data_source_config()

        if config.tushare_token:
            save_system_setting(TUSHARE_TOKEN_SETTING_KEY, config.tushare_token, "Tushare API Token")

        if config.tushare_api_url:
            save_system_setting(TUSHARE_API_SETTING_KEY, config.tushare_api_url, "Tushare API URL")

        if config.tavily_api_key is not None:
            current_tavily_keys = data_source_config.get(TAVILY_API_KEY_SETTING_KEY, [])
            save_system_setting(
                TAVILY_API_KEY_SETTING_KEY,
                _normalize_secret_list_update(config.tavily_api_key, current_tavily_keys),
                "Tavily API Key",
            )

        if config.news_api_key is not None:
            current_news_keys = data_source_config.get(NEWS_API_KEY_SETTING_KEY, [])
            save_system_setting(
                NEWS_API_KEY_SETTING_KEY,
                _normalize_secret_list_update(config.news_api_key, current_news_keys),
                "NewsAPI API Key",
            )

        invalidate_data_source_config_cache()
        return {"status": "success", "message": i18n_service.t("sources.config_updated")}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"{i18n_service.t('sources.update_config_failed')}: {str(e)}"
        )


def _mask_secret(value: str) -> Optional[str]:
    """
    返回敏感配置的脱敏展示值。

    Args:
        value: 原始敏感配置。

    Returns:
        脱敏字符串；空值返回 None。
    """
    if not value:
        return None
    return f"...{value[-3:]}"


def _mask_secret_list(value: Any) -> list[str]:
    """
    返回敏感配置列表的脱敏展示值。

    Args:
        value: 原始敏感配置列表。

    Returns:
        脱敏字符串列表。
    """
    if not isinstance(value, list):
        return []
    return [masked for item in value if isinstance(item, str) and (masked := _mask_secret(item))]


def _normalize_secret_list_update(next_values: list[str], current_values: Any) -> list[str]:
    """
    规范化前端提交的密钥列表，并保留未修改的脱敏占位项。

    Args:
        next_values: 前端提交的密钥列表，可能包含脱敏占位值。
        current_values: 数据库当前保存的真实密钥列表。

    Returns:
        可直接保存到 system_settings 的真实密钥列表。
    """
    current_keys = (
        [item for item in current_values if isinstance(item, str)]
        if isinstance(current_values, list)
        else []
    )
    masked_to_current = {_mask_secret(item): item for item in current_keys}
    normalized: list[str] = []
    for value in next_values:
        stripped_value = value.strip()
        if not stripped_value:
            continue
        if stripped_value.startswith("..."):
            current_value = masked_to_current.get(stripped_value)
            if current_value:
                normalized.append(current_value)
            continue
        normalized.append(stripped_value)
    return normalized


def _attach_key_context(result: Dict[str, Any], index: int, api_key: str) -> Dict[str, Any]:
    """
    为单个 API Key 的透传测试结果添加上下文。

    Args:
        result: 外部服务透传响应。
        index: 当前 Key 在配置中的序号。
        api_key: 当前测试的 API Key。

    Returns:
        带脱敏 Key 信息的响应。
    """
    return {"key_index": index, "key": _mask_secret(api_key), **result}


def _build_plugin_test_result(results: list[dict[str, Any]]) -> Dict[str, Any]:
    """
    将新闻插件搜索结果转换为配置测试响应。

    Args:
        results: 新闻插件返回的标准化结果。

    Returns:
        配置测试响应体。
    """
    fatal_error = next((item for item in results if item.get("fatal") and item.get("error")), None)
    if fatal_error:
        return {"status": "error", "error": fatal_error["error"], "data": results}
    return {"status": "success", "data": results}


def _build_tushare_test_result(data: Any) -> Dict[str, Any]:
    """
    将 Tushare 查询结果转换为配置测试响应。

    Args:
        data: Tushare Pro 客户端返回的数据。

    Returns:
        配置测试响应体。
    """
    if hasattr(data, "to_dict"):
        return {"status": "success", "data": data.to_dict(orient="records")}
    return {"status": "success", "data": data}


@router.post("/config/test/tushare", response_model=Dict[str, Any])
async def test_tushare_config_key():
    """测试当前 Tushare 配置。"""
    try:
        data = TushareIngestor().pro.daily(
            ts_code="000001.SZ",
            start_date="20240102",
            end_date="20240110",
            fields="ts_code,trade_date,open,close",
        )
        return _build_tushare_test_result(data)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@router.post("/config/test/tavily", response_model=Dict[str, Any])
async def test_tavily_config_key():
    """测试当前 Tavily 配置。"""
    data_source_config = get_cached_data_source_config()
    raw_api_keys = data_source_config.get(TAVILY_API_KEY_SETTING_KEY, [])
    api_keys = raw_api_keys if isinstance(raw_api_keys, list) else []
    api_keys = api_keys or [""]
    return {
        "status": "completed",
        "results": [
            _attach_key_context(
                _build_plugin_test_result(await tavily.search_with_api_keys(api_key, "A股", limit=1)),
                index,
                api_key,
            )
            for index, api_key in enumerate(api_keys, start=1)
        ],
    }


@router.post("/config/test/newsapi", response_model=Dict[str, Any])
async def test_newsapi_config_key():
    """测试当前 NewsAPI 配置。"""
    data_source_config = get_cached_data_source_config()
    raw_api_keys = data_source_config.get(NEWS_API_KEY_SETTING_KEY, [])
    api_keys = raw_api_keys if isinstance(raw_api_keys, list) else []
    api_keys = api_keys or [""]
    results = []
    for index, api_key in enumerate(api_keys, start=1):
        results.append(
            _attach_key_context(
                _build_plugin_test_result(await newsapi.search_with_api_keys(api_key, "stock", limit=1)),
                index,
                api_key,
            )
        )
    return {"status": "completed", "results": results}
