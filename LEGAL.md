# 法律与合规说明

本仓库用于研究、教育和模拟交易工作流。它不是持牌投资顾问、证券经纪、数据再分发服务或金融信息服务。

本文是给维护者和使用者的操作性说明，不构成法律意见。任何部署、修改、再分发或向第三方开放本项目的使用者，都需要自行完成法律审查和合规安排。

## 许可定位

天枢智投（Best-AI-Trader）采用 MIT License 公开源码。该许可证允许商业和非商业用途，包括使用、复制、修改、合并、出版发行、散布、再许可和/或销售本软件的副本。详情请参见 [LICENSE](./LICENSE)。

`memo/` 子模块是独立的 MemoFlux 记忆服务项目，同样使用 MIT License。

## 投资建议边界

系统可以生成股票分析、`buy` / `sell` / `hold` 标签、目标仓位、止损字段和模拟订单执行。这些输出只应被视为模拟研究工作流中的研究材料。

维护者和下游使用者不得将项目表述为：

- 注册投资顾问、证券投资咨询机构、证券经纪商或券商系统；
- 面向公众提供个性化投资建议的服务；
- 可供真实资金交易直接依赖的 AI 决策工具；
- 可替代适当性评估、风险画像、人工监督或专业判断的系统。

如果某个部署收费、服务客户、提供个性化推荐、管理组合操作或定期发布证券分析，部署方必须独立完成法律审查和合规安排。

## 数据权利边界

本项目包含行情、新闻、搜索 API、浏览器渲染页面和用户安装插件的集成能力。**本项目的 MIT License 仅覆盖项目本身的代码和文档，不覆盖任何第三方数据。** 使用、缓存、复制、转换、再分发、展示或商业化任何第三方数据，均需部署方自行获取对应数据源的合法授权。

每个部署方必须独立确认所配置数据源是否允许目标用途，包括：

- API 访问方式和账户/API Key 使用规则；
- 是否允许目标非商业研究用途；
- 缓存时长和存储位置；
- 是否允许向用户、WebSocket 客户端、看板、日志或模型 prompt 再分发；
- 是否允许使用爬取或浏览器观察到的内容；
- 署名、限速和删除义务。

数据源政策见 [DATA_SOURCES.md](./DATA_SOURCES.md)。

## 隐私和用户数据

公开或多人部署可能处理账户数据、会话历史、订单、持仓、prompt、日志、记忆记录和 API Key。服务开放到私人研究环境之外前，部署方必须提供隐私说明、数据保留策略、删除机制、访问控制和泄露响应流程。

除非已经具备合法依据并审查过服务商条款和数据处理安排，不应把个人数据、敏感账户信息、专有数据或保密交易信息发送给 LLM、搜索、embedding 或新闻服务商。

## 自动化决策

本项目默认应被视为人类参与的模拟系统。金融场景中的自动化决策可能触发金融监管、消费者保护、数据保护、平台条款和 AI 服务条款下的额外义务。

生产环境应禁用或严格门控任何类似自动交易的能力，除非已经具备书面控制框架、人工审核、审计日志和适用监管批准。

## 维护者检查清单

- README、UI、API 文档和生成报告中应保持明显的投资风险提示。
- 不要在仓库或镜像中发布真实第三方新闻文本、行情数据导出或专有数据集。
- 要求贡献者确认其贡献为原创或已获得适当许可。
- 将数据源适配器定位为集成示例，而不是捆绑的数据授权。
- 公开部署默认值应保守：鉴权、受限 CORS、禁用运行时插件安装、禁用未鉴权测试/管理接口，并禁止自动真实资金交易。

## 参考链接

- MIT License: https://opensource.org/licenses/MIT
- SEC investment adviser guidance: https://www.sec.gov/file/ia-1092
- FINRA Rule 2210: https://www.finra.org/rules-guidance/rulebooks/finra-rules/2210
- 中国证监会《证券投资顾问业务暂行规定》: https://www.csrc.gov.cn/csrc/c101838/c1022038/content.shtml
- 中华人民共和国个人信息保护法: https://www.miit.gov.cn/jgsj/zfs/fl/art/2022/art_515a4b20c12f430eab54bb4f56d89f56.html

