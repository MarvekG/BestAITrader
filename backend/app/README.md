# Backend Capability Map

本文档说明 `backend/app` 这块后端代码到底厉害在哪，以及这些能力分别落在哪些模块里。

## 1. 系统优势

天枢智投（Best-AI-Trader）后端不是“把行情数据塞进 prompt 让模型猜涨跌”的 demo，而是一套把 Agentic AI、A 股数据工程、模拟交易、长期记忆、经验复盘和实时可观测性工程化到一起的投研操作系统。

它真正强的地方在于：模型不是孤立聊天，而是在一个有工具、有证据、有上下文编译、有角色分工、有记忆、有交易账本、有后验复盘的系统里工作。

### Agentic AI 不是口号

系统把一次单股投研拆成上下文构建、新闻/政策/情绪/垂直分析、多空辩论、PM 决策和持久化审计。Agent 能调用行情、财务、新闻、数据库、Python 沙箱、Skills 和 Memory 工具，输出还会经过结构化校验、重试和落库。

### A 股上下文是编译出来的

后端不是临时拼一段股票资料，而是把目标股票编译成 `metadata / realtime / snapshot / history / signals / events` 六层上下文。每层有覆盖状态、缺失状态和错误信息，再通过 adapter 分发给不同 Agent。

### 情报系统不是单一搜索框

新闻能力是插件矩阵：Tavily、政府网政策、交易所公告、巨潮披露，以及部署方自行审查并安装的外部新闻插件都能接入 `search_news`，Agent 可以按来源选择证据。

### 数据底座覆盖 A 股关键维度

系统接入的不只是 K 线，还覆盖估值、财务、北向资金、龙虎榜、两融、大宗交易、涨跌停池、炸板池、股权质押、限售解禁、股东人数、十大股东、基金持仓、板块资金流等 A 股特色数据。

### 模型能安全做计算

Agent 可以调用 Python 做数值计算和表格分析，但不是直接执行宿主代码。后端用 Deno + Pyodide 跑计算沙箱，并用 AST 校验拦截危险 import、危险调用、文件访问、网络访问和逃逸属性链。

### 经验能回流到长期记忆

经验复盘系统会把 PM 决策、Agent timeline、订单成交和后验市场结果放在一起复核，找出被市场验证/证伪的信号，再把可复用规则写回 Memory。系统因此不是每次从零分析，而是能从历史判断中进化。

### 工程闭环完整

后端有异步任务、Redis 通知、WebSocket 订阅、LLM token 统计、Prompt 静态查看、系统自检、数据刷新调度、任务中断恢复、独立沙箱服务、独立网页渲染服务和结构化日志。它不是一个脚本，而是一套可以部署、排障、观察和迭代的系统。

## 2. 怎么实现

### 2.1 AI 投研工作流

核心代码：

- [`ai/llm_engine/orchestrator.py`](./ai/llm_engine/orchestrator.py)：LangGraph 节点、边、状态和 PM 决策持久化。
- [`ai/llm_engine/agents/base.py`](./ai/llm_engine/agents/base.py)：通用 Agent 执行器，封装 LLM、工具循环、结构化输出和重试。
- [`ai/llm_engine/agents/specialists.py`](./ai/llm_engine/agents/specialists.py)：新闻、政策、情绪、基本面、技术面、资金流、风控分析师。
- [`ai/llm_engine/agents/strategic.py`](./ai/llm_engine/agents/strategic.py)：多头、空头、激进、保守、中性等战略辩论角色。
- [`ai/llm_engine/agents/governance.py`](./ai/llm_engine/agents/governance.py)：PM Agent 和交易工具包装层。

关键机制：

- LangGraph 编排多阶段分析和错误闸门。
- `BaseAgent` 统一处理工具调用、无效 tool call 清理、长输出摘要和 Pydantic 结构化校验。
- LLM usage 通过 [`crud/llm_usage_log.py`](./crud/llm_usage_log.py) 记录，供前端统计调用次数和 token。

### 2.2 AI Context Compiler

核心代码：

