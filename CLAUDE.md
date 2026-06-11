# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

天枢智投（Best-AI-Trader）是面向 A 股投研、AI 多智能体决策、模拟交易、长期记忆和经验复盘的研究系统。采用 FastAPI + PostgreSQL/Redis + LiteLLM Proxy + LangGraph + React 18 + MemoFlux 的技术栈。

**核心特点**：不是简单的"LLM 聊天 + 行情查询"，而是工具增强型 Agent、多角色辩论、结构化决策、长期记忆、后验复盘和模拟交易账本的完整 AI 投研系统。

## 技术栈

- **后端**：Python 3.11+, FastAPI, SQLAlchemy, PostgreSQL, Redis, LangGraph, LangChain
- **前端**：React 18, TypeScript, Vite, Ant Design 5, react-i18next
- **AI 引擎**：LiteLLM Proxy (统一 LLM 接入), LangGraph (多 Agent 工作流)
- **记忆系统**：MemoFlux (`memo/` 子目录，独立子项目，通过 HTTP 集成)
- **部署**：Docker Compose 编排所有服务（PostgreSQL, Redis, LiteLLM, Memory, Backend, Frontend, Nginx）

## 常用命令

### 测试
```bash
# 后端默认测试（收集 backend/tests）
pytest

# 后端特定测试
pytest backend/tests/test_api_auth_required.py
pytest backend/tests/test_experience_workflow.py

# MemoFlux 测试
cd memo && pytest tests
```

### 前端质量门禁
```bash
cd frontend
npm run lint          # ESLint (--max-warnings 0，warning 会失败)
npm run typecheck     # TypeScript 类型检查
npm run build         # Vite 构建验证
```

### 部署与调试
```bash
# 生产模式（镜像部署）
docker compose up -d

# 开发模式（源码调试，挂载本地代码）
docker compose -f docker-compose.dev.yml up -d --build

# 配置变更后重建容器（不要只用 restart）
docker compose up -d --force-recreate <service>
```

## 架构核心

### 后端模块结构

- **`backend/app/main.py`**: FastAPI 应用、lifespan 启停、CORS、WebSocket 挂载
- **`backend/app/api/`**: API 路由聚合，默认鉴权边界在 `/api/v1`
- **`backend/app/ai/llm_engine/`**: 单股多 Agent 投研辩论工作流（LangGraph 编排）
  - `orchestrator.py`: 工作流编排
  - `agents/`: 新闻/政策/情绪分析师、多空辩论、PM 决策 Agent
  - `context/`: 六层上下文编译器 (metadata/realtime/snapshot/history/signals/events)
- **`backend/app/ai/stock_picker/`**: AI 智能选股（股票池过滤 + 因子初排 + LLM 深研）
- **`backend/app/ai/experience/`**: 后验经验复盘系统（用真实价格路径校验 PM 决策，写入长期记忆）
- **`backend/app/ai/agentic/`**: Agent 工具体系
  - `tools.py`: 统一工具注册（数据查询、新闻、沙箱、交易、记忆）
  - `tooling/news_plugins/`: 新闻插件体系（Tavily、NewsAPI、外部插件）
  - `skills_loader/`: Skills 加载器（让 Agent 加载专业能力）
  - `tooling/python_sandbox.py`: Deno + Pyodide 沙箱
- **`backend/app/data/`**: A 股数据接入、DataFrame 落库、指标计算、刷新调度
- **`backend/app/trading/`**: 模拟交易服务 + 纯计算交易引擎（FIFO 账本、T+1、费用）
- **`backend/app/portfolio/`**: 组合估值
- **`backend/app/risk_control/`**: 组合风控
- **`backend/app/models/`, `crud/`, `schemas/`**: SQLAlchemy 模型、CRUD、Pydantic 契约
- **`memo/`**: MemoFlux 独立子项目，主后端只通过 HTTP client 集成

### 前端模块结构

- **`frontend/src/App.tsx`**: 路由配置
- **`frontend/src/layouts/DashboardLayout.tsx`**: 主布局
- **`frontend/src/api/`**: API 请求封装（复用 `apiClient`）
- **`frontend/src/services/websocket.ts`**: WebSocket 订阅
- **`frontend/src/utils/apiHistory.ts`**: API 历史与脱敏
- **`frontend/src/features/`**: 功能模块（market, portfolio, debate 等）

