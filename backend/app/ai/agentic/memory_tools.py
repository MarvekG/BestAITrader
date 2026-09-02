from typing import Any, Dict, List, Literal, Optional

from langchain.tools import tool

from app.ai.llm_engine.roles import (
    MEMORY_ENABLED_AGENT_NAMES,
)
from app.ai.memory_documents import store

MEMORY_IMPORTANCE_LEVELS = {"low", "medium", "high"}
MemoryImportanceLiteral = Literal["low", "medium", "high"]
MEMORY_RECALL_ALLOWED_ROLES = MEMORY_ENABLED_AGENT_NAMES
MEMORY_WRITE_ALLOWED_ROLES = MEMORY_ENABLED_AGENT_NAMES

def build_memory_tools(
    *,
    state: Optional[Dict[str, Any]] = None,
) -> List[Any]:
    tools: List[Any] = []
    memory_state = dict(state or {})
    agent_role = memory_state.get("agent_role")
    user_id = memory_state.get("user_id")
    target_stock_code = str(memory_state.get("stock_code") or "").strip() or None
    allow_read = agent_role in MEMORY_RECALL_ALLOWED_ROLES and target_stock_code is not None
    allow_write = agent_role in MEMORY_WRITE_ALLOWED_ROLES and target_stock_code is not None

    if allow_read:
        @tool
        async def read_memory() -> Dict[str, Any]:
            """
            读取当前用户在已绑定股票下的整份记忆文档。
            工具自动绑定到分析目标股票，不支持通用记忆，也不接受外部传入的 `stock_code`。
            文档是自由格式的 Markdown 活文档，由历史复盘与辩论沉淀的可复用经验组成，
            可包含决策对错、驱动验证、风控教训、策略边界和流程改进等内容。
            这不是当前事实源，不能替代实时行情、财务数据、新闻或政策检索。
            使用协议:
            1. 使用时机: 仅当历史经验可能实质影响当前判断、仓位、止损、置信度或执行计划时调用；不要机械调用。
            2. 返回内容: `content` 为整份文档全文，`version` 为当前版本号，`size_chars` 为文档字符数。
            3. 文档尚不存在时 `exists` 为 `false`，表示该股票还没有沉淀过经验，可直接返回，不要重复调用。
            """
            if not user_id or not target_stock_code:
                return {"exists": False, "content": "", "error": "stock-bound memory context unavailable"}

            data = await store.read_document(user_id=user_id, stock_code=target_stock_code)
            result = {
                "exists": data.get("exists"),
                "content": data.get("content") or "",
                "size_chars": data.get("size_chars") or 0,
                "version": data.get("version") or 0,
                "stock_code": target_stock_code,
            }
            return result

        tools.append(read_memory)

    if allow_write:
        @tool
        async def write_memory(
            content: str,
            importance: MemoryImportanceLiteral,
            base_version: int,
        ) -> Dict[str, Any]:
            """
            整文档替换写入当前用户在当前目标股票下的记忆文档。
            工具自动绑定到当前分析目标股票，不支持通用记忆，也不接受外部传入的 `stock_code`。
            写入协议:
            1. 写前必读: 必须先调用 `read_memory` 获取最新全文和 `version`，并把 `version` 作为 `base_version` 传入；`base_version` 与当前版本不一致会写入失败。
            2. 整文档替换: `content` 必须是重写后的完整文档全文，不是追加条目；未包含进 `content` 的旧内容将被永久丢弃，因此重写时必须保留仍然有效的历史经验。
            3. 自由格式: 文档结构由你自主组织，不设固定模板。建议按时间组织、标注决策与复盘日期、保留后验收益与信号验证证据、合并过时或重复内容，保证高信息密度。
            4. 内容要素: 经验应能让未来决策直接复用，覆盖场景、交易频率、交易策略、关键证据、触发条件、失效边界、执行纪律等；禁止把普通背景信息或流水账写入。
            5. 容量上限: 文档超过系统上限时写入失败，必须在同一轮先精炼合并旧内容再重试，不要直接放弃。
            6. 版本冲突: 写入失败并返回最新全文时，把你的新增经验合并进最新全文后用返回的版本号重试；并行 Agent 写入冲突时禁止覆盖他人新增内容。
            7. 写入时机: 只写可复用规则、触发条件、失败模式、执行纪律或证据权重；Debate 内部不得伪造未来后验结果；没有新增经验时应跳过写入。
            8. `importance` 是本次写入的元数据标注，可选值: `low`, `medium`, `high`。
            """
            if not user_id or not target_stock_code:
                return {"success": False, "error": "stock-bound memory context unavailable"}
            normalized_importance = importance.strip().lower()
            if normalized_importance not in MEMORY_IMPORTANCE_LEVELS:
                return {"success": False, "error": f"unsupported importance: {normalized_importance}"}
            if not isinstance(base_version, int) or base_version < 0:
                return {"success": False, "error": "base_version must be a non-negative integer from read_memory"}

            result = await store.update_document(
                user_id=user_id,
                stock_code=target_stock_code,
                content=content,
                base_version=base_version,
            )
            return result

        tools.append(write_memory)

    return tools