- [`ai/llm_engine/context/service.py`](./ai/llm_engine/context/service.py)：统一构建 AI 上下文。
- [`ai/llm_engine/context/providers.py`](./ai/llm_engine/context/providers.py)：六层上下文 provider。
- [`ai/llm_engine/context/adapters.py`](./ai/llm_engine/context/adapters.py)：把统一上下文裁剪成各 Agent 所需输入。
- [`ai/llm_engine/context/fundamental.py`](./ai/llm_engine/context/fundamental.py)、[`technical.py`](./ai/llm_engine/context/technical.py)、[`capital_flow.py`](./ai/llm_engine/context/capital_flow.py)、[`risk.py`](./ai/llm_engine/context/risk.py)、[`sentiment.py`](./ai/llm_engine/context/sentiment.py)：具体数据读取和信号派生。

关键机制：

- 每个上下文层都有 `status`，能表达 available、partial、missing、error。
- 资金、风险、事件等派生信号在 context 层提前整理，减少 Agent 直接处理原始表的负担。
- 财报字段通过 [`data/metadata/financial_report_localizer.py`](./data/metadata/financial_report_localizer.py) 做本地化和结构清理。

### 2.3 Agent 工具体系

核心代码：

- [`ai/agentic/tools.py`](./ai/agentic/tools.py)：统一股票数据、市场数据、数据库查询、同步数据、沙箱计算和交易工具。
- [`ai/agentic/tooling/news_tool.py`](./ai/agentic/tooling/news_tool.py)：`search_news` 统一入口。
- [`ai/agentic/tooling/news_plugins/registry.py`](./ai/agentic/tooling/news_plugins/registry.py)：新闻插件自动发现和注册。
- [`ai/agentic/tooling/python_sandbox.py`](./ai/agentic/tooling/python_sandbox.py)：后端 AST 校验和独立 `sandbox` 服务 HTTP 调用入口。
- [`ai/agentic/tooling/browser_tool.py`](./ai/agentic/tooling/browser_tool.py)：独立 `webfetch` 服务网页渲染和 HTML/Markdown 抓取入口。
- [`ai/agentic/tooling/pdf_tool.py`](./ai/agentic/tooling/pdf_tool.py)：通过 `webfetch` 下载 PDF 后解析为 Markdown。
- [`ai/agentic/skills_loader/runtime.py`](./ai/agentic/skills_loader/runtime.py)：Skills loader 运行时工具。
- [`ai/agentic/memory_tools.py`](./ai/agentic/memory_tools.py)：通过 MemoFlux 提供 Memory 召回和写入工具。

关键机制：

- `search_news` 要求 Agent 显式选择来源，避免所有新闻源混在一起造成证据污染。
- 沙箱在后端先用 AST 拦截危险语法和调用，再通过 `PY_SANDBOX_BASE_URL` 交给独立 `sandbox` 容器执行纯计算任务。
- 浏览器和 PDF 工具通过 `WEBFETCH_BASE_URL` 调用独立 `webfetch` 容器，后端不直接管理浏览器生命周期。
- Skills 用 `SKILL.md + references + scripts` 扩展 Agent 能力，当前内置 Tushare 数据 skill。
- 新增 Skills 的完整规范见 [`ai/agentic/skills_loader/README.md`](./ai/agentic/skills_loader/README.md)。
- 替换和开发新闻插件的完整规范见 [`ai/agentic/tooling/news_plugins/README.md`](./ai/agentic/tooling/news_plugins/README.md)。

### 2.4 数据工程底座

核心代码：

- [`data/ingestors/plugin_loader.py`](./data/ingestors/plugin_loader.py)：数据源插件自动发现。
- [`data/ingestors/manager.py`](./data/ingestors/manager.py)：统一采集入口、默认数据源和 failover。
- [`data/ingestion/service.py`](./data/ingestion/service.py)：DataFrame 落库、去重、upsert、结构表/通用表写入。
- [`models/data_storage.py`](./models/data_storage.py)：A 股市场数据模型。
- [`models/data_registry.py`](./models/data_registry.py)：API 注册表和通用 JSON 数据表定义。
- [`data/refresh_scheduler.py`](./data/refresh_scheduler.py)：自动刷新调度。
- [`data/analytics/indicators.py`](./data/analytics/indicators.py)：技术指标批量计算和 upsert。

关键机制：

- 插件只要继承 `BaseIngestor` 并放入 `data/ingestors/plugins`，就能被自动发现。
- `IngestorManager` 按默认源、Tushare、其他插件顺序尝试，支持 failover。
- `DataIngestionService` 自动过滤无效股票代码、按唯一约束去重，并执行 PostgreSQL upsert。
- 调度器错峰刷新基础资料、K 线、指数、技术指标、资金流、龙虎榜、北向、估值和盘中动态。

