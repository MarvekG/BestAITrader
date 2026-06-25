import httpx
import pytest

from app.ai.agentic.tooling.news_plugins import provider_clients


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, request=httpx.Request("GET", "https://example.com"))


@pytest.mark.asyncio
async def test_request_with_key_failover_prefers_last_successful_key():
    provider_clients._SERVICE_HEALTHY_KEYS.clear()
    used_keys = []

    async def request_once(api_key: str) -> httpx.Response:
        used_keys.append(api_key)
        return _response(200 if api_key == "good-key" else 401)

    await provider_clients.request_with_key_failover("test-service", "bad-key,good-key", request_once)
    assert used_keys == ["bad-key", "good-key"]

    used_keys.clear()
    await provider_clients.request_with_key_failover("test-service", "bad-key,good-key", request_once)
    assert used_keys == ["good-key"]


@pytest.mark.asyncio
async def test_request_with_key_failover_clears_unavailable_state_and_recovers_in_config_order():
    provider_clients._SERVICE_HEALTHY_KEYS.clear()
    used_keys = []
    status_codes = {"first-key": 503, "second-key": 200}

    async def request_once(api_key: str) -> httpx.Response:
        used_keys.append(api_key)
        return _response(status_codes[api_key])

    await provider_clients.request_with_key_failover("test-service", "first-key,second-key", request_once)
    assert used_keys == ["first-key", "second-key"]

    used_keys.clear()
    status_codes = {"first-key": 503, "second-key": 503}
    response = await provider_clients.request_with_key_failover("test-service", "first-key,second-key", request_once)
    assert response is None
    assert used_keys == ["second-key", "first-key"]

    used_keys.clear()
    status_codes = {"first-key": 200, "second-key": 503}
    await provider_clients.request_with_key_failover("test-service", "first-key,second-key", request_once)
    assert used_keys == ["first-key"]
