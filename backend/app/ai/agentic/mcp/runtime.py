from __future__ import annotations

from typing import Any, Dict, List

from app.ai.agentic.mcp.registry import get_enabled_mcp_server_configs, get_mcp_server_config
from app.core.logger import get_logger


logger = get_logger(__name__)


class MCPRuntimeError(RuntimeError):
    """MCP 运行时错误。"""


async def get_mcp_tools() -> List[Any]:
    """返回可绑定的 MCP LangChain 工具。

    Returns:
        已启用 MCP Server 暴露的 MCP 工具列表。
    """
    tools: List[Any] = []
    for config in get_enabled_mcp_server_configs():
        try:
            server_tools = await list_mcp_langchain_tools(config.name)
        except Exception as exc:
            logger.warning(
                "MCP server tools unavailable",
                extra={"name": config.name, "error": str(exc)},
            )
            continue
        tools.extend(server_tools)
    return tools


def build_mcp_catalog_prompt() -> str:
    """生成可用 MCP Server 摘要。

    Returns:
        可注入系统提示词的 MCP catalog 文本。
    """
    lines = ["# Available MCP Tools", ""]
    for config in get_enabled_mcp_server_configs():
        lines.append(f"- {config.name}")
    return "\n".join(lines).strip() if len(lines) > 2 else ""


async def list_mcp_langchain_tools(name: str) -> List[Any]:
    """按配置创建官方 MCP client 并返回 LangChain 工具。

    Args:
        name: MCP Server 名称。

    Returns:
        官方 adapter 返回的 LangChain 工具列表。

    Raises:
        MCPRuntimeError: 配置不存在、依赖缺失或工具获取失败时抛出。
    """
    config = get_mcp_server_config(name)
    if config is None:
        raise MCPRuntimeError(f"MCP server not found: {name}")
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient({config.name: build_adapter_config(config)})
        return await client.get_tools()
    except ImportError as exc:
        raise MCPRuntimeError("Python package `langchain-mcp-adapters` is required to use MCP tools") from exc
    except Exception as exc:
        raise MCPRuntimeError(str(exc)) from exc


def build_adapter_config(config: Any) -> Dict[str, Any]:
    """构建官方 MCP adapter 的 server 配置。

    Args:
        config: MCP Server 配置。

    Returns:
        `MultiServerMCPClient` 可识别的 server 配置。
    """
    return {
        "transport": "streamable_http",
        "url": config.url,
    }


async def list_mcp_tools(name: str) -> Dict[str, Any]:
    """列出 MCP Server 的工具元数据。

    Args:
        name: MCP Server 名称。

    Returns:
        工具列表响应。
    """
    tools = await list_mcp_langchain_tools(name)
    items = [tool_to_item(name, tool) for tool in tools]
    return {"status": "success", "name": name, "count": len(items), "items": items}


async def invoke_mcp_tool(name: str, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """调用 MCP 工具。

    Args:
        name: MCP Server 名称。
        tool_name: MCP 原始工具名。
        arguments: 工具入参。

    Returns:
        工具调用结果。

    Raises:
        MCPRuntimeError: 工具不存在或调用失败时抛出。
    """
    for tool in await list_mcp_langchain_tools(name):
        if normalize_tool_name(name, str(getattr(tool, "name", "") or "")) == tool_name:
            try:
                result = await tool.ainvoke(arguments or {})
            except Exception as exc:
                raise MCPRuntimeError(str(exc)) from exc
            return {"status": "success", "name": name, "tool_name": tool_name, "result": json_safe(result)}
    raise MCPRuntimeError(f"MCP tool not found: {tool_name}")


def tool_to_item(name: str, tool: Any) -> Dict[str, Any]:
    """将 LangChain 工具转换为管理 API 条目。

    Args:
        name: MCP Server 名称。
        tool: LangChain 工具对象。

    Returns:
        工具元数据字典。
    """
    raw_name = str(getattr(tool, "name", "") or "")
    args_schema = getattr(tool, "args_schema", None)
    input_schema = args_schema.model_json_schema() if args_schema is not None else {}
    return {
        "server": name,
        "name": normalize_tool_name(name, raw_name),
        "langchain_name": raw_name,
        "description": str(getattr(tool, "description", "") or ""),
        "input_schema": input_schema,
    }


def normalize_tool_name(name: str, tool_name: str) -> str:
    """还原官方 adapter 可能添加的 server 前缀。

    Args:
        name: MCP Server 名称。
        tool_name: LangChain 工具名。

    Returns:
        MCP 原始工具名。
    """
    for prefix in (f"{name}__", f"{name}_", f"mcp__{name}__"):
        if tool_name.startswith(prefix):
            return tool_name[len(prefix):]
    return tool_name


def json_safe(value: Any) -> Any:
    """递归转换为 JSON 安全值。

    Args:
        value: 任意 Python 值。

    Returns:
        可 JSON 序列化的值。
    """
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
