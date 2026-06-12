# Best-AI-Trader 项目上下文

## 项目定位
- 天枢智投是面向 A 股投研、AI 多智能体决策、模拟交易、长期记忆和经验复盘的研究系统。
- 主栈：FastAPI + SQLAlchemy + PostgreSQL/Redis + LiteLLM Proxy + LangGraph/LangChain + React 18/Vite/TypeScript/Ant Design + MemoFlux。
- 完整部署由根 `docker-compose.yml` 编排 PostgreSQL、Redis、LiteLLM、MemoFlux、Sandbox、WebFetch、Scrapling MCP、Backend、Frontend、Nginx；源码调试使用 `docker-compose.dev.yml`。

## 关键目录
- `backend/app/main.py`：FastAPI 应用、lifespan 启停副作用、HTTP access log、CORS、WebSocket 挂载。
- `backend/app/api/__init__.py`：`/api/v1` 路由聚合与默认鉴权边界。
- `backend/app/ai/llm_engine/`：单股多 Agent 投研辩论工作流。
- `backend/app/ai/stock_picker/`：股票池、因子初排、整池 LLM 深研和推荐生成；不下单。
- `backend/app/ai/experience/`：已有 PM 决策的后验复盘、事件流和记忆写入。
- `backend/app/ai/agentic/`：Agent 工具、新闻插件、Skills Loader、Python 沙箱、浏览器、PDF、Memory 工具。
- `backend/app/data/`：A 股数据接入、DataFrame 落库、指标、刷新调度。
- `backend/app/models/`、`backend/app/crud/`、`backend/app/schemas/`：SQLAlchemy 模型、CRUD、Pydantic API 契约。
- `backend/app/trading/`：模拟交易服务与纯计算交易引擎。
- `backend/app/portfolio/`、`backend/app/risk_control/`、`backend/app/performance/`：组合估值、组合风控、绩效快照。
- `frontend/src/App.tsx`、`frontend/src/layouts/DashboardLayout.tsx`：前端路由与主布局。
- `frontend/src/api/`、`frontend/src/services/websocket.ts`、`frontend/src/utils/apiHistory.ts`：前端 API、WebSocket、任务历史。
- `memo/`：MemoFlux 独立子项目；主后端只通过 HTTP client 集成。
- `docs/`：正式编号设计/部署文档；`docs/superpowers/` 只作为临时工作流记录。

## 常用命令
- 后端默认测试：`pytest`（根 `pytest.ini` 默认只收集 `backend/tests`）。
- 后端定向测试：`pytest backend/tests/test_api_auth_required.py`。
- MemoFlux 测试：在 `memo/` 下运行 `pytest tests`。
- 前端质量门禁：`cd frontend && npm run lint && npm run typecheck && npm run build`。
- 本地镜像部署：`docker compose up -d`。
- 本地源码调试：`docker compose -f docker-compose.dev.yml up -d --build`。
- 配置变更后重建容器：`docker compose up -d --force-recreate <service>`，不要只用 `restart`。

## 修改前先读
- 部署/环境：`docs/002-deployment.md`，Windows WSL2 读 `docs/004-windows-wsl-docker-engine-deployment.md`。
- 后端能力地图：`backend/app/README.md`。
- 交易链路：`backend/app/trading/README.md`。
- Debate 工作流：`backend/app/ai/llm_engine/README.md`。
- AI 选股：`backend/app/ai/stock_picker/README.md`。
- 经验复盘：`backend/app/ai/experience/README.md`；当前允许无新增可复用经验时不写 Memory，以 `backend/app/ai/experience/workflow.py` 和 `backend/tests/test_experience_workflow.py` 为准。
- Skills Loader：`backend/app/ai/agentic/skills_loader/README.md`。
- 新闻插件：`backend/app/ai/agentic/tooling/news_plugins/README.md`。
- 安全/合规/数据源：`SECURITY.md`、`LEGAL.md`、`DATA_SOURCES.md`、`CONTRIBUTING.md`。
- MemoFlux 变更：`memo/README.md`、`memo/docs/`。

