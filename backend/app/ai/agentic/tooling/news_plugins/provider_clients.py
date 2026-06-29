"""新闻插件 API Key 故障倒换工具。"""

from collections.abc import Awaitable, Callable

import httpx

from app.core.logger import get_logger

logger = get_logger(__name__)
_SERVICE_HEALTHY_KEYS: dict[str, str] = {}


class ProviderRequestError(RuntimeError):
    """外部新闻服务全部 API Key 请求失败。"""

    def __init__(self, service_name: str, status_code: int, response_text: str = "") -> None:
        """
        保存外部服务不可用时的关键诊断信息。

        Args:
            service_name: 外部服务名称。
            status_code: 最后一次失败请求的 HTTP 状态码。
            response_text: 最后一次失败响应正文。
        """
        self.service_name = service_name
        self.status_code = status_code
        self.response_text = response_text[:500]
        message = f"{service_name} request failed with HTTP {status_code}"
        if self.response_text:
            message = f"{message}: {self.response_text}"
        super().__init__(message)


def split_api_keys(raw_api_keys: str) -> list[str]:
    """
    将逗号分隔的配置拆分为可用 API Key 列表。

    Args:
        raw_api_keys: 配置项原始值，支持英文逗号分隔多个 Key。

    Returns:
        去除空白和空项后的 API Key 列表。
    """
    return [api_key.strip() for api_key in raw_api_keys.split(",") if api_key.strip()]


async def request_with_key_failover(
    service_name: str,
    raw_api_keys: str,
    request_once: Callable[[str], Awaitable[httpx.Response]],
) -> httpx.Response | None:
    """
    优先尝试上次可用 Key，并返回首个 HTTP 200 响应。

    Args:
        service_name: 日志中展示的外部服务名称。
        raw_api_keys: 配置项原始值，支持英文逗号分隔多个 Key。
        request_once: 使用单个 API Key 发起请求的异步回调。

    Returns:
        首个 HTTP 200 响应；没有 Key 时返回 None。

    Raises:
        ProviderRequestError: 所有已配置 API Key 均返回非 200 状态。
    """
    api_keys = split_api_keys(raw_api_keys)
    healthy_key = _SERVICE_HEALTHY_KEYS.get(service_name)
    if healthy_key in api_keys:
        ordered_keys = [healthy_key, *[api_key for api_key in api_keys if api_key != healthy_key]]
    else:
        ordered_keys = api_keys

    last_response: httpx.Response | None = None
    for index, api_key in enumerate(ordered_keys):
        response = await request_once(api_key)
        last_response = response
        if response.status_code == httpx.codes.OK:
            _SERVICE_HEALTHY_KEYS[service_name] = api_key
            return response
        if index == len(ordered_keys) - 1:
            logger.warning(
                "%s request returned non-200 status. No API key is currently available.",
                service_name,
                extra={"status_code": response.status_code},
            )
            break
        logger.info(
            "%s request returned non-200 status. Trying next API key.",
            service_name,
            extra={"status_code": response.status_code},
        )
    _SERVICE_HEALTHY_KEYS.pop(service_name, None)
    if last_response is not None:
        raise ProviderRequestError(service_name, last_response.status_code, getattr(last_response, "text", ""))
    return None
