from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MCPServerConfig(BaseModel):
    """MCP Server 运行时配置。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool = False
    url: str = Field(..., min_length=1)
    token: str = ""
    allowed_tools: List[str] = Field(default_factory=list)


class MCPServerCreateRequest(BaseModel):
    """创建 MCP Server 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool = False
    url: str = Field(..., min_length=1)
    token: str = ""
    allowed_tools: List[str] = Field(..., min_length=1)


class MCPServerUpdateRequest(BaseModel):
    """更新 MCP Server 的请求体。"""

    model_config = ConfigDict(extra="forbid")

    enabled: Optional[bool] = None
    url: Optional[str] = Field(default=None, min_length=1)
    token: Optional[str] = None
    allowed_tools: Optional[List[str]] = Field(default=None, min_length=1)


class MCPToolPreviewRequest(BaseModel):
    """提交前预览 MCP Server 工具的请求体。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field("preview", min_length=1, max_length=64)
    url: str = Field(..., min_length=1)
    token: str = ""


class MCPToolInvokeRequest(BaseModel):
    """管理页试调用 MCP 工具的请求体。"""

    arguments: Dict[str, Any] = Field(default_factory=dict)
