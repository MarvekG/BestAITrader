# AI 模块设计与约束

`ai` 是投研智能体、模型路由、工具调用、选股、单股分析、市场观察、经验复盘和 Memory 集成的总入口。AI 模块的核心边界是：模型只在受控上下文、受控工具和结构化输出约束下工作。

## 子模块

- `llm_engine/`：单股多 Agent 投研辩论和 PM 决策工作流。
- `agentic/`：Agent 工具、新闻插件、Skills、沙箱、浏览器、PDF 和 Memory 工具。
- `stock_picker/interactive_research/`：交互式 Deep Research 选股、计划确认、工具循环和消息流。
- `stock_analysis/`：面向单股分析页面/API 的分析能力。
- `experience/`：PM 决策后验复盘、事件流和 Memory 写入判断。
- `market_watch/`：市场监控相关 AI 能力。
- `memory_client.py`：主后端访问 MemoFlux 的 HTTP client。
- `llm_routing.py`、`llm_providers/`：模型别名和 LiteLLM/OpenAI 兼容调用边界。

## 设计约束

- LLM 接入固定走 LiteLLM Proxy 和模型别名，真实 provider key、模型名和 base URL 不进入代码。
- Agent 事实材料优先由 context、tool 或 service 编译，不直接绑定数据库表结构拼 prompt。
- PM 是唯一能追加交易工具的 Agent；普通分析师不能直接下单。
- 交互式选股只输出推荐、观察名单、淘汰候选和证据摘要，不构建持仓组合、不执行交易。
- 后验评估统一落在 `experience`，不新增平行历史评估中心。
- 主后端只通过 `memory_client.py` 或 Memory 工具访问 MemoFlux HTTP API，不直接写 MemoFlux 数据库。
- Prompt 变更不要新增 pytest 字符串断言；通过人工审计、既有 eval 或明确 live eval 验证效果。

## 验证

- LLM/Agentic 修改运行相关 `test_llm_*`、`test_agentic_*`。
- 交互式选股修改运行 `PYTHONPATH=backend pytest backend/tests/test_interactive_stock_picker.py`。
- 经验复盘修改运行 `PYTHONPATH=backend pytest backend/tests/test_experience_workflow.py`。
