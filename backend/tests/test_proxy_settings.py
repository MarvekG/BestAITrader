from typing import ClassVar

import httpx
import pytest

from app.core import proxy
from app.core.proxy import ProxySettings, build_playwright_proxy, get_proxy_url_for_url


@pytest.fixture(autouse=True)
def reset_proxy_cache():
    """每个测试前后清空代理配置缓存。"""
    proxy._proxy_settings_cache = None
    yield
    proxy._proxy_settings_cache = None


@pytest.mark.parametrize(
    ("raw_url", "normalized_url"),
    [
        ("http://127.0.0.1:7890", "http://127.0.0.1:7890"),
        ("HTTP://Proxy.Example.com:8080/", "http://proxy.example.com:8080"),
        ("socks5h://[::1]:1080", "socks5h://[::1]:1080"),
    ],
)
def test_proxy_url_is_normalized(raw_url, normalized_url):
    """代理 URL 保存前统一协议、主机和尾斜杠格式。"""
    assert ProxySettings(url=raw_url).url == normalized_url


@pytest.mark.parametrize(
    "raw_url",
    [
        "socks5://127.0.0.1:1080",
        "http://user:password@127.0.0.1:7890",
        "http://127.0.0.1",
        "http://127.0.0.1:7890/path",
        "http://127.0.0.1:7890?query=1",
    ],
)
def test_proxy_url_rejects_unsupported_values(raw_url):
    """代理配置只接受无认证的 http 和 socks5h URL。"""
    with pytest.raises(ValueError):
        ProxySettings(url=raw_url)


def test_build_playwright_proxy_maps_supported_schemes():
    """代理 URL 转换为浏览器引擎使用的代理配置。"""
    assert build_playwright_proxy("http://127.0.0.1:7890") == {
        "server": "http://127.0.0.1:7890",
    }
    assert build_playwright_proxy("socks5h://127.0.0.1:1080") == {
        "server": "socks5://127.0.0.1:1080",
    }
    assert build_playwright_proxy(None) is None


def test_get_proxy_url_for_url_skips_internal_hosts():
    """公共代理 API 对内部服务返回 None，对公网地址返回代理。"""
    proxy._proxy_settings_cache = ProxySettings(url="http://127.0.0.1:7890")

    assert get_proxy_url_for_url() == "http://127.0.0.1:7890"
    assert get_proxy_url_for_url("https://example.com/news") == "http://127.0.0.1:7890"
    assert get_proxy_url_for_url("http://litellm:4000/v1") is None
    assert get_proxy_url_for_url("http://127.0.0.1:8000/health") is None


@pytest.mark.asyncio
async def test_proxy_settings_update_takes_effect_without_restart(test_db):
    """保存代理配置后立即刷新公共 API 返回值。"""
    await proxy.update_proxy_settings(ProxySettings(url="http://127.0.0.1:7890"))
    assert get_proxy_url_for_url("https://example.com") == "http://127.0.0.1:7890"

    await proxy.update_proxy_settings(ProxySettings(url="socks5h://127.0.0.1:1080"))
    assert get_proxy_url_for_url("https://example.com") == "socks5h://127.0.0.1:1080"

    await proxy.update_proxy_settings(ProxySettings(url=""))
    assert get_proxy_url_for_url("https://example.com") is None


def test_proxy_settings_api_round_trip(client, auth_headers):
    """代理配置 API 保存后立即返回最新配置。"""
    response = client.put(
        "/api/v1/general/proxy-settings",
        headers=auth_headers,
        json={"url": "http://127.0.0.1:7890"},
    )

    assert response.status_code == 200
    assert response.json() == {"url": "http://127.0.0.1:7890"}

    read_response = client.get("/api/v1/general/proxy-settings", headers=auth_headers)
    assert read_response.status_code == 200
    assert read_response.json() == {"url": "http://127.0.0.1:7890"}

    client.put(
        "/api/v1/general/proxy-settings",
        headers=auth_headers,
        json={"url": ""},
    )


class _FakeStreamResponse:
    def __init__(self, status_code: int, url: str):
        self.status_code = status_code
        self.url = httpx.URL(url)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeAsyncClient:
    instances: ClassVar[list["_FakeAsyncClient"]] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.requested_url: str | None = None
        self.requested_headers: dict[str, str] | None = None
        type(self).instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url, headers=None):
        self.requested_url = url
        self.requested_headers = headers
        return _FakeStreamResponse(200, url)


@pytest.mark.asyncio
async def test_test_proxy_connection_uses_proxy_and_normalizes_target(monkeypatch):
    """测试代理时使用传入的代理地址，并为裸域名补全 https。"""
    _FakeAsyncClient.instances.clear()
    monkeypatch.setattr(proxy.httpx, "AsyncClient", _FakeAsyncClient)

    result = await proxy.test_proxy_connection("http://127.0.0.1:7890", "deepseek.com")

    assert result.status == "success"
    assert result.proxy_url == "http://127.0.0.1:7890"
    assert result.target_url == "https://deepseek.com"
    assert result.http_status == 200
    assert result.final_url == "https://deepseek.com"
    fake_client = _FakeAsyncClient.instances[0]
    assert fake_client.kwargs["proxy"] == "http://127.0.0.1:7890"
    assert fake_client.kwargs["trust_env"] is False
    assert fake_client.requested_url == "https://deepseek.com"


@pytest.mark.asyncio
async def test_test_proxy_connection_reports_network_error(monkeypatch):
    """代理连接失败时返回错误状态而不是抛出异常。"""
    class _FailingAsyncClient(_FakeAsyncClient):
        def stream(self, method, url, headers=None):
            raise httpx.ConnectError(
                "connection refused",
                request=httpx.Request(method, url),
            )

    monkeypatch.setattr(proxy.httpx, "AsyncClient", _FailingAsyncClient)

    result = await proxy.test_proxy_connection("http://127.0.0.1:7890", "https://example.com")

    assert result.status == "error"
    assert result.http_status is None
    assert "ConnectError" in (result.error or "")


def test_proxy_settings_test_api_returns_result(client, auth_headers, monkeypatch):
    """代理测试 API 返回代理地址、目标地址和耗时。"""
    _FakeAsyncClient.instances.clear()
    monkeypatch.setattr(proxy.httpx, "AsyncClient", _FakeAsyncClient)

    response = client.post(
        "/api/v1/general/proxy-settings/test",
        headers=auth_headers,
        json={"url": "http://127.0.0.1:7890", "target_url": "deepseek.com"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["proxy_url"] == "http://127.0.0.1:7890"
    assert payload["target_url"] == "https://deepseek.com"
    assert payload["http_status"] == 200
    assert payload["elapsed_ms"] >= 0


@pytest.mark.parametrize(
    "payload",
    [
        {"url": "http://127.0.0.1", "target_url": "https://example.com"},
        {"url": "", "target_url": "ftp://example.com"},
    ],
)
def test_proxy_settings_test_api_rejects_invalid_input(client, auth_headers, payload):
    """代理测试 API 校验代理地址和目标网址。"""
    response = client.post(
        "/api/v1/general/proxy-settings/test",
        headers=auth_headers,
        json=payload,
    )

    assert response.status_code == 400
