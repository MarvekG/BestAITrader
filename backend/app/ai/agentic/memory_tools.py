from typing import Any, Dict, List, Literal, Optional

from langchain.tools import tool

from app.ai.memory_client import memory_client
from app.ai.llm_engine.roles import (
    MEMORY_ENABLED_AGENT_NAMES,
)

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
    allow_recall = agent_role in MEMORY_RECALL_ALLOWED_ROLES and target_stock_code is not None
    allow_write = agent_role in MEMORY_WRITE_ALLOWED_ROLES and target_stock_code is not None

    if allow_recall:
        @tool
        async def recall_memory(
            query: str,
        ) -> Dict[str, Any]:
            """
            读取当前用户在已绑定股票下的历史记忆。
            工具会自动绑定到分析目标股票，不支持通用记忆，也不接受外部传入的 `stock_code`。
            可用于检索历史观察、复用规则、争议结论、执行教训、复盘笔记或其他高价值经验。
            这不是当前事实源，不能替代实时行情、财务数据、新闻或政策检索。
            记忆召回协议:
            1. 使用时机: 仅在历史记忆确实能降低不确定性时使用，避免机械重复调用；默认优先做一次小而精的检索，先把 query 写窄。
            2. Query 结构: query 必须写成“真实股票名 + 股票代码 + 要复用的经验主题 + 2-5 个关键变量/动作/触发器”。不要写“当前目标股票”这种占位词；要从 Context 的 `_target_stock_name` 和 `_target_stock_code` 同时取真实股票名和股票代码写进 query。
            3. 主题召回: 不按 Agent 角色固定召回主题，也不要求每轮检查所有主题。根据当前不确定性、证据缺口、交易频率、交易策略和市场环境，自主决定是否召回以及召回哪些主题。
            3.1 [MEMORY_TOPIC: decision_outcome]: 当需要对比历史类似 PM 决策结果、后验收益、回撤或结论正确性时召回。
            3.2 [MEMORY_TOPIC: risk_control]: 当需要输出仓位、止损、`buy`/`sell`/`hold` 或失效条件时召回。
            3.3 [MEMORY_TOPIC: driver_validation]: 当需要判断当前核心驱动、信号或噪音是否已有历史验证/证伪经验时召回。
            3.4 [MEMORY_TOPIC: strategy_fit]: 当需要判断历史经验是否适配当前交易频率、交易策略或市场环境时召回。
            3.5 [MEMORY_TOPIC: process_improvement]: 当需要检查本轮 Debate / PM / 风控流程是否可能重复历史流程缺陷时召回。
            4. Query 示例: “交通银行(601328.SH) [MEMORY_TOPIC: risk_control] 中长线 价值投资 银行Beta 仓位 止损 加仓”；“交通银行(601328.SH) [MEMORY_TOPIC: driver_validation] 业绩说明会 高股息 PB低估 板块资金流”；“交通银行(601328.SH) [MEMORY_TOPIC: process_improvement] Debate PM 风控 检查项 催化验证”；“中远海控(601919.SH) PM裁决 HOLD 仓位 止损 加仓触发”；“中远海控(601919.SH) 运价反弹 SCFI 加仓纪律 失效条件”。
            5. 限制: 时间意图、问题类型不是必填项；只有在区分当前/历史、事实/原因、支持/冲突或演进关系时才补充。不要只写“查一下历史经验”“以前怎么样”这类宽泛 query。若首次召回结果相关性弱，不要反复重试宽泛 query；应回到当前事实分析。
            """
            if not user_id or not target_stock_code:
                return {"items": [], "error": "stock-bound memory context unavailable"}

            data = await memory_client.recall(
                user_id=user_id,
                stock_code=target_stock_code,
                query=query,
            )
            references = data.get("references") if isinstance(data, dict) else []
            reference_count = len(references) if isinstance(references, list) else 0
            result = {
                "data": data if isinstance(data, dict) else {},
                "count": reference_count,
                "memo_session": "stock",
                "stock_code": target_stock_code,
            }
            last_error = memory_client.get_last_error("recall")
            if last_error:
                result["error"] = last_error.get("message") or "memory recall request failed"
            return result

        tools.append(recall_memory)

    if allow_write:
        @tool
        async def write_memory(
            content: str,
            importance: MemoryImportanceLiteral,
        ) -> Dict[str, Any]:
            """
            写入当前用户在当前目标股票下的可复用记忆。
            工具会自动绑定到当前分析目标股票，不支持通用记忆，也不接受外部传入的 `stock_code`。
            记忆写入协议:
            1. 写入前提: 仅在内容对未来复用确有价值时使用，可写观察、规则、结论、教训、复盘或自定义高价值笔记。如果没有新增规则、阈值、失效条件、失败模式或执行纪律，应跳过写入。
            2. 内容要素: 能让系统持续进步的记忆必须同时包含真实股票名和股票代码，并写清场景、交易频率、交易策略、关键证据、决策/结论、触发器、失效条件、阈值、常见误判、执行纪律和后续验证点；若交易频率或交易策略无法确认，必须在正文中说明缺失。
            3. 协议主题: 写入时优先使用以下主题。
            3.1 [MEMORY_TOPIC: decision_outcome]: 如果原始 PM 结论有明确后验结果，记录原始决策、目标仓位、置信度、止损/加仓计划、后续收益/回撤/相对收益和结论正确性。
            3.2 [MEMORY_TOPIC: driver_validation]: 如果能区分被验证、被证伪和噪音信号，记录主导驱动、被验证信号、被证伪信号、噪音信号和被排除伪因。
            3.3 [MEMORY_TOPIC: risk_control]: 如果仓位、止损、`buy`/`sell`/`hold` 或回撤管理有教训，记录仓位大小、买入/卖出/持有条件、止损是否缺失/失效、流动性、板块 Beta、事件落地和失效条件。
            3.4 [MEMORY_TOPIC: strategy_fit]: 如果经验的适用频率、策略或市场环境存在明显边界，记录适用的交易频率、交易策略、市场环境、失效环境和经验是否过时及原因。
            3.5 [MEMORY_TOPIC: process_improvement]: 如果能提炼出未来 Debate / PM / Risk 的流程检查项，记录哪个 Agent 要补什么证据、哪类推理错误要避免、PM 如何调整仓位/置信度/卖出设计、Risk Control 要检查哪些否决条件。
            4. 拆分规则: 一条 Memory 只写一个主主题；不同主题分次调用 `write_memory`，不要把多个主题揉成一条。
            5. 推荐结构: 单条 Memory 正文建议包含 [MEMORY_TOPIC: ...]、对象:、交易频率:、交易策略:、场景:、经验:、触发条件:、未来动作:、失效边界:、证据:。对象必须同时包含真实股票名和股票代码；复盘写入必须包含后验市场结果或信号验证证据；Debate 内部写入不能伪造未来后验结果。
            6. 禁止事项: 优先写高信息密度内容，不要把普通背景信息、重复事实或流水账写入记忆。需要沉淀的触发器、失效条件、置信度或执行教训，直接写进 `content` 本身，不要依赖额外字段包装。示例: “中远海控(601919.SH) PM裁决经验: 盈利同比-49.75%但SCFI两周+13.47%时，不因单一运价反弹追涨；若SCFI连续3周站稳2200且Q2净利降幅收窄至20%以内才加仓，若SCFI跌破1900或价格跌破13.80则减仓，硬止损12.50。误判风险: 高股息是后视镜数据。”
            7. 异步语义: 写入为异步生效，不保证当前轮次、当前步骤或当前调用链内立即可读。禁止先调用 `write_memory`，再马上用 `recall_memory` 读取刚写入的内容。`importance` 可选值: `low`, `medium`, `high`。
            """
            if not user_id or not target_stock_code:
                return {"success": False, "error": "stock-bound memory context unavailable"}
            normalized_importance = importance.strip().lower()
            if normalized_importance not in MEMORY_IMPORTANCE_LEVELS:
                return {"success": False, "error": f"unsupported importance: {normalized_importance}"}

            stored_content = content.strip()
            response = await memory_client.write_memory(
                user_id=user_id,
                stock_code=target_stock_code,
                content=stored_content,
            )
            last_error = memory_client.get_last_error("ingest")
            success = bool(response) and last_error is None
            observation_id = response.get("observation_id") if isinstance(response, dict) else None
            result = {
                "success": success,
                "memo_session": "stock",
                "stock_code": target_stock_code,
                "status": response.get("status") if isinstance(response, dict) else "unknown",
                "observation_id": observation_id,
            }
            if last_error:
                result["error"] = last_error.get("message") or "memory write request failed"
            return result

        tools.append(write_memory)

    return tools
