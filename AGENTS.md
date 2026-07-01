# Best-AI-Trader 代理规则

## 基本原则
- 先查阅现有代码、文档和接口，再做判断；不暗猜、不盲改。
- 优先复用已有模块和约定，避免新增平行入口、兼容分支或无必要抽象。
- 涉及业务含义、公开接口、旧数据兼容或数据库变更时，先向用户确认。
- 修改后运行与改动范围最接近的验证；无法验证时说明原因。

## 项目概况
- 天枢智投是面向 A 股投研、AI 多智能体决策、模拟交易、长期记忆和经验复盘的研究系统。
- 主栈：FastAPI、SQLAlchemy、PostgreSQL、Redis、LiteLLM Proxy、LangGraph/LangChain、React 18、Vite、TypeScript、Ant Design、MemoFlux。
- 完整部署使用根 `docker-compose.yml`；源码调试使用 `docker-compose.dev.yml`。

## 关键边界
- 后端 LLM 接入固定走 LiteLLM Proxy；真实 key、模型名、base URL 不写入代码或提交仓库。
- 主 backend 只能通过 `backend/app/ai/memory_client.py` 或 HTTP 集成 MemoFlux，不直接写 MemoFlux 数据库。
- 手动/API/AI 下单都必须进入 `TradingService` 与 `TradingEngine`；普通分析师 Agent 不直接下单。
- `stock_picker` 只输出推荐、备选和风险摘要，不构建持仓组合、不执行交易。
- 新闻源走 `news_plugins` 插件体系；数据源走 ingestor 插件和 `DataIngestionService.write_dataframe()`。
- HTTP 业务路由默认要求登录；新增公开端点必须同步更新鉴权测试白名单。
- WebSocket 不把 JWT 放入 URL，必须先通过已鉴权 HTTP 换一次性 ticket。
- 不提交真实 `.env`、`litellm/config.yaml`、API key、Cookie、JWT、数据库 dump、真实订单或账户数据。

## 常用命令
- 后端定向测试：`PYTHONPATH=backend pytest backend/tests/test_api_auth_required.py`。
- MemoFlux 测试：在 `memo/` 下运行 `pytest tests`。
- 前端质量门禁：`cd frontend && npm run lint && npm run typecheck && npm run build`。
- 开发环境启动：`docker compose -f docker-compose.dev.yml up -d`。
- 配置变更后重建服务：`docker compose -f docker-compose.dev.yml up -d --force-recreate <service>`。

## 编码约定
- Python 使用 3.11+、PEP 8、绝对导入、`logging`，不使用 `print`、裸 `except`、`eval`、`exec`。
- 新增或修改公共 Python 函数时补中文 Google 风格 docstring。
- 前端 API 放入 `frontend/src/api/*.ts` 并复用 `apiClient`；UI 优先使用 Ant Design 5 和现有组件。
- 单测禁止访问真实 LLM、Redis、Tushare、NewsAPI、Tavily、浏览器外部服务。

## Git 与文档
- 不主动创建独立 `git worktree`，不使用破坏性 git 命令，不 amend，除非用户明确要求。
- 禁止在 `main` 分支上直接提交或推送；需要提交时先建语义化功能分支。
