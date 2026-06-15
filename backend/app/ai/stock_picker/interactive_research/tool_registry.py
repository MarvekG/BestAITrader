from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from app.ai.agentic.mcp.runtime import get_mcp_tools
from app.ai.agentic.skills_loader.runtime import get_skills_loader_tools
from app.ai.agentic.tools import get_all_tools
from app.ai.stock_picker.interactive_research.flow_control import control_research_flow


ToolLoaderFactory = Callable[[Dict[str, Any]], "InteractiveResearchToolRegistry"]

TRADING_TOOL_NAMES = {
    "execute_trading_order",
    "get_pm_order_type_guidance",
}
TRADING_TOOL_NAME_MARKERS = {
    "account",
    "order",
    "portfolio",
    "position",
    "trade",
    "trading",
}


class InteractiveResearchToolRegistry:
    """为交互式研究加载可绑定到 LLM 的非交易工具。"""

    def __init__(self, state: Optional[Dict[str, Any]] = None) -> None:
        """初始化工具注册表。

        Args:
            state: 传给运行时工具的上下文。
        """
        self._state = dict(state or {})
        self._tools: Optional[List[Any]] = None

    async def aload_tools(self) -> List[Any]:
        """异步加载可绑定到 LLM 的工具。

        Returns:
            已过滤交易工具后的 LangChain 工具列表。
        """
        if self._tools is not None:
            return list(self._tools)

        tools: List[Any] = []
        tools.extend(get_all_tools())
        tools.extend(get_skills_loader_tools())
        tools.extend(await get_mcp_tools())
        tools.append(control_research_flow)

        self._tools = [tool for tool in tools if not is_trading_tool_name(str(getattr(tool, "name", "") or ""))]
        return list(self._tools)

    async def available_tool_names(self) -> List[str]:
        """返回交互式研究允许绑定的工具名称。

        Returns:
            已过滤交易工具后的工具名称列表。
        """
        tools = await self.aload_tools()
        return sorted(str(getattr(tool, "name", "")) for tool in tools if getattr(tool, "name", ""))


def is_trading_tool_name(tool_name: str) -> bool:
    """判断工具名是否属于交易、账户、订单、组合或仓位边界。

    Args:
        tool_name: LangChain 工具名称。

    Returns:
        True 表示 interactive research 不允许绑定或调用该工具。
    """
    normalized = str(tool_name or "").strip().lower()
    if normalized in TRADING_TOOL_NAMES:
        return True
    return any(marker in normalized for marker in TRADING_TOOL_NAME_MARKERS)
