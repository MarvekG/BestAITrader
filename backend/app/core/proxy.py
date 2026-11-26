"""全局外网代理配置与插件公共 API。

系统设置中的代理配置保存为全局 system_settings 记录。所有需要访问外部网络的
业务客户端应通过本模块读取代理地址并显式传给 HTTP 客户端，不依赖进程环境变量。
设置保存后会立即刷新进程内缓存，后续请求按最新代理地址生效。
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, field_validator

from app.core.logger import get_logger
from app.crud.system_setting import system_setting

__all__ = ["get_proxy_url_for_url", "test_proxy_connection"]

logger = get_logger(__name__)

PROXY_SETTING_KEY = "network.proxy"
PROXY_SETTING_DESCRIPTION = "Global outbound network proxy"
PROXY_TEST_DEFAULT_TARGET_URL = "https://www.deepseek.com"
PROXY_TEST_TIMEOUT_SECONDS = 15.0
PROXY_TEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

SUPPORTED_PROXY_SCHEMES = {"http", "socks5h"}
PLAYWRIGHT_PROXY_SCHEMES = {
    "http": "http",
    "socks5h": "socks5",
}
INTERNAL_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "host.docker.internal",
    "gateway.docker.internal",
    "postgres",
    "redis",
    "litellm",
    "sandbox",
    "webfetch",
    "scrapling.mcp",
}


class ProxySettings(BaseModel):
    """全局外网代理配置。"""

    url: str = Field(default="", max_length=2048)

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url(cls, value: Any) -> str:
        """校验并规范化代理 URL。

        Args:
            value: 原始代理 URL；空字符串表示不使用代理。

        Returns:
            规范化后的代理 URL。

        Raises:
            ValueError: 协议、主机、端口或认证信息不符合要求。
        """
        raw_url = str(value or "").strip()
        if not raw_url:
            return ""

        parsed = urlsplit(raw_url)
        scheme = parsed.scheme.lower()
        if scheme not in SUPPORTED_PROXY_SCHEMES:
            raise ValueError("proxy url scheme must be http or socks5h")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("proxy authentication is not supported")
        if not parsed.hostname:
            raise ValueError("proxy url must include a hostname")

        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("proxy url port is invalid") from exc
        if port is None:
            raise ValueError("proxy url must include a port")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("proxy url must not include path, query, or fragment")

        hostname = parsed.hostname
        if ":" in hostname:
            hostname = f"[{hostname}]"
        return f"{scheme}://{hostname}:{port}"

    @property
    def enabled(self) -> bool:
        """返回是否已配置代理。

        Returns:
            代理 URL 非空时返回 True。
        """
        return bool(self.url)


class ProxyTestResult(BaseModel):
    """代理连通性测试结果。"""

    status: str
    target_url: str
    proxy_url: str | None = None
    http_status: int | None = None
    elapsed_ms: int = 0
    final_url: str | None = None
    error: str | None = None


def normalize_proxy_test_target_url(value: str | None) -> str:
    """校验并补全代理测试使用的目标网址。

    Args:
        value: 用户输入的测试网址；未带协议时默认补全为 https。

    Returns:
        规范化后的目标网址。

    Raises:
        ValueError: 网址为空、协议不支持或缺少主机名。
    """
    raw_url = str(value or "").strip()
    if not raw_url:
        raise ValueError("test url is required")
    if "://" not in raw_url:
        raw_url = f"https://{raw_url}"

    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("test url scheme must be http or https")
    if not parsed.hostname:
        raise ValueError("test url must include a hostname")
    return raw_url


async def test_proxy_connection(
    proxy_url: str | None,
    target_url: str | None = None,
) -> ProxyTestResult:
    """通过指定代理访问目标网址，验证代理连通性。

    Args:
        proxy_url: 待测试的代理 URL；为空时测试直连。
        target_url: 测试目标网址；为空时使用默认测试网址。

    Returns:
        包含状态码、耗时和错误信息的测试结果。

    Raises:
        ValueError: 代理 URL 或目标网址不合法。
    """
    normalized_proxy = ProxySettings(url=proxy_url or "").url or None
    normalized_target = normalize_proxy_test_target_url(
        target_url or PROXY_TEST_DEFAULT_TARGET_URL
    )

    started_at = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - started_at) * 1000)

    try:
        async with (
            httpx.AsyncClient(
                proxy=normalized_proxy,
                trust_env=False,
                timeout=PROXY_TEST_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as client,
            client.stream(
                "GET",
                normalized_target,
                headers=PROXY_TEST_HEADERS,
            ) as response,
        ):
            http_status = response.status_code
            final_url = str(response.url)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return ProxyTestResult(
            status="error",
            target_url=normalized_target,
            proxy_url=normalized_proxy,
            elapsed_ms=elapsed_ms(),
            error=f"{type(exc).__name__}: {exc}"[:500],
        )

    if http_status >= 400:
        return ProxyTestResult(
            status="error",
            target_url=normalized_target,
            proxy_url=normalized_proxy,
            http_status=http_status,
            elapsed_ms=elapsed_ms(),
            final_url=final_url,
            error=f"HTTP {http_status}",
        )

    return ProxyTestResult(
        status="success",
        target_url=normalized_target,
        proxy_url=normalized_proxy,
        http_status=http_status,
        elapsed_ms=elapsed_ms(),
        final_url=final_url,
    )


_proxy_settings_cache: ProxySettings | None = None
_proxy_settings_lock = asyncio.Lock()


def _coerce_proxy_settings(raw_value: Any) -> ProxySettings:
    """将 system_settings 中的原始值转换为代理配置。

    Args:
        raw_value: system_settings 中保存的原始值。

    Returns:
        代理配置对象；缺失时返回空配置。

    Raises:
        ValueError: 配置结构或字段校验失败。
    """
    if raw_value is None:
        return ProxySettings()
    if isinstance(raw_value, str):
        return ProxySettings(url=raw_value)
    if not isinstance(raw_value, dict):
        raise ValueError("proxy settings must be a JSON object or string")
    return ProxySettings.model_validate(raw_value)


async def get_proxy_settings(*, refresh: bool = False) -> ProxySettings:
    """读取全局代理配置并维护进程内缓存。

    Args:
        refresh: 为 True 时强制从系统设置重新读取。

    Returns:
        当前全局代理配置。
    """
    global _proxy_settings_cache

    if _proxy_settings_cache is not None and not refresh:
        return _proxy_settings_cache

    async with _proxy_settings_lock:
        if _proxy_settings_cache is not None and not refresh:
            return _proxy_settings_cache
        raw_value = await system_setting.get_value(PROXY_SETTING_KEY, default=None, user_id=None)
        _proxy_settings_cache = _coerce_proxy_settings(raw_value)
        return _proxy_settings_cache


async def initialize_proxy_settings() -> ProxySettings | None:
    """在应用启动时加载代理配置。

    Returns:
        加载成功时返回代理配置；读取或校验失败时返回 None。
    """
    try:
        settings = await get_proxy_settings(refresh=True)
    except Exception as exc:
        logger.exception("Failed to load global proxy settings: %s", exc)
        return None

    logger.info(
        "Global proxy settings loaded",
        extra={"configured": settings.enabled},
    )
    return settings


async def update_proxy_settings(payload: ProxySettings) -> ProxySettings:
    """保存全局代理配置并立即刷新进程内缓存。

    Args:
        payload: 已通过校验的代理配置。

    Returns:
        保存后的代理配置。
    """
    global _proxy_settings_cache

    await system_setting.set_value(
        PROXY_SETTING_KEY,
        payload.model_dump(mode="json"),
        description=PROXY_SETTING_DESCRIPTION,
        user_id=None,
    )
    _proxy_settings_cache = payload
    logger.info(
        "Global proxy settings updated",
        extra={"configured": payload.enabled},
    )
    return payload


def _get_proxy_url() -> str | None:
    """获取当前缓存的代理地址。

    Returns:
        代理 URL；未配置或启动时尚未加载配置时返回 None。
    """
    if _proxy_settings_cache is None:
        return None
    return _proxy_settings_cache.url or None


def build_playwright_proxy(proxy_url: str | None) -> dict[str, str] | None:
    """将代理 URL 转换为 Playwright 代理配置。

    Args:
        proxy_url: 代理 URL；为空时不使用代理。

    Returns:
        Playwright 代理配置；未配置代理时返回 None。

    Raises:
        ValueError: 代理 URL 协议、主机或端口非法。
    """
    if not proxy_url:
        return None

    normalized_url = ProxySettings(url=proxy_url).url
    parsed = urlsplit(normalized_url)
    scheme = PLAYWRIGHT_PROXY_SCHEMES[parsed.scheme]
    hostname = parsed.hostname or ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    return {"server": f"{scheme}://{hostname}:{parsed.port}"}


def get_proxy_url_for_url(url: str | None = None) -> str | None:
    """获取访问目标 URL 时应使用的代理地址。

    这是提供给运行时插件的公共代理查询 API。传入内部服务或本机 URL 时返回
    None，避免数据库、Redis、LiteLLM 等内部流量误走外部代理。

    Args:
        url: 目标 URL；为空时直接返回全局代理地址。

    Returns:
        已配置且目标需要代理时返回代理 URL，否则返回 None。
    """
    proxy_url = _get_proxy_url()
    if not proxy_url:
        return None
    if url is None or not _is_internal_url(url):
        return proxy_url
    return None


def _is_internal_url(url: str) -> bool:
    """判断 URL 是否指向系统内部服务或本机地址。

    Args:
        url: 待判断的 HTTP URL。

    Returns:
        是内部地址时返回 True。
    """
    parsed = urlsplit(str(url or "").strip())
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname in INTERNAL_HOSTNAMES:
        return True
    if hostname.endswith((".localhost", ".local", ".internal")):
        return True
    if "." not in hostname:
        return True
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False