## 常见任务路径
- 新增后端 API：先读 `backend/app/api/__init__.py`、相关 `schemas`/`models`/`crud`，默认加 `get_current_user`，补对应 `backend/tests`。
- 新增异步任务或调度：复用 `TaskManager`、`AsyncTaskRunner`、`AsyncTaskScheduler`，显式处理 `user_id`、状态和 Redis 通知。
- 新增前端页面：改 `frontend/src/App.tsx` 路由，页面放 `frontend/src/pages` 或既有 feature 下，API 放 `frontend/src/api/*.ts`。
- 改 LLM/Agent：先核对 LiteLLM 别名、Agent 工具边界、结构化输出 schema、usage 记录和相关 `test_llm_*`/agentic 测试。
- 新增新闻源：按 `backend/app/ai/agentic/tooling/news_plugins/README.md` 做插件，不新增平行工具。
- 新增数据源：按 `backend/app/data/ingestors/plugins/README.md` 做 ingestor，写入走 `DataIngestionService.write_dataframe()`。
- 改交易/组合/风控：先读 `backend/app/trading/README.md`，保持 API/Service/Engine 分层和风控预检。
- 改 Memory/经验复盘：先读 `backend/app/ai/experience/DESIGN.md` 和 `backend/app/ai/experience/MEMORY_PROTOCOL_PROMPT_DESIGN.md`，语义判断走 LLM schema/prompt/eval，不写关键词规则。

## 架构约定
- 后端 LLM 接入固定走 LiteLLM Proxy 与模型别名，真实 provider key、模型名和 base URL 放在本地 `litellm/config.yaml`，不要写入后端代码或提交仓库；公开或多人环境还必须轮换 `general_settings.master_key` 并同步后端/Memory 使用的 LiteLLM API key。
- Debate 的事实上下文由 `backend/app/ai/llm_engine/context/` 构建；Agent 不应直接绑定数据库表结构拼 prompt。
- PM 是唯一追加交易工具的 Agent；普通分析师不应直接下单。
- `stock_picker` 只输出推荐、备选和风险摘要，不构建持仓组合、不执行交易。
- 后验评估统一落在 `experience` 复盘系统，不要另造平行历史评估中心。
- 手动/API 下单和 AI 下单都必须进入 `TradingService` 与 `TradingEngine`；`positions.purchase_details.ledger` 是 FIFO/T+1 关键账本。
- 组合估值复用 `build_portfolio_valuation`；组合风控复用 `portfolio_risk_control_service.evaluate_order`。
- 新闻源通过 `news_plugins` 插件体系接入，一个插件代表一个明确来源；不要新增平行新闻工具。
- Agent 使用 Skills Loader 时必须先 `load_skill` 再按需读 references/scripts；不能只看 catalog 猜用法。
- 主 backend 只能通过 `backend/app/ai/memory_client.py`/HTTP 集成 MemoFlux；不要直接写 MemoFlux 数据库。

## 复用入口
- 通用重试、日期、股票代码、JSON 安全序列化：`backend/app/core/utils/`。
- 结构化日志和敏感字段脱敏：`backend/app/core/logger.py`。
- 后端 i18n 文案加载与格式化：`backend/app/core/i18n.py`。
- 后端 `.env` 读写：`backend/app/core/env_manager.py`。
- 前端错误格式化：`frontend/src/utils/errorUtils.ts`。
- 前端 API 历史与脱敏：`frontend/src/utils/apiHistory.ts`。
- 前端 WebSocket 订阅：`frontend/src/services/websocket.ts`。

## 测试与夹具约定
- 后端测试默认使用 `backend/tests/conftest.py` 中的内存 SQLite、`client`、`db_session`、`auth_headers`。
- 新增后端 SQLAlchemy 表并参与 API/DB 测试时，必须检查并更新 `_sqlite_test_tables()`。
- 单测禁止访问真实 LLM、Redis、Tushare、NewsAPI、Tavily、浏览器外部服务；使用现有 mock/fake/monkeypatch 模式。
- Memory 测试环境设置 `MEMORY_DATABASE_URL=db.invalid`，真实数据库访问若未 mock 会失败。
- 提示词变更禁止新增 pytest 或其他单元测试来约束 prompt 文案；也不要在既有单测中新增针对 prompt 文案的字符串断言。提示词效果通过人工审计、既有评测脚本或用户明确要求的 live eval 验证。
- 前端当前没有 test script；提交前至少运行 lint、typecheck、build，且 ESLint warning 会因 `--max-warnings 0` 失败。

