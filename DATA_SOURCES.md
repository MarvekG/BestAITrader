# 数据源政策

本仓库提供用于研究工作流的数据适配器和工具，但不授予任何第三方数据权利。启用任何数据源前，部署方必须自行确认该数据源的当前条款、账户权限、API Key 规则、缓存规则、再分发限制和非商业使用边界。

## 通用规则

- 优先使用官方 API 和已授权的访问路径。
- 不要提交 API Key、Cookie、Session Token、请求签名、下载数据集或原始供应商 payload。
- 除非数据源许可明确允许，否则不要再分发行情数据、新闻文本、公告或搜索输出。
- 缓存时长、存储位置和展示范围必须符合数据提供方条款。
- 优先使用链接、元数据和短摘要，避免复制完整文章或完整专有数据 payload。
- 遵守限速、robots 规则、账户限制、付费墙边界和反滥用控制。
- 不要构建或贡献用于绕过鉴权、验证码、付费墙、技术限制、API 配额或合同限制的代码。
- 不要将本仓库用于商业部署。本项目许可证不允许商业使用。

## 数据源清单

| 数据源 | 当前项目用途 | 主要路径 | 部署方必须确认 |
| --- | --- | --- | --- |
| Tushare | 通过用户提供的 token 获取 A 股行情、财务和参考数据。 | `backend/app/data/ingestors/plugins/tushare_ingestor.py`, `backend/app/ai/agentic/skills_loader/skills/tushare-data/` | token 计划是否允许目标非商业研究用途、缓存和再分发。 |
| 历史数据同步源 | 数据同步代码中仍可能包含第三方行情或财务数据探测和字段映射；盯盘模块不再内置固定行情或新闻来源。 | `backend/app/data/ingestors/`, `backend/app/data/storage.py` | 是否允许自动访问、缓存、展示、衍生分析和下游再分发；如无授权应禁用或移除。 |
| 盯盘网页源 | 由部署方配置必填的数据源和新闻源 URL，运行时通过 `browse_web_page_html` 渲染为 Markdown；默认移除 Markdown 中的 URL 目标和裸 URL，可在设置中关闭清理；仓库不内置固定行情源或固定新闻源，也不缓存新闻正文。 | `backend/app/ai/market_watch/web_sources.py`, `backend/app/ai/market_watch/settings.py` | 配置的每个 URL 是否允许自动访问、Markdown 转换、模型输入、实时推送和展示。 |
| NewsAPI | 通过用户提供的必填 API Key 进行新闻搜索。 | `backend/app/ai/agentic/tooling/news_plugins/newsapi.py` | 当前计划限制、生产使用、署名、存储和文章内容处理要求。 |
| Tavily | 通过用户提供的 API Key 进行可选网页/新闻搜索。 | `backend/app/ai/agentic/tooling/news_plugins/tavily.py` | API 条款、可接受使用限制、高风险金融约束和输出处理要求。 |
| LLM 服务商 | 使用用户提供的 Key 生成聊天、记忆和分析结果。 | LiteLLM Proxy via `litellm/config.yaml`, `backend/app/ai/llm_providers/`, `memo/memoflux/llm.py` | 是否可以向服务商发送 prompt、输出、个人数据、账户数据和金融分析内容。 |
| 外部插件和 Skills | 用户安装的数据源插件和脚本。 | `/runtime/news_plugins/external/`, `/runtime/skills/` | 安装前审查代码、依赖、数据权利、网络目标和许可证兼容性。 |

## 原始数据处理

仓库和容器镜像不应包含运行时快照或缓存的供应商输出。测试应优先使用合成夹具。如果测试必须使用类似供应商 payload 的结构，应移除文章正文、个人数据、专有批量行、Cookie、Token 和请求签名。

测试中可以使用：

- 使用虚构值构造的合成数据行；
- 只用于表达 schema 形状的小型手写夹具；
- 为说明集成行为所需的供应商链接和公开标识符。

测试或提交中不得包含：

- 未经明确许可的原始文章正文、完整新闻流或复制公告；
- 批量行情数据导出；
- 包含用户账户元数据、Cookie、Token 或付费计划标识的 API 响应；
- 条款禁止复制或再分发的捕获页面。

## 新增数据源

合并新的数据源适配器前，Pull Request 必须说明：

- 官方文档或条款 URL；
- 访问方式、认证方式和预期限速；
- 数据源是否允许目标非商业研究用途；
- 是否允许缓存、数据库持久化、模型 prompt 和 UI 展示；
- 适配器是否存储原始 payload，还是只存储标准化字段；
- 测试是否使用合成或已获授权的夹具；
- 失败模式不得静默回退到未经授权的数据访问路径。

## 参考链接

- Tushare 数据服务协议: https://tushare.pro/document/1?doc_id=405
- NewsAPI terms: https://newsapi.org/terms
- NewsAPI pricing and production-use notes: https://newsapi.org/pricing
- Tavily terms: https://www.tavily.com/terms
- Tavily acceptable use policy: https://www.tavily.com/acceptable-use-policy