### 2.5 AI 智能选股和经验复盘

核心代码：

- [`ai/stock_picker/service.py`](./ai/stock_picker/service.py)：股票池构建、因子初排、LLM 深研和推荐生成。
- [`ai/stock_picker/models.py`](./ai/stock_picker/models.py)：选股 run、事件、候选表。
- [`ai/experience/service.py`](./ai/experience/service.py)：经验复盘任务、上下文构建、事件持久化和结果查询。
- [`ai/experience/workflow.py`](./ai/experience/workflow.py)：复盘 LangGraph、LLM 工具循环和结构化复盘输出。

关键机制：

- 选股先用确定性规则压缩候选池，再让 LLM 做整池深研，避免全市场逐只调用模型。
- 复盘读取 PM 决策后的市场结果，检查收益、回撤、相对指数、相对行业表现。
- 经验复盘只在提炼出可复用赚钱经验、失败教训、仓位纪律或流程改进时写入 MemoFlux Memory；没有新增可复用经验时允许跳过写入。

### 2.6 模拟交易账本

核心代码：

- [`trading/service.py`](./trading/service.py)：账户、持仓、订单、成交记录的一致性写库。
- [`trading/trading_engine.py`](./trading/trading_engine.py)：纯计算交易引擎。
- [`api/endpoints/trading.py`](./api/endpoints/trading.py)：手动/API 下单入口。
- [`api/endpoints/accounts.py`](./api/endpoints/accounts.py)：账户、资产和持仓查询。

关键机制：

- `TradingService` 对账户和持仓加锁，统一写入订单、成交和持仓快照。
- `TradingEngine` 处理 A 股 100 股一手、T+1 可卖股数、FIFO 批次账本、手续费、止损/止盈判断。
- PM 专属交易工具只在 `PortfolioManagerAgent` 中挂载，普通分析师不能直接下单。

### 2.7 可观测性和运维能力

核心代码：

- [`tasks/task_manager.py`](./tasks/task_manager.py)：异步任务状态、去重和 Redis 通知。
- [`websocket/manager.py`](./websocket/manager.py)：任务、价格、选股、经验复盘等事件推送。
- [`api/endpoints/testing.py`](./api/endpoints/testing.py)：系统自检接口。
- [`api/endpoints/prompt.py`](./api/endpoints/prompt.py)：Prompt 静态查看。
- [`api/endpoints/llm.py`](./api/endpoints/llm.py)：LLM 健康检查、模型信息和用量统计。
- [`core/logger.py`](./core/logger.py)：结构化日志和 request id 注入。

关键机制：

- WebSocket 支持按资源订阅，前端重连后会自动恢复订阅。
- 自检中心覆盖 Redis、DB、Tushare、Tavily、沙箱、Skills、新闻插件、MemoFlux 读写和 DB schema。
- Prompt 由代码中的静态模板管理，避免运行时覆盖破坏 LLM 缓存前缀稳定性。

## 3. 详细技术点清单