## 高风险边界
- 除 `/health`、`/api/v1/auth/register`、`/api/v1/auth/login`、`/api/v1/general/i18n/{lang}` 外，HTTP 业务路由默认必须要求登录；新增公开端点需同步测试白名单。
- WebSocket 禁止使用 JWT query token；必须先通过已鉴权 HTTP 换 30 秒一次性 ticket。
- `/api/v1/testing/*`、数据库备份/导入、数据源配置、新闻插件、Skills、运行时依赖安装都属于高风险面，必须保持鉴权。
- `ENABLE_OPENAPI_DOCS` 默认开启会暴露 `/api/v1/docs`、`/api/v1/redoc`、`/api/v1/openapi.json`；生产或公开部署必须关闭或加访问控制。
- `ENABLE_RUNTIME_EXTENSIONS` 控制插件/Skill 管理与依赖安装入口，不等价于停止已安装 external 插件被 registry 扫描。
- `ENABLE_MAINTENANCE_ENDPOINTS` 只控制数据库 backup/import，不关闭 testing、sources、plugins 或 skills。
- 根 `nginx.conf` 是实际 Compose 代理配置；`frontend/nginx.conf` 当前不是根 Compose 使用的入口。公开部署前要收紧根 Nginx 的 `client_max_body_size`、长超时、请求速率和 LiteLLM `4000` 暴露面。
- `backend/app/ai/agentic/skills_loader/skills/` 与 `backend/app/ai/agentic/tooling/news_plugins/external/` 可能包含运行时上传或独立外部仓库内容，提交前必须核验 tracked 状态和敏感信息。
- `backend/scripts/clear_tables.py` 是破坏性脚本，会对 PostgreSQL 执行 `TRUNCATE TABLE ... CASCADE`。
- 不要提交真实 `.env`、`litellm/config.yaml`、API key、Cookie、JWT、浏览器 profile、数据库 dump、prompt/记忆/订单/账户真实数据或供应商原始 payload。
- 常见本地产物和生成物不要提交：`node_modules/`、`dist/`、`coverage/`、`.pytest_cache/`、`__pycache__/`、`logs/`、`backend/runtime/snapshots/`、`memo/evals/reports/`、`pyodide-*.tar.bz2`、`reload-trigger.json`。

## 前端约定
- API 请求优先新增到 `frontend/src/api/*.ts` 并复用 `apiClient`；调用方按业务体处理返回值，不再取 `.data`。
- 认证 token 同时涉及 `localStorage.token` 和 `useSessionStore`；WebSocket 先换 ticket，不把 JWT 放入 WS URL。
- UI 默认使用 Ant Design 5、`theme.useToken()`、CSS variables 和现有组件；项目没有 Tailwind 配置。
- UI 文案优先用 `react-i18next` 的 `t(...)`；翻译来源是后端 `/api/v1/general/i18n/{{lng}}`。
- 图表优先复用 `frontend/src/features/market/echartsCore.ts`。
- `frontend/src/shared/*` 和 `frontend/src/components/ui` 当前基本为空；新增共享层前先查 `frontend/src/api/`、`frontend/src/utils/`、`frontend/src/services/`、`frontend/src/theme/`、`frontend/src/components/`、`frontend/src/features/`、`frontend/src/pages/` 是否已有入口。

## 完成修改后的验证清单
- 先运行与改动范围最近的测试或构建命令，不用无关大套件替代近邻验证。
- 后端 API/鉴权/路由：至少运行 `pytest backend/tests/test_api_auth_required.py`，再加相关 endpoint/service 测试。
- 后端模型/数据层：检查 `_sqlite_test_tables()`，运行相关数据库、ingestor 或 CRUD 测试。
- 交易/组合/风控：运行 trading、risk_control、portfolio、performance 相关测试。
- LLM/Agentic：运行相关 `test_llm_*`、`test_agentic_*`、stock picker 或 experience 测试。
- MemoFlux：在 `memo/` 下运行 `pytest tests` 或更窄的 MemoFlux 测试；eval JSON 先用 `python -m json.tool` 校验。
- 前端：运行 `cd frontend && npm run lint && npm run typecheck && npm run build`。
- 文档/配置：检查引用路径真实存在、确认没有模板占位残留，并查看 `git diff --check`。

# Python 编码规范

## 语言
- 使用 Python 3.11 及以上版本