## 关键设计原则

### LLM 接入
- 后端通过 **LiteLLM Proxy** 统一接入所有 LLM，使用模型别名
- 真实 provider key、模型名和 base URL 配置在 `litellm/config.yaml`，**不写入代码或提交仓库**
- 公开部署需轮换 `general_settings.master_key` 并同步后端/Memory 使用的 API key

### AI 上下文编译
- Debate 的事实上下文由 `backend/app/ai/llm_engine/context/` 构建（六层上下文模型）
- Agent 不直接绑定数据库表结构拼 prompt，统一通过 Context Adapter 分发

### Agent 分工与工具权限
- **PM（Portfolio Manager）是唯一能下单的 Agent**，普通分析师不能直接交易
- `stock_picker` 只输出推荐、备选和风险摘要，**不构建持仓组合、不执行交易**
- 新闻源通过 `news_plugins` 插件体系接入，**不新增平行新闻工具**

### 交易与持仓账本
- 手动/API 下单和 AI 下单都必须进入 `TradingService` 与 `TradingEngine`
- **`positions.purchase_details.ledger` 是 FIFO/T+1 关键账本**（买入批次、T+1 规则、止损价）
- 组合估值复用 `build_portfolio_valuation`
- 组合风控复用 `portfolio_risk_control_service.evaluate_order`

### 记忆与经验复盘
- 后验评估统一落在 **`experience` 复盘系统**，不另造平行历史评估中心
- 主 backend 只能通过 `backend/app/ai/memory_client.py`/HTTP 集成 MemoFlux
- **不要直接写 MemoFlux 数据库**

### 数据源插件化
- 数据源通过 `data/ingestors/plugins/` 接入，实现 `BaseIngestor` 即可自动发现
- 新闻源通过 `news_plugins` 插件体系接入，一个插件代表一个明确来源

## 修改前必读文档

根据改动范围查阅以下文档（路径相对于仓库根目录）：

- **部署/环境**: `docs/001-deployment.md`
- **Windows WSL2**: `docs/004-windows-wsl-docker-engine-deployment.md`
- **后端能力地图**: `backend/app/README.md`
- **交易链路**: `backend/app/trading/README.md`
- **Debate 工作流**: `backend/app/ai/llm_engine/README.md`
- **AI 选股**: `backend/app/ai/stock_picker/README.md`
- **经验复盘**: `backend/app/ai/experience/README.md`（注意：当前代码允许无新增经验时不写 Memory，以代码和测试为准）
- **Skills Loader**: `backend/app/ai/agentic/skills_loader/README.md`
- **新闻插件**: `backend/app/ai/agentic/tooling/news_plugins/README.md`
- **安全/合规/数据源**: `SECURITY.md`, `LEGAL.md`, `DATA_SOURCES.md`, `CONTRIBUTING.md`
- **MemoFlux**: `memo/README.md`, `memo/docs/`

## 开发约定

### Python 编码规范

- Python 3.11+，遵循 PEP 8，每行最大 120 字符
- 使用绝对导入，分组：标准库/第三方库/本地模块
- 函数保持简短（不超过 100 行），职责单一
- **所有公共函数必须写中文 Google 风格 docstring**（包括 Args, Returns, Raises）
- 使用 `logging`，不使用 `print`；额外上下文通过 `extra={...}` 传递，**禁止拼接进消息正文**
- 错误处理使用明确异常，禁止裸 `except`，抛出有意义错误信息
- 核心逻辑必须编写 pytest 单元测试

### 测试约定

- 后端测试使用 `backend/tests/conftest.py` 中的内存 SQLite、`client`、`db_session`、`auth_headers`
- **新增 SQLAlchemy 表参与测试时，必须更新 `conftest.py` 的 `_sqlite_test_tables()`**
- 单测禁止访问真实 LLM、Redis、Tushare、NewsAPI、Tavily、浏览器等外部服务，使用 mock/fake
- Memory 测试环境设置 `MEMORY_DATABASE_URL=db.invalid`，未 mock 的数据库访问会失败
- **禁止新增 pytest 或单测来约束 prompt 文案**，提示词效果通过人工审计或 live eval 验证
- 前端无 test script，提交前至少运行 `lint`, `typecheck`, `build`

