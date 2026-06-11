from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse

from app.ai.agentic.mcp.models import MCPServerConfig, MCPServerCreateRequest, MCPServerUpdateRequest
from app.crud.system_setting import read_system_setting, save_system_setting


MCP_SETTINGS_KEY = "mcp.servers"
MCP_SETTINGS_DESCRIPTION = "Global MCP server runtime configuration"
DEFAULT_MCP_SERVERS = [
    {
        "name": "网页抓取",
        "enabled": False,
        "url": "http://scrapling-mcp:8765/mcp",
        "token": "",
        "allowed_tools": [],
    }
]

SENSITIVE_CONFIG_FIELDS = {"token"}


def _default_payload() -> Dict[str, Any]:
    """返回默认 MCP 系统配置。

    Returns:
        默认系统配置字典。
    """
    return {"servers": DEFAULT_MCP_SERVERS}


def _read_raw_payload() -> Dict[str, Any]:
    """读取 MCP 系统配置。

    Returns:
        系统配置中的原始字典，配置不存在时返回默认 server 列表。

    Raises:
        ValueError: 配置不是合法 JSON 对象时抛出。
    """
    payload = read_system_setting(MCP_SETTINGS_KEY, default=_default_payload(), user_id=None)
    if not isinstance(payload, dict):
        raise ValueError("MCP server system config must be a JSON object")
    servers = payload.get("servers")
    if servers is None:
        payload["servers"] = []
    elif not isinstance(servers, list):
        raise ValueError("MCP server system config field `servers` must be a list")
    return payload


def _write_configs(configs: List[MCPServerConfig]) -> None:
    """将 MCP Server 配置写入系统配置。

    Args:
        configs: MCP Server 配置列表。
    """
    payload = {
        "servers": [config.model_dump(mode="json") for config in sorted(configs, key=lambda item: item.name)],
    }
    save_system_setting(MCP_SETTINGS_KEY, payload, description=MCP_SETTINGS_DESCRIPTION, user_id=None)


def _validate_server_name(name: str) -> str:
    """校验并返回 MCP Server 名称。

    Args:
        name: 原始 MCP Server 名称。

    Returns:
        规整后的 MCP Server 名称。

    Raises:
        ValueError: 名称为空或过长时抛出。
    """
    normalized = str(name or "").strip()
    if not normalized or len(normalized) > 64:
        raise ValueError(f"Invalid MCP server name: {normalized}")
    return normalized


def validate_mcp_url(url: str) -> str:
    """校验 MCP HTTP URL。

    Args:
        url: MCP Server HTTP URL。

    Returns:
        规整后的 URL。

    Raises:
        ValueError: URL 为空或 scheme 非 http/https 时抛出。
    """
    normalized = str(url or "").strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("MCP url must be an absolute http:// or https:// URL")
    return normalized


def _validate_config(config: MCPServerConfig) -> MCPServerConfig:
    """执行 MCP Server 配置的跨字段校验。

    Args:
        config: 待校验配置。

    Returns:
        校验后的配置。

    Raises:
        ValueError: 配置不满足安全约束时抛出。
    """
    config.url = validate_mcp_url(config.url)
    config.token = str(config.token or "").strip()
    config.allowed_tools = sorted({str(tool).strip() for tool in config.allowed_tools if str(tool).strip()})
    return config


def _public_config(config: MCPServerConfig) -> Dict[str, Any]:
    """转换为管理 API 可返回的 MCP Server 配置。

    Args:
        config: MCP Server 配置。

    Returns:
        移除敏感字段后的配置字典。
    """
    payload = config.model_dump(mode="json")
    for field in SENSITIVE_CONFIG_FIELDS:
        payload.pop(field, None)
    return payload


def list_mcp_servers() -> Dict[str, Any]:
    """列出 MCP Server 配置。

    Returns:
        管理 API 使用的配置列表响应。
    """
    items = [_public_config(config) for config in load_mcp_server_configs()]
    return {"status": "success", "count": len(items), "items": items}


def load_mcp_server_configs() -> List[MCPServerConfig]:
    """加载全部 MCP Server 配置。

    Returns:
        MCP Server 配置对象列表。
    """
    payload = _read_raw_payload()
    configs: List[MCPServerConfig] = []
    for item in payload.get("servers", []):
        if not isinstance(item, dict):
            continue
        configs.append(_validate_config(MCPServerConfig.model_validate(item)))
    return configs


def get_mcp_server_config(name: str) -> MCPServerConfig | None:
    """按名称查询 MCP Server 配置。

    Args:
        name: MCP Server 名称。
    Returns:
        找到时返回配置对象，否则返回 None。
    """
    normalized_name = _validate_server_name(name)
    return next((config for config in load_mcp_server_configs() if config.name == normalized_name), None)


def get_enabled_mcp_server_configs() -> List[MCPServerConfig]:
    """查询已启用 MCP Server 配置。

    Returns:
        已启用的 MCP Server 配置。
    """
    return [config for config in load_mcp_server_configs() if config.enabled]


def create_mcp_server(request: MCPServerCreateRequest) -> Dict[str, Any]:
    """创建 MCP Server 配置。

    Args:
        request: 创建请求。
    Returns:
        创建结果和配置内容。

    Raises:
        ValueError: 名称重复或配置非法时抛出。
    """
    configs = load_mcp_server_configs()
    if any(config.name == request.name for config in configs):
        raise ValueError(f"MCP server already exists: {request.name}")
    config = _validate_config(
        MCPServerConfig(**request.model_dump())
    )
    configs.append(config)
    _write_configs(configs)
    return {"status": "success", "server": _public_config(config)}


def update_mcp_server(name: str, request: MCPServerUpdateRequest) -> Dict[str, Any]:
    """更新 MCP Server 配置。

    Args:
        name: MCP Server 名称。
        request: 更新请求。
    Returns:
        更新后的配置响应。

    Raises:
        ValueError: 名称不存在或配置非法时抛出。
    """
    normalized_name = _validate_server_name(name)
    configs = load_mcp_server_configs()
    target_index = next((index for index, config in enumerate(configs) if config.name == normalized_name), None)
    if target_index is None:
        raise ValueError(f"MCP server not found: {normalized_name}")

    current = configs[target_index]
    update_payload = request.model_dump(exclude_unset=True)
    next_config = _validate_config(
        MCPServerConfig(
            **{
                **current.model_dump(),
                **update_payload,
                "name": normalized_name,
            }
        )
    )
    configs[target_index] = next_config
    _write_configs(configs)
    return {"status": "success", "server": _public_config(next_config)}


def delete_mcp_server(name: str) -> Dict[str, Any]:
    """删除 MCP Server 配置。

    Args:
        name: MCP Server 名称。
    Returns:
        删除结果。

    Raises:
        ValueError: 名称不存在时抛出。
    """
    normalized_name = _validate_server_name(name)
    configs = load_mcp_server_configs()
    remaining = [config for config in configs if config.name != normalized_name]
    if len(remaining) == len(configs):
        raise ValueError(f"MCP server not found: {normalized_name}")
    _write_configs(remaining)
    return {"status": "success", "name": normalized_name}