## 代码风格
- 遵循 PEP 8 规范
- 变量和函数使用 snake_case
- 类名使用 PascalCase
- 每行最大长度 120 字符

## 导入规范
- 使用绝对导入（absolute import）
- 导入分组：标准库 / 第三方库 / 本地模块
- 禁止未使用的导入

## 函数规范
- 函数应保持简短、职责单一
- 单个函数不超过 100 行
- 使用清晰、描述性的命名
- 所有公共函数必须写文档字符串（docstring）
- 新增或修改的函数都必须补充中文 Google 风格文档字符串（docstring），包括必要的 Args、Returns、Raises

## 文档字符串（Docstring）
- 使用中文 Google 风格
- 描述必须说明函数意图，不要只复述函数名
- 存在入参时必须写 Args，存在返回值时必须写 Returns，可能主动抛出异常时必须写 Raises

示例：
def add(a: int, b: int) -> int:
    """
    两个整数相加

    Args:
        a: 第一个整数
        b: 第二个整数

    Returns:
        两数之和
    """
    return a + b

## 错误处理
- 使用明确的异常处理（try/except）
- 禁止使用裸 except
- 抛出有意义的错误信息

## 日志
- 使用 logging，不使用 print
- 默认日志级别为 INFO
- 新增或修改日志时，额外上下文字段必须通过 `extra={...}` 传递，禁止把字段和值拼接进日志消息正文。

## 测试
- 使用 pytest
- 核心逻辑必须编写单元测试

## 性能
- 在保证可读性的前提下使用列表/字典推导式
- 避免过早优化

## 约束
- 非必要不使用全局变量
- 禁止硬编码密钥或敏感信息
- 避免超过 5 层嵌套逻辑
- 针对记忆系统，禁止直接使用关键词匹配，影响记忆系统的泛化性能。
- 针对记忆系统，禁止为了让用例通过而搞定制化修改，影响记忆系统的泛化性能
- 针对记忆系统，prompts的案例要和测试样本不同，避免影响记忆系统的泛化性能。
- 针对提示词维护，禁止新增专门的 prompt pytest/单测，也禁止把 prompt 文案断言塞进既有测试；不要用测试反向锁死提示词表达，优先保持 prompt 简洁和可审计。
- 禁止未经用户确认新增兼容函数、兼容分支、fallback 逻辑或自动降级路径；确需兼容旧配置、旧数据、旧接口或异常降级时，必须先说明原因和影响并获得用户明确确认。

## 项目结构
- 模块划分清晰
- 业务逻辑与 I/O 分离
- `docs/superpowers/` 仅供 Superpowers 工作流临时记录使用，默认不要提交该目录下文件；需要长期保留的正式设计或实施文档应放在 `docs/` 根目录，并使用连续编号命名。

## 优先级
- 可读性 > 可维护性 > 性能

## 异步
- I/O 密集任务优先使用 async/await

## 依赖管理
- 优先使用标准库
- 尽量减少第三方依赖

## 安全
- 所有外部输入必须校验
- 禁止使用 eval/exec

## Git 分支策略
- 禁止在 `main` 分支上直接提交或推送；所有改动必须从 `main` 拉取新分支，提交后推送到功能分支并通过 PR 合并。
- 禁止默认创建或使用独立 `git worktree`。除非用户明确要求使用 worktree，否则所有改动都应在当前工作区完成，并严格保护已有未提交改动。
- 禁止默认使用 `git commit --amend` 修改既有提交；除非用户明确要求 amend，否则后续修改必须创建新的普通提交。

## 数据库变更
- 当前项目未上线阶段，数据库结构变更可在 Docker 容器内一次性手动执行。
- 非必要不要把一次性表结构修补逻辑写入应用启动代码；模型代码保持目标结构，实际数据库由开发者在容器内执行对应 SQL 完成同步。
- 执行数据库变更前先确认目标容器、数据库名和 schema，避免误改非目标环境。

## Superpowers使用步骤
Superpowers 的核心就是这 7 个步骤，按顺序执行：
1. brainstorming（头脑风暴）
2. using-git-worktrees（仅在用户明确要求时创建独立工作区；默认禁止）
3. writing-plans（写实施计划）
4. subagent-driven-development（子代理开发）
5. test-driven-development（测试驱动开发）
6. requesting-code-review（代码审查）
7. finishing-a-development-branch（完成分支）