| 技术点 | 代码位置 | 说明 |
| --- | --- | --- |
| LangGraph 多阶段投研图 | `ai/llm_engine/orchestrator.py` | `fetch_context` 后并行跑新闻、政策、情绪、垂直分析，再进入战略辩论和 PM 决策 |
| Agent 统一执行器 | `ai/llm_engine/agents/base.py` | 统一封装 LLM 调用、工具循环、结构化输出、重试、长输出摘要和错误处理 |
| PM 专属交易工具 | `ai/llm_engine/agents/governance.py` | 下单工具只挂给 PM，普通分析师不能直接交易 |
| 长工具输出压缩 | `ai/agentic/tool_output_summarizer.py` | 对长新闻结果做信息密度保留型摘要，降低上下文爆炸风险 |
| AI Context 六层模型 | `ai/llm_engine/context/providers.py` | 构建 `metadata / realtime / snapshot / history / signals / events` 分层上下文 |
| Agent Context Adapter | `ai/llm_engine/context/adapters.py` | 按 Agent 角色裁剪上下文，让不同分析师看到不同视角 |
| A 股风险信号 | `ai/llm_engine/context/risk.py` | 处理质押、解禁、监管、股东变化、财务风险等信号 |
| 资金流信号 | `ai/llm_engine/context/capital_flow.py` | 处理北向资金、龙虎榜、两融、大宗交易、板块资金流等数据 |
| 财报字段本地化 | `data/metadata/financial_report_localizer.py` | 把 JSONB 财报字段转成更适合中文 Agent 阅读的结构 |
| 多源新闻插件注册 | `ai/agentic/tooling/news_plugins/registry.py` | 自动扫描内置和 external 新闻插件，形成可选来源列表 |
| 统一新闻工具 | `ai/agentic/tooling/news_tool.py` | `search_news` 强制指定来源，避免多源结果混杂 |
| 独立 Python 沙箱 | `ai/agentic/tooling/python_sandbox.py` | 后端 AST 校验危险 import/call，再通过 `sandbox` 服务受限执行计算 |
| 独立 WebFetch 服务 | `ai/agentic/tooling/browser_tool.py` | 通过 `webfetch` 服务渲染网页并返回 HTML 或 Markdown |
| Skills Loader | `ai/agentic/skills_loader/runtime.py` | 让 Agent 读取 `SKILL.md`、references 和 scripts 扩展专业能力 |
| Memory 工具绑定 | `ai/agentic/memory_tools.py` | 自动绑定用户和股票 scope，提供 `recall_memory` / `write_memory` |
| 数据源插件发现 | `data/ingestors/plugin_loader.py` | 自动发现 `*_ingestor.py`，无需手动改注册表 |
| 数据源 failover | `data/ingestors/manager.py` | 按默认源、Tushare、其他插件顺序尝试，失败自动切换 |
| DataFrame 智能落库 | `data/ingestion/service.py` | 自动过滤无效股票、按唯一约束去重、执行 PostgreSQL upsert |
| 技术指标批量计算 | `data/analytics/indicators.py` | 计算 MA、MACD、RSI、KDJ、CCI、WR、BOLL、ATR、OBV 等指标 |
| 自动刷新调度 | `data/refresh_scheduler.py` | APScheduler 错峰刷新日线、指数、资金流、龙虎榜、北向、热榜和实时行情 |
| AI 智能选股任务 | `ai/stock_picker/service.py` | 股票池过滤、因子初排、LLM 深研、推荐生成和事件记录 |
| 经验复盘任务 | `ai/experience/workflow.py` | 基于 PM 决策后的真实价格路径输出复盘 JSON，并按可复用经验价值选择是否写入记忆 |
| 经验复盘调度 | `tasks/experience_review_scheduler.py` | 默认关闭，可配置盘后扫描可复盘 session 并自动发起经验分析任务 |
| FIFO 持仓账本 | `trading/trading_engine.py` | 维护买入批次、T+1 可卖股数、费用、止损/止盈判断 |
| 交易一致性写库 | `trading/service.py` | 对账户和持仓加锁，统一写 orders、positions、trade_records |
| 异步任务通知 | `tasks/task_manager.py` | 记录任务状态并通过 Redis 发布完成/失败事件 |
| WebSocket 资源订阅 | `websocket/manager.py` | 支持任务、价格、选股、经验复盘等实时事件推送 |
| Prompt 静态查看 | `api/endpoints/prompt.py` | 前端可查看当前 Agent prompt，运行时不支持覆盖 |
| LLM 用量统计与探针 | `api/endpoints/llm.py` | 合并后端和 Memory 的模型调用次数、token 用量，并提供 LLM thinking / tool / skills 探针 |
| 系统自检中心 | `api/endpoints/testing.py` | 覆盖 DB、Redis、Tushare、Tavily、新闻插件、沙箱、Skills、Memory |
| 结构化日志 | `core/logger.py` | 注入 request id、source 和 extra 字段，后端日志同时输出 console 和文件 |
| 用户股票仓库 | `api/endpoints/stock_warehouse.py` | 用户级股票池、上证 50 初始化、添加时补齐基础信息、删除时检查持仓 |

## 4. 当前边界

- 后验评估能力当前统一落在经验复盘系统，不保留独立的历史策略测算中心或旧式评估接口。
- 真实交易券商接入未实现；当前是模拟交易和账本系统。
- 自动止损后台扫描任务未启用；止损字段和判断函数存在，但卖出仍由 AI 或手动订单触发。
- 外部新闻源和数据源的稳定性取决于上游站点、授权、网络和反爬策略。
