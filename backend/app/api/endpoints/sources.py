import http.client
import json
import time
from typing import Dict, Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.data_source_config_cache import (
    get_data_source_config as get_cached_data_source_config,
    invalidate_data_source_config_cache,
)
from app.data.ingestors.manager import ingestor_manager
from app.core.data_source_settings import (
    NEWS_API_KEY_SETTING_KEY,
    TAVILY_API_KEY_SETTING_KEY,
    TUSHARE_API_SETTING_KEY,
    TUSHARE_TOKEN_SETTING_KEY,
)
from app.crud.system_setting import save_system_setting
from app.ai.agentic.tooling.news_plugins.provider_clients import split_api_keys
from app.core.i18n import i18n_service

router = APIRouter()
DATA_SOURCE_TEST_TIMEOUT_SECONDS = 30


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
    tavily_api_key: Optional[str] = None
    news_api_key: Optional[str] = None


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
                "tavily_api_key": _mask_secret(data_source_config.get(TAVILY_API_KEY_SETTING_KEY, "")),
                "news_api_key": _mask_secret(data_source_config.get(NEWS_API_KEY_SETTING_KEY, "")),
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
        if config.tushare_token:
            save_system_setting(TUSHARE_TOKEN_SETTING_KEY, config.tushare_token, "Tushare API Token")

        if config.tushare_api_url:
            save_system_setting(TUSHARE_API_SETTING_KEY, config.tushare_api_url, "Tushare API URL")

        if config.tavily_api_key:
            save_system_setting(TAVILY_API_KEY_SETTING_KEY, config.tavily_api_key, "Tavily API Key")

        if config.news_api_key:
            save_system_setting(NEWS_API_KEY_SETTING_KEY, config.news_api_key, "NewsAPI API Key")

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


def _read_json_response(request: Request) -> Dict[str, Any]:
    """
    发送 HTTP 请求并透传外部服务响应。

    Args:
        request: 已构造的 urllib 请求。

    Returns:
        包含 HTTP 状态码、耗时和响应正文的结果。
    """
    start_time = time.time()
    try:
        with urlopen(request, timeout=DATA_SOURCE_TEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
            return _build_external_response(response.status, body, int((time.time() - start_time) * 1000))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return _build_external_response(exc.code, body, int((time.time() - start_time) * 1000))
    except URLError as exc:
        return _build_transport_error(str(exc.reason), int((time.time() - start_time) * 1000))
    except http.client.HTTPException as exc:
        return _build_transport_error(str(exc), int((time.time() - start_time) * 1000))
    except OSError as exc:
        return _build_transport_error(str(exc), int((time.time() - start_time) * 1000))
    except TimeoutError:
        return _build_transport_error("request timeout", int((time.time() - start_time) * 1000))


def _build_external_response(http_status: int, body: str, elapsed_ms: int) -> Dict[str, Any]:
    """
    构造外部服务透传响应。

    Args:
        http_status: 外部服务 HTTP 状态码。
        body: 外部服务响应正文。
        elapsed_ms: 请求耗时。

    Returns:
        API 响应体。
    """
    payload: Dict[str, Any] = {
        "status": "success" if 200 <= http_status < 300 else "error",
        "http_status": http_status,
        "elapsed_ms": elapsed_ms,
        "raw_body": body[:4000],
    }
    try:
        payload["data"] = json.loads(body) if body else None
    except json.JSONDecodeError:
        payload["data"] = None
    return payload


def _build_transport_error(error: str, elapsed_ms: int) -> Dict[str, Any]:
    """
    构造请求未到达外部服务时的错误响应。

    Args:
        error: 传输层错误说明。
        elapsed_ms: 请求耗时。

    Returns:
        API 响应体。
    """
    return {"status": "error", "elapsed_ms": elapsed_ms, "error": error}


def _post_json_to_external(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    向外部服务发送 JSON POST 请求。

    Args:
        url: 请求地址。
        payload: JSON 请求体。

    Returns:
        外部服务透传响应。
    """
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return _read_json_response(request)


def _get_external(url: str) -> Dict[str, Any]:
    """
    向外部服务发送 GET 请求。

    Args:
        url: 请求地址。

    Returns:
        外部服务透传响应。
    """
    return _read_json_response(Request(url, method="GET"))


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


@router.post("/config/test/tushare", response_model=Dict[str, Any])
async def test_tushare_config_key():
    """测试当前 Tushare 配置并透传外部响应。"""
    data_source_config = get_cached_data_source_config()
    api_url = data_source_config.get(TUSHARE_API_SETTING_KEY, "") or "http://api.waditu.com/dataapi"
    token = data_source_config.get(TUSHARE_TOKEN_SETTING_KEY, "")
    return _post_json_to_external(
        f"{api_url.rstrip('/')}/daily",
        {
            "api_name": "daily",
            "token": token,
            "params": {"ts_code": "000001.SZ", "start_date": "20240102", "end_date": "20240110"},
            "fields": "ts_code,trade_date,open,close",
        },
    )


@router.post("/config/test/tavily", response_model=Dict[str, Any])
async def test_tavily_config_key():
    """测试当前 Tavily 配置并透传外部响应。"""
    data_source_config = get_cached_data_source_config()
    api_keys = split_api_keys(data_source_config.get(TAVILY_API_KEY_SETTING_KEY, "")) or [""]
    return {
        "status": "completed",
        "results": [
            _attach_key_context(
                _post_json_to_external(
                    "https://api.tavily.com/search",
                    {"api_key": api_key, "query": "A股", "max_results": 1},
                ),
                index,
                api_key,
            )
            for index, api_key in enumerate(api_keys, start=1)
        ],
    }


@router.post("/config/test/newsapi", response_model=Dict[str, Any])
async def test_newsapi_config_key():
    """测试当前 NewsAPI 配置并透传外部响应。"""
    from urllib.parse import urlencode

    data_source_config = get_cached_data_source_config()
    api_keys = split_api_keys(data_source_config.get(NEWS_API_KEY_SETTING_KEY, "")) or [""]
    results = []
    for index, api_key in enumerate(api_keys, start=1):
        query = urlencode({"q": "stock", "pageSize": 1, "apiKey": api_key})
        results.append(
            _attach_key_context(
                _get_external(f"https://newsapi.org/v2/everything?{query}"),
                index,
                api_key,
            )
        )
    return {"status": "completed", "results": results}
