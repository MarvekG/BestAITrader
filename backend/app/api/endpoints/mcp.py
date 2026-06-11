from typing import Any, Dict

from fastapi import APIRouter

from app.ai.agentic.mcp.models import MCPServerCreateRequest, MCPServerUpdateRequest, MCPToolInvokeRequest
from app.ai.agentic.mcp.registry import (
    create_mcp_server,
    delete_mcp_server,
    list_mcp_servers,
    update_mcp_server,
)
from app.ai.agentic.mcp.runtime import MCPRuntimeError, build_mcp_catalog_prompt, invoke_mcp_tool, list_mcp_tools
from app.core.logger import get_logger


logger = get_logger(__name__)
router = APIRouter()


def _error_response(message: str) -> Dict[str, Any]:
    """构建 MCP 管理 API 错误响应。

    Args:
        message: 错误信息。

    Returns:
        统一错误响应。
    """
    return {"status": "error", "message": message}


@router.get("/servers", response_model=Dict[str, Any])
async def list_registered_mcp_servers() -> Dict[str, Any]:
    """列出已配置 MCP Server。

    Returns:
        MCP Server 完整配置列表。
    """
    try:
        return list_mcp_servers()
    except ValueError as exc:
        return _error_response(str(exc))


@router.post("/servers", response_model=Dict[str, Any])
async def create_registered_mcp_server(request: MCPServerCreateRequest) -> Dict[str, Any]:
    """创建 MCP Server 配置。

    Args:
        request: 创建请求。

    Returns:
        创建结果。
    """
    try:
        return create_mcp_server(request)
    except ValueError as exc:
        return _error_response(str(exc))
    except Exception as exc:
        logger.exception("Failed to create MCP server", extra={"name": request.name, "error": str(exc)})
        return _error_response(str(exc))


@router.put("/servers/{name}", response_model=Dict[str, Any])
async def update_registered_mcp_server(name: str, request: MCPServerUpdateRequest) -> Dict[str, Any]:
    """更新 MCP Server 配置。

    Args:
        name: MCP Server 名称。
        request: 更新请求。

    Returns:
        更新结果。
    """
    try:
        return update_mcp_server(name, request)
    except ValueError as exc:
        return _error_response(str(exc))
    except Exception as exc:
        logger.exception("Failed to update MCP server", extra={"name": name, "error": str(exc)})
        return _error_response(str(exc))


@router.delete("/servers/{name}", response_model=Dict[str, Any])
async def delete_registered_mcp_server(name: str) -> Dict[str, Any]:
    """删除 MCP Server 配置。

    Args:
        name: MCP Server 名称。

    Returns:
        删除结果。
    """
    try:
        return delete_mcp_server(name)
    except ValueError as exc:
        return _error_response(str(exc))
    except Exception as exc:
        logger.exception("Failed to delete MCP server", extra={"name": name, "error": str(exc)})
        return _error_response(str(exc))


@router.post("/servers/{name}/test", response_model=Dict[str, Any])
async def test_registered_mcp_server(name: str) -> Dict[str, Any]:
    """测试 MCP Server 连接。

    Args:
        name: MCP Server 名称。

    Returns:
        连接测试结果。
    """
    try:
        result = await list_mcp_tools(name)
        return {"status": "success", "name": name, "tool_count": result["count"], "tools": result["items"]}
    except (ValueError, MCPRuntimeError) as exc:
        return _error_response(str(exc))


@router.get("/servers/{name}/tools", response_model=Dict[str, Any])
async def list_registered_mcp_server_tools(name: str) -> Dict[str, Any]:
    """列出 MCP Server 工具。

    Args:
        name: MCP Server 名称。

    Returns:
        MCP 工具列表。
    """
    try:
        return await list_mcp_tools(name)
    except (ValueError, MCPRuntimeError) as exc:
        return _error_response(str(exc))


@router.post("/servers/{name}/tools/{tool_name}/invoke", response_model=Dict[str, Any])
async def invoke_registered_mcp_server_tool(
    name: str,
    tool_name: str,
    request: MCPToolInvokeRequest,
) -> Dict[str, Any]:
    """管理页试调用 MCP 工具。

    Args:
        name: MCP Server 名称。
        tool_name: MCP 原始工具名。
        request: 调用参数。

    Returns:
        工具调用结果。
    """
    try:
        return await invoke_mcp_tool(name, tool_name, request.arguments)
    except (ValueError, MCPRuntimeError) as exc:
        return _error_response(str(exc))


@router.get("/prompt", response_model=Dict[str, Any])
async def get_mcp_catalog_prompt() -> Dict[str, Any]:
    """获取 MCP catalog prompt。

    Returns:
        MCP prompt 文本。
    """
    return {"status": "success", "prompt": build_mcp_catalog_prompt()}
