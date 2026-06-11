from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, List

from app.ai.agentic.mcp.models import MCPServerConfig, MCPServerCreateRequest, MCPServerUpdateRequest
from app.core.config import PROJECT_ROOT


MCP_RUNTIME_ROOT = PROJECT_ROOT.parent / "runtimes" / "mcp"
MCP_SERVERS_FILE = MCP_RUNTIME_ROOT / "servers.json"
SERVER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _ensure_runtime_dir() -> None:
    """确保 MCP 运行时目录存在。"""
    MCP_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)


def _read_raw_payload() -> Dict[str, Any]:
    """读取 MCP 配置文件原始 JSON。

    Returns:
        配置文件中的原始字典，文件不存在时返回空 server 列表。

    Raises:
        ValueError: 配置文件不是合法 JSON 对象时抛出。
    """
    if not MCP_SERVERS_FILE.exists():
        return {"servers": []}
    try:
        payload = json.loads(MCP_SERVERS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"MCP server config is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("MCP server config must be a JSON object")
    servers = payload.get("servers")
    if servers is None:
        payload["servers"] = []
    elif not isinstance(servers, list):
        raise ValueError("MCP server config field `servers` must be a list")
    return payload


def _write_configs(configs: List[MCPServerConfig]) -> None:
    """将 MCP Server 配置写入运行时文件。

    Args:
        configs: MCP Server 配置列表。
    """
    _ensure_runtime_dir()
    payload = {
        "servers": [config.model_dump(mode="json") for config in sorted(configs, key=lambda item: item.name)],
    }
    MCP_SERVERS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_server_name(name: str) -> str:
    """校验并返回 MCP Server 名称。

    Args:
        name: 原始 MCP Server 名称。

    Returns:
        规整后的 MCP Server 名称。

    Raises:
        ValueError: 名称为空或格式非法时抛出。
    """
    normalized = str(name or "").strip()
    if not SERVER_NAME_PATTERN.fullmatch(normalized):
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
    return config


def list_mcp_servers() -> Dict[str, Any]:
    """列出 MCP Server 配置。

    Returns:
        管理 API 使用的配置列表响应。
    """
    items = [config.model_dump(mode="json") for config in load_mcp_server_configs()]
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
    return {"status": "success", "server": config.model_dump(mode="json")}


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
    return {"status": "success", "server": next_config.model_dump(mode="json")}


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