---

# Data Source Policy

This repository provides adapters and tooling for research workflows. It does not grant rights to any third-party
data. Before enabling a source, the operator must confirm the source's current terms, account permissions, API key
rules, caching rules, redistribution limits, and noncommercial-use restrictions.

## General Rules

- Use official APIs and licensed access paths where available.
- Do not commit API keys, cookies, session tokens, request signatures, downloaded datasets, or raw vendor payloads.
- Do not redistribute market data, news text, announcements, or search output unless the source license allows it.
- Keep cache duration, storage location, and display scope within the data provider's terms.
- Prefer links, metadata, and short summaries over copying full articles or full proprietary data payloads.
- Respect rate limits, robots rules, account restrictions, paywall boundaries, and anti-abuse controls.
- Do not build or contribute code whose purpose is to bypass authentication, captchas, paywalls, technical controls,
  API quotas, or contractual restrictions.
- Do not use this repository for commercial deployments. Commercial use is not permitted by the project license.

## Source Inventory

| Source | Current Project Use | Main Paths | Required Operator Check |
| --- | --- | --- | --- |
| Tushare | A-share market, financial, and reference data through a user-provided token. | `backend/app/data/ingestors/plugins/tushare_ingestor.py`, `backend/app/ai/agentic/skills_loader/skills/tushare-data/` | Confirm the token plan permits the intended noncommercial research purpose, caching, and redistribution. |
| Historical data-sync sources | Data ingestion code may still include third-party market or financial data probes and field mappings. Market watch no longer includes fixed market-data or news-source integrations. | `backend/app/data/ingestors/`, `backend/app/data/storage.py` | Confirm permission for automated access, caching, display, derived analysis, and downstream redistribution; disable or remove if unauthorized. |
| Market-watch web sources | Operators configure required data-source and news-source URLs; runtime renders pages through `browse_web_page_html` into Markdown. URL targets and bare URLs are removed from Markdown by default and can be kept through settings. The repository does not include a fixed quote extractor or fixed news-source watcher, and it does not cache news bodies. | `backend/app/ai/market_watch/web_sources.py`, `backend/app/ai/market_watch/settings.py` | Confirm each configured URL permits automated access, Markdown conversion, model input, live push, and display. |
| NewsAPI | News search provider through a required user-provided API key. | `backend/app/ai/agentic/tooling/news_plugins/newsapi.py` | Confirm current plan limits, production use, attribution, storage, and article-content handling. |
| Tavily | Optional web/news search provider through a user-provided API key. | `backend/app/ai/agentic/tooling/news_plugins/tavily.py` | Confirm API terms, acceptable-use limits, high-risk finance constraints, and output handling. |
| LLM providers | Chat/completion, memory, and analysis generation through user-provided keys. | LiteLLM Proxy via `litellm/config.yaml`, `backend/app/ai/llm_providers/`, `memo/memoflux/llm.py` | Confirm whether prompts, outputs, personal data, account data, and financial analysis may be sent to the provider. |
| External plugins and skills | User-installed source plugins and scripts. | `/runtime/news_plugins/external/`, `/runtime/skills/` | Review code, dependencies, source rights, network targets, and license compatibility before installing. |

## Handling Raw Data

The repository and container images should not include runtime snapshots or cached provider output. Synthetic fixtures
are preferred for tests. If a test must use a provider-shaped payload, remove source-specific article bodies, personal
data, proprietary bulk rows, cookies, tokens, and request signatures.

Allowed in tests:

- synthetic rows with invented values;
- small hand-written fixtures that only model schema shape;
- provider links and public identifiers when needed to document integration behavior.

Not allowed in tests or commits:

- raw article bodies, full news feeds, or copied announcements unless explicitly licensed;
- bulk market-data exports;
- API responses containing user account metadata, cookies, tokens, or paid-plan identifiers;
- captured pages from services whose terms forbid reproduction or redistribution.

## Adding A New Data Source

Before merging a new source adapter, include the following in the pull request:

- the official documentation or terms URL;
- the access method, authentication method, and expected rate limits;
- whether the source allows the intended noncommercial research use;
- whether caching, database persistence, model prompting, and UI display are allowed;
- whether the adapter stores raw payloads or only normalized fields;
- tests using synthetic or rights-cleared fixtures;
- a failure mode that does not silently fall back to unlicensed data-access paths.

## Reference Links

- Tushare data service agreement: https://tushare.pro/document/1?doc_id=405
- NewsAPI terms: https://newsapi.org/terms
- NewsAPI pricing and production-use notes: https://newsapi.org/pricing
- Tavily terms: https://www.tavily.com/terms
- Tavily acceptable use policy: https://www.tavily.com/acceptable-use-policy