---

# Legal And Compliance Notice

This repository is published for research, education, and simulated trading workflows. It is not a licensed
investment advisory, brokerage, data redistribution, or financial information service.

This document is operational guidance for maintainers and users. It is not legal advice. Users who deploy,
modify, redistribute, or expose this project to third parties are responsible for their own legal review and
compliance program.

## License Positioning

Best-AI-Trader is released under the MIT License. The license permits commercial and noncommercial use, including
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software. See [LICENSE](./LICENSE)
for details.

The `memo/` submodule is a separate MemoFlux memory service project, also under the MIT License.

## Investment Advice Boundary

The system can generate stock analysis, `buy` / `sell` / `hold` labels, target position suggestions, stop-loss
fields, and simulated order execution. Those outputs are research artifacts for simulated workflows only.

Maintainers and downstream users must not present the project as:

- a registered investment adviser, securities investment consultant, broker-dealer, or brokerage system;
- a service that provides individualized investment advice to the public;
- a tool whose AI output can be relied on for real-money trading without independent verification;
- a substitute for user suitability checks, risk profiling, supervision, or professional judgment.

If a deployment charges fees, serves clients, personalizes recommendations, manages portfolio actions, or issues
regular securities analysis, operators must conduct their own legal review and compliance arrangements.

## Data Rights Boundary

This project includes integrations for market data, news, search APIs, browser-rendered pages, and user-installed
plugins. **The MIT License of this project covers only the project's own code and documentation, not any third-party
data.** Operators must obtain their own legal authorization from each data source for any use, caching, reproduction,
transformation, redistribution, display, or commercialization of third-party data.

Each operator must independently confirm that every configured data source permits the intended use. This includes:

- API access method and account/API key sharing rules;
- whether the source permits the intended noncommercial research use;
- caching duration and storage location;
- redistribution to users, WebSocket clients, dashboards, logs, or model prompts;
- use of scraped or browser-observed content;
- attribution, rate limits, and deletion obligations.

See [DATA_SOURCES.md](./DATA_SOURCES.md) for the project data-source policy.

## Privacy And User Data

Public or multi-user deployments may process account data, session history, orders, holdings, prompts, logs, memory
records, and API keys. Operators must provide their own privacy notice, data retention policy, deletion process,
access controls, and breach response process before exposing the service beyond a private research environment.

Do not send personal data, sensitive account information, proprietary data, or confidential trading information to
LLM, search, embedding, or news providers unless the operator has a lawful basis and has reviewed the provider's
terms and data-processing posture.

## Automated Decision-Making

The default project should be treated as a human-in-the-loop simulation. Automated decisions in finance can trigger
additional obligations under financial regulation, consumer protection law, data protection law, platform terms, and
AI service terms.

Production operators should disable or gate any automated trading-like behavior unless they have a documented
control framework, human review, audit logs, and applicable regulatory approval.

## Maintainer Checklist

- Keep investment-risk disclaimers visible in README, UI, API docs, and generated reports.
- Avoid shipping real third-party news text, market-data dumps, or proprietary datasets in the repository or images.
- Require contributors to confirm that their contributions are original or properly licensed.
- Treat data-source adapters as integration examples, not bundled data rights.
- Keep public deployment defaults conservative: authentication, restricted CORS, no runtime plugin install, no
  unauthenticated test/admin endpoints, and no automatic real-money trading.

## Reference Links

- MIT License: https://opensource.org/licenses/MIT
- SEC investment adviser guidance: https://www.sec.gov/file/ia-1092
- FINRA Rule 2210: https://www.finra.org/rules-guidance/rulebooks/finra-rules/2210
- CSRC Securities Investment Advisory Interim Provisions: https://www.csrc.gov.cn/csrc/c101838/c1022038/content.shtml
- PRC Personal Information Protection Law: https://www.miit.gov.cn/jgsj/zfs/fl/art/2022/art_515a4b20c12f430eab54bb4f56d89f56.html
