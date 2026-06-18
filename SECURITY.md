# 安全策略

天枢智投（Best-AI-Trader）优先面向私人研究和模拟交易场景。不要在未加固的情况下把默认部署暴露到公网。

## 支持的使用方式

安全默认场景是私有、已鉴权部署。公网、多人或真实资金交易相关部署需要额外的安全、隐私、法律和运维审查。

## 高风险面

| 风险面 | 风险 | 生产控制要求 |
| --- | --- | --- |
| `/api/v1/testing/*` | 诊断接口可能触发外部调用、schema 检查、记忆预览和工具执行探测。 | 始终注册；默认仍要求登录。 |
| `/api/v1/sources/*` | 可读取数据源配置并更新 API token。 | 默认要求登录；生产环境应脱敏密钥并限制运维访问面。 |
| `/api/v1/news-plugins/*` | 允许上传和注册 Python 新闻插件。 | 不需要安装或管理插件时设置 `ENABLE_RUNTIME_EXTENSIONS=false`。 |
| `/api/v1/skills/*` | 允许上传 skill 文件夹并暴露 prompt/catalog。 | 不需要安装或管理 Skill 时设置 `ENABLE_RUNTIME_EXTENSIONS=false`。 |
| 运行时依赖安装 | 可向后端环境安装插件/Skill 声明的 Python 包。 | 由 `ENABLE_RUNTIME_EXTENSIONS` 控制，安装完成后建议关闭。 |
| 浏览器自动化 | 可访问外部页面并缓存渲染内容。 | 限制目标、速率和供应商许可范围。 |
| 模拟订单执行 | 可修改账户、持仓、订单和成交记录。 | 要求用户鉴权并校验账户归属。 |
| 记忆服务 | 存储 prompt、决策、经验以及可能的个人或金融上下文。 | 应用保留、删除、访问控制和服务商审查策略。 |

## 生产加固清单

- 替换所有默认密钥，包括 `SECRET_KEY`、管理员密码、数据库密码、Redis 凭证和服务商 API Key。
- `ENABLE_AUTO_TRADE` 只控制内部模拟交易账本写入，不连接真实券商交易通道；通常可以保持开启。
  如果部署方只希望生成分析报告、不希望 AI 写入模拟订单和持仓记录，可以设置 `ENABLE_AUTO_TRADE=false`。
- 安装新的 Skill 或新闻插件前，将 `ENABLE_RUNTIME_EXTENSIONS=true` 写入 `.env` 并重启后端；
  安装完成后建议设置 `ENABLE_RUNTIME_EXTENSIONS=false` 并再次重启后端。
- 所有管理、诊断、上传和配置接口默认都必须保持登录态要求。
- 限制 CORS origins，不要使用 `allow_origins=["*"]`。
- 使用 TLS 和反向代理，并配置请求体大小、请求速率和超时限制。
- PostgreSQL、Redis 和 memory 服务应保持在私有网络内。
- 除非有访问控制，不要公开 FastAPI docs、OpenAPI schema 或 debug 接口。
- 在日志中脱敏 token 和供应商响应。
- 为 session、订单、prompt、记忆记录、日志和运行时快照定义保留与删除策略。
- 向第三方服务商发送 prompt、账户数据、个人数据或金融上下文前，先审查服务条款。
- 确保运行时快照和缓存的供应商输出不会进入 git 或容器镜像。

## 密钥

密钥必须通过环境文件、密钥管理系统或部署工具提供。不要提交：

- `.env` 文件；
- API Key 或服务商 token；
- Cookie、session、浏览器 profile 或请求签名；
- 包含真实用户、账户、订单、prompt、记忆或供应商数据的数据库 dump。

如果密钥被提交或暴露，应立即轮换，并在发布分支前从历史记录中移除。

## 漏洞报告

目前请通过项目维护者偏好的私有联系渠道报告安全问题。报告应包含：

- 受影响的 commit 或版本；
- 受影响的接口、文件或配置；
- 复现步骤；
- 预期影响；
- 是否暴露密钥、个人数据或第三方供应商数据。

除非维护者另行宣布，否则本项目没有公开漏洞赏金计划。

## 事件响应

如果某个部署可能暴露了密钥、用户数据、供应商数据或交易记录：

