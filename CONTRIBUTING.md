# 贡献指南

感谢你为天枢智投（Best-AI-Trader）做贡献。本项目采用 MIT License，并涉及金融工作流和第三方数据。

## 贡献许可

提交 Pull Request、补丁或其他贡献，即表示你确认自己有权贡献这些内容，并同意贡献内容按本仓库的 MIT License 分发。

如果你单独向 `memo/` 子模块贡献，请遵循 MemoFlux 项目的上游贡献流程。

## 不要提交的内容

不要提交：

- API Key、Cookie、Token、密码、私有 URL、账户标识符或付费计划凭证；
- 原始行情数据导出、完整新闻流、复制的文章正文、专有报告或批量供应商 payload；
- 用于绕过验证码、付费墙、鉴权、API 配额、反爬控制或供应商限制的代码；
- 真实用户账户数据、订单历史、持仓、prompt、记忆记录、日志或个人信息；
- 无法说明兼容许可证和署名要求的第三方代码或数据集；
- 将项目表述为持牌投资顾问、券商或真实资金交易系统的改动。
- 更改本仓库许可证的改动。

## 金融和数据源改动

涉及分析、股票推荐、交易行为或数据访问的改动必须：

- 保持项目的研究和模拟交易定位；
- 在数据缺失或来源未授权时提供清晰失败行为；
- 优先使用供应商批准的 API；
- 在 [DATA_SOURCES.md](./DATA_SOURCES.md) 中补充或更新来源条款；
- 避免记忆系统中的关键词捷径或为了测试样本定制 prompt；
- 保持 prompt 示例和评测样本不同。

## 安全敏感改动

涉及鉴权、授权、插件执行、依赖安装、浏览器自动化或外部网络访问的改动必须：

- 默认采用适合私有本地环境的保守行为；
- 将管理或诊断接口置于鉴权之后；
- 避免在 API 响应或日志中返回密钥；
- 避免在生产示例中使用宽泛 CORS；
- 涉及代码改动时补充或更新授权边界测试。

## 测试和夹具

后端和记忆逻辑使用 pytest。前端改动使用 lint/typecheck。纯文档改动可以运行 `git diff --check` 等轻量检查。

测试夹具应使用合成数据或已获授权的数据。如果夹具模拟供应商 payload，应保持最小化，并移除文章正文、专有批量行和敏感标识符。

## Pull Request 检查清单

- 改动保持非商业、研究/模拟定位。
- 不包含密钥或真实用户数据。
- 不提交未经授权的第三方数据。
- 相关数据源条款已经检查并记录。
- 安全敏感接口或运行时执行路径没有变得更容易公开暴露。
- PR 描述中列出测试或验证命令。

## 报告安全问题

不要用公开 issue 报告可利用漏洞或泄露凭证。请使用 [SECURITY.md](./SECURITY.md) 中列出的私有渠道；如果暂未列出，请先私下联系维护者，再公开细节。

---

# Contributing

Thanks for contributing to Best-AI-Trader. This project is available under the MIT License, and it touches
regulated financial workflows and third-party data.

## Contribution License

By submitting a pull request, issue patch, or other contribution to this repository, you confirm that you have the
right to contribute it and that your contribution may be distributed under the repository's MIT License.

If you contribute to the `memo/` submodule separately, follow the MemoFlux project's upstream contribution
process.

## What Not To Submit

Do not submit:

- API keys, cookies, tokens, passwords, private URLs, account identifiers, or paid-plan credentials;
- raw market-data dumps, full news feeds, copied article bodies, proprietary reports, or bulk vendor payloads;
- code designed to bypass captchas, paywalls, authentication, API quotas, anti-bot controls, or provider restrictions;
- real user account data, order history, holdings, prompts, memory records, logs, or personal information;
- third-party code or datasets unless you can point to a compatible license and attribution requirement;
- changes that present the project as a licensed investment adviser, broker, or real-money trading system;
- changes that alter this repository's license.

## Financial And Data-Source Changes

For changes that affect analysis, stock recommendations, trading behavior, or data access:

- keep the project positioned as research and simulated trading;
- include clear failure behavior when data is missing or the source is unauthorized;
- use provider-approved APIs where available;
- document source terms in [DATA_SOURCES.md](./DATA_SOURCES.md);
- avoid keyword-only memory-system shortcuts or test-specific prompt hacks;
- keep prompt examples different from evaluation samples.

## Security-Sensitive Changes

For changes involving authentication, authorization, plugin execution, dependency installation, browser automation,
or external network access:

- default to private/local-safe behavior;
- gate admin or diagnostic endpoints behind authentication;
- avoid returning secrets in API responses or logs;
- avoid broad CORS in production examples;
- add or update tests for authorization boundaries where code changes are involved.

## Tests And Fixtures

Use pytest for backend and memory logic. Use frontend lint/typecheck for frontend changes. For documentation-only
changes, run lightweight checks such as `git diff --check`.

Fixtures should be synthetic or rights-cleared. If a fixture resembles a provider payload, keep it small and remove
article bodies, proprietary row batches, and sensitive identifiers.

## Pull Request Checklist

- The change keeps the research/simulation positioning intact.
- No secrets or real user data are included.
- No unlicensed third-party data is committed.
- Data-source terms were checked and documented when relevant.
- Security-sensitive endpoints or runtime execution paths are not made easier to expose publicly.
- Tests or verification commands are listed in the PR description.

## Reporting Security Issues

Do not open a public issue for exploitable vulnerabilities or leaked credentials. Use the private channel listed in
[SECURITY.md](./SECURITY.md) when available, or contact the maintainer privately before publishing details.