### Git 分支策略

- **禁止直接提交或推送到 `main` 分支**，所有改动必须通过 PR 合并
- 禁止默认使用 `git worktree`，除非用户明确要求
- 禁止默认使用 `git commit --amend`，除非用户明确要求

### 前端约定

- API 请求新增到 `frontend/src/api/*.ts`，复用 `apiClient`
- 认证 token 存储在 `localStorage.token` 和 `useSessionStore`
- WebSocket 先换 ticket，不把 JWT 放入 WS URL
- UI 使用 Ant Design 5 + `theme.useToken()` + CSS variables（无 Tailwind）
- UI 文案使用 `react-i18next` 的 `t(...)`，翻译来源是后端 `/api/v1/general/i18n/{{lng}}`
- 图表复用 `frontend/src/features/market/echartsCore.ts`

### 数据库变更

- 当前项目未上线阶段，数据库结构变更可在 Docker 容器内一次性手动执行
- 非必要不要把一次性修补逻辑写入应用启动代码
- 模型代码保持目标结构，实际数据库由开发者在容器内执行对应 SQL 完成同步

## 高风险边界

- 除 `/health`, `/api/v1/auth/register`, `/api/v1/auth/login`, `/api/v1/general/i18n/{lang}` 外，HTTP 业务路由**默认必须要求登录**
- WebSocket 禁止使用 JWT query token，必须先通过已鉴权 HTTP 换 30 秒一次性 ticket
- `/api/v1/testing/*`, 数据库备份/导入、数据源配置、新闻插件、Skills、运行时依赖安装都属于高风险面，**必须保持鉴权**
- `ENABLE_OPENAPI_DOCS` 默认开启会暴露 `/api/v1/docs`, `/api/v1/redoc`, `/api/v1/openapi.json`；**生产部署必须关闭或加访问控制**
- `ENABLE_RUNTIME_EXTENSIONS` 控制插件/Skill 管理与依赖安装入口
- 根 `nginx.conf` 是实际 Compose 代理配置，公开部署前要收紧 `client_max_body_size`、长超时、请求速率和 LiteLLM `4000` 暴露面
- `backend/scripts/clear_tables.py` 是破坏性脚本，会执行 `TRUNCATE TABLE ... CASCADE`
- **不要提交真实 `.env`, `litellm/config.yaml`, API key, Cookie, JWT, 浏览器 profile, 数据库 dump, prompt/记忆/订单真实数据或供应商原始 payload**

## 验证清单

根据改动范围选择相应验证命令：

- **后端 API/鉴权/路由**: `pytest backend/tests/test_api_auth_required.py` + 相关 endpoint/service 测试
- **后端模型/数据层**: 检查 `_sqlite_test_tables()` + 运行相关 CRUD 测试
- **交易/组合/风控**: 运行 trading, risk_control, portfolio, performance 相关测试
- **LLM/Agentic**: 运行相关 `test_llm_*`, `test_agentic_*`, stock picker 或 experience 测试
- **MemoFlux**: `cd memo && pytest tests`；eval JSON 先用 `python -m json.tool` 校验
- **前端**: `cd frontend && npm run lint && npm run typecheck && npm run build`
- **文档/配置**: 检查引用路径真实存在、确认无模板占位残留、查看 `git diff --check`

## 约束与禁止事项

- 针对记忆系统，**禁止直接使用关键词匹配**，影响泛化性能
- 针对记忆系统，**禁止为了让用例通过而搞定制化修改**
- 针对记忆系统，prompts 的案例要和测试样本不同
- 针对提示词维护，**禁止新增专门的 prompt pytest/单测**，也禁止把 prompt 文案断言塞进既有测试
- **禁止未经用户确认新增兼容函数、兼容分支、fallback 逻辑或自动降级路径**；需要兼容时，先说明原因和影响并获得确认
- `docs/superpowers/` 仅供临时记录，默认不提交；需要长期保留的文档放在 `docs/` 根目录，使用连续编号命名

## 许可与合规

- 本项目采用 **PolyForm Noncommercial License 1.0.0**，仅允许非商业用途
- 禁止任何商业行为，包括商业使用、商业部署、SaaS 化、商业销售、面向商业客户集成
- 禁止更改本仓库许可证
- 所有贡献自动按本仓库许可分发
