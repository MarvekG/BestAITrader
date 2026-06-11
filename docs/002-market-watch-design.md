# 盯盘模块设计

盯盘模块持续读取用户股票仓库、持仓和账户摘要，并按用户配置的网页来源生成数据源与新闻源 Markdown。仓库不再内置固定行情源或固定新闻源，也不再缓存新闻正文。

## 目标

- 数据源 URL 和新闻源 URL 由部署方或用户显式配置，保存设置时两类 URL 都必须至少有一项。
- 每轮扫描通过仓库内现有的 `browse_web_page_html` 渲染网页，并请求 `content_format="markdown"`。
- 生成的 Markdown 文档默认移除 URL 目标和裸 URL 后进入本轮盯盘 AI 输入；`clean_source_markdown=false` 时保留原始 Markdown。
- 新闻源 Markdown 不写入 Redis 列表或业务数据库；本轮生成后只通过 WebSocket 直接推送给当前用户。
- 盯盘 AI 只能基于输入中的仓库股票、持仓、账户摘要、结构化 quote（如有）和 Markdown 文档判断是否启动深度分析。

## 后端路径

- `backend/app/ai/market_watch/schemas.py`：盯盘设置、URL 校验、Markdown 清理和 Markdown 文档 schema。
- `backend/app/ai/market_watch/settings.py`：按用户持久化 `data_source_urls`、`news_source_urls` 与 `clean_source_markdown`。
- `backend/app/ai/market_watch/web_sources.py`：调用 `browse_web_page_html` 获取 Markdown 文档，并按设置清理 URL。
- `backend/app/ai/market_watch/service.py`：组装扫描上下文、发布 Markdown 文档、调用 Watch AI、记录审计事件。
- `backend/app/websocket/manager.py`：转发 `market_watch_documents` 和 `market_watch_event`。

## 扫描流程

1. 读取用户盯盘设置和时间窗口。
2. 若数据源 URL 或新闻源 URL 缺失，则跳过本轮扫描并返回 `missing_source_urls`。
3. 加载仓库股票、持仓和账户摘要。
4. 对 `data_source_urls` 逐个调用 `browse_web_page_html`，生成 `data_documents`。
5. 对 `news_source_urls` 逐个调用 `browse_web_page_html`，生成 `news_documents`。
6. 将本轮生成的所有 Markdown 文档通过 `market_watch_documents` 推送给当前用户。
7. 若存在结构化 quote、数据源 Markdown 或新闻源 Markdown，则调用 Watch AI。
8. 根据 Watch AI 结果执行冷却、去重和自动辩论启动逻辑。

## 合规边界

- 仓库不包含固定的专有行情源或固定新闻源逻辑。
- 默认设置中的数据源和新闻源为空；保存运行时设置和执行有效扫描前必须配置两类 URL。部署方必须自行确认配置 URL 的条款、robots、鉴权、限速、缓存、展示和模型输入权限。
- 新闻正文不缓存；Redis 只用于转发本轮 WebSocket 事件。
- 测试使用合成 Markdown，不提交真实供应商 payload、文章正文、Cookie、Token 或请求签名。

---

# Market Watch Module Design

The market watch module reads a user's stock warehouse, positions, and account summary, then renders user-configured data and news web pages into Markdown. The repository no longer ships a fixed quote source or fixed news source, and it no longer caches news bodies.

## Goals

- Data-source URLs and news-source URLs are explicitly configured by the operator or user; saving settings requires at least one URL in each group.
- Each scan renders pages through the existing `browse_web_page_html` tool with `content_format="markdown"`.
- Rendered Markdown removes link targets and bare URLs by default before reaching the Watch AI payload; `clean_source_markdown=false` keeps the original Markdown.
- News-source Markdown is not written to Redis lists or business tables; the current source documents are pushed directly to the current user over WebSocket.
- Watch AI can only use the supplied stock warehouse, positions, account summary, optional structured quote data, and Markdown documents.

## Backend Paths

- `backend/app/ai/market_watch/schemas.py`: settings, URL validation, Markdown cleaning, and Markdown document schema.
- `backend/app/ai/market_watch/settings.py`: per-user `data_source_urls`, `news_source_urls`, and `clean_source_markdown` persistence.
- `backend/app/ai/market_watch/web_sources.py`: `browse_web_page_html` integration for Markdown documents, with URL cleaning controlled by settings.
- `backend/app/ai/market_watch/service.py`: scan orchestration, document publishing, Watch AI invocation, and audit events.
- `backend/app/websocket/manager.py`: forwards `market_watch_documents` and `market_watch_event`.

## Scan Flow

1. Load the user's settings and scan window.
2. If either URL group is missing, skip the scan with `missing_source_urls`.
3. Load warehouse stocks, positions, and account summary.
4. Render each `data_source_urls` entry into `data_documents`.
5. Render each `news_source_urls` entry into `news_documents`.
6. Push all current Markdown documents to the current user as `market_watch_documents`.
7. Invoke Watch AI when structured quote data, data Markdown, or news Markdown is present.
8. Apply cooldown, deduplication, and automatic debate launch rules to Watch AI decisions.

## Compliance Boundary

- The repository does not include fixed proprietary market-data or fixed news-source logic.
- Default data and news sources are empty; saving runtime settings and running a valid scan require both URL groups. Operators must verify terms, robots rules, authentication, rate limits, caching, display, and model-input permissions for every configured URL.
- News bodies are not cached; Redis is only used to forward current WebSocket events.
- Tests use synthetic Markdown and must not commit real vendor payloads, article bodies, cookies, tokens, or request signatures.
