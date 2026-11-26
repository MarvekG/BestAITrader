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