1. 移除受影响服务的公网访问。
2. 轮换受影响凭证并吊销泄露的 API Key。
3. 保留调查所需日志，同时限制进一步访问。
4. 确认受影响用户、数据类别、服务商和时间范围。
5. 按适用法律、服务商条款和合同要求完成通知。

---

# Security Policy

Best-AI-Trader is designed first for private research and simulated trading. Do not expose a default deployment to
the public internet without hardening it.

## Supported Use

The safe default use case is a private, authenticated research deployment. Public, multi-tenant,
or real-money trading deployments require additional security, privacy, legal, and operational review.

## High-Risk Surfaces

| Surface | Risk | Required Production Control |
| --- | --- | --- |
| `/api/v1/testing/*` | Diagnostics can trigger external calls, schema inspection, memory preview, and tool execution probes. | Always registered; authentication is still required by default. |
| `/api/v1/sources/*` | Can expose data-source configuration and update API tokens. | Authentication is required by default; redact secrets and limit operational access in production. |
| `/api/v1/news-plugins/*` | Allows Python plugin upload and registration. | Set `ENABLE_RUNTIME_EXTENSIONS=false` when plugin management is not needed. |
| `/api/v1/skills/*` | Allows skill folder upload and prompt/catalog exposure. | Set `ENABLE_RUNTIME_EXTENSIONS=false` when Skill management is not needed. |
| Runtime dependency install | Can install Python packages declared by plugins or Skills into the backend environment. | Controlled by `ENABLE_RUNTIME_EXTENSIONS`; disable it after installation. |
| Browser automation | Can access external pages and cache rendered content. | Restrict targets, rate limits, and provider permissions. |
| Simulated order execution | Can mutate accounts, positions, orders, and trade records. | Require user authentication and account ownership checks. |
| Memory service | Stores prompts, decisions, experiences, and potentially personal or financial context. | Apply retention, deletion, access control, and provider-review policies. |

## Production Hardening Checklist

- Replace all default secrets, including `SECRET_KEY`, admin password, database passwords, Redis credentials, and
  provider API keys.
- `ENABLE_AUTO_TRADE` only controls writes to the internal simulated trading ledger. It does not connect to a
  real brokerage trading channel, so it can usually stay enabled. Set it to `false` only when the deployment
  should generate analysis reports without writing simulated orders or positions.
- Before installing new Skills or news plugins, set `ENABLE_RUNTIME_EXTENSIONS=true` in `.env` and restart the backend.
  After installation, set `ENABLE_RUNTIME_EXTENSIONS=false` and restart the backend again.
- Keep authentication required for all management, diagnostic, upload, and configuration endpoints.
- Restrict CORS origins instead of using `allow_origins=["*"]`.
- Run behind TLS and a reverse proxy that enforces body-size, request-rate, and timeout limits.
- Keep PostgreSQL, Redis, and memory services on a private network.
- Do not expose FastAPI docs, OpenAPI schema, or debug endpoints publicly unless access-controlled.
- Redact tokens and provider responses from logs.
- Define retention and deletion policies for sessions, orders, prompts, memory records, logs, and runtime snapshots.
- Review third-party provider terms before sending prompts, account data, personal data, or financial context.
- Keep runtime snapshots and cached provider output out of git and container images.

## Secrets

Secrets must be provided through environment files, secret stores, or deployment tooling. Never commit:

- `.env` files;
- API keys or provider tokens;
- cookies, sessions, browser profiles, or request signatures;
- database dumps containing real user, account, order, prompt, memory, or provider data.

If a secret is committed or exposed, rotate it immediately and remove it from history before publishing the branch.

## Vulnerability Reports

For now, report security issues privately to the repository maintainer through the project owner's preferred private
contact channel. Include:

- affected commit or version;
- affected endpoint, file, or configuration;
- reproduction steps;
- expected impact;
- whether any secret, personal data, or third-party provider data was exposed.

There is no public bug bounty program unless one is announced by the maintainer.

## Incident Response

If a deployment may have exposed secrets, user data, provider data, or trading records:

1. Remove public access to the affected service.
2. Rotate affected credentials and revoke compromised API keys.
3. Preserve logs needed for investigation while limiting further access.
4. Identify affected users, data categories, providers, and time ranges.
5. Follow applicable legal, provider, and contractual notification requirements.
