# 天枢智投 (Best-AI-Trader)

[系统示例：v0.1.0 Release](https://github.com/MarvekG/BestAITrader/releases/tag/v0.1.0)

天枢智投（Best-AI-Trader）是一个面向 A 股投研、AI 多智能体决策、模拟交易、长期记忆和经验复盘的下一代智能交易系统。

它基于 LLM、Agentic Workflow、工具调用、Skills、长期记忆和后验经验复盘等先进 AI 技术，构建可执行、可审计、可持续进化的智能投研闭环。

它不是一个只把行情表包成聊天机器人的 demo，而是一套把金融数据工程、工具增强型 Agent、专业投研分工、
多轮策略辩论、组合经理决策、长期记忆、模拟撮合、持仓账本、任务追踪和前端实时展示串起来的完整 AI 投研系统。

一句话概括：天枢智投让 LLM 从“会聊天的模型”升级为一个能查数据、会分工、能辩论、可记忆、可审计、能执行和能复盘的 AI 投研与交易团队。

> 风险说明：本项目用于研究、开发和模拟交易，不构成任何投资建议。真实交易前请自行验证数据、策略和风险。
> 项目不附带任何第三方行情、新闻、搜索或金融数据授权；启用相关数据源前，请自行确认服务条款、缓存限制、再分发限制和非商业研究使用权限。

## 致敬与来源说明

天枢智投（Best-AI-Trader）在多智能体投研工作流的学习和设计过程中，受到
[TradingAgents-CN](https://github.com/hsliuping/TradingAgents-CN) 的启发。感谢 `hsliuping/TradingAgents-CN`
对 TradingAgents 思路在中文社区的传播、中文化实践和工程化探索。

本项目围绕 A 股投研、长期记忆、后验经验复盘、模拟交易和可审计前端体验做独立实现与扩展。上述致敬不改变
天枢智投的源码开放、非商业研究许可定位，也不代表项目之间存在官方从属或授权关系。

## 友链

[![LinuxDo](https://img.shields.io/badge/社区-LinuxDo-blue?style=for-the-badge)](https://linux.do/)

## 一眼看懂

- **工具增强型 AI 投研**：Agent 不只生成文本，而是通过行情、财务、新闻、政策、资金流、技术指标和沙箱计算工具形成证据。
- **多智能体投资委员会**：新闻、政策、情绪、基本面、技术面、资金流、风控、多头、空头和 PM 分工协作，模拟专业投委会流程。
- **多轮辩论与结构化决策**：系统把分析、质疑、反驳、收敛和 PM 决策拆成可追踪工作流，不依赖单次 prompt 碰运气。
- **AI 长期记忆闭环**：集成 MemoFlux，把经验、复盘、偏好和历史结论沉淀为可召回、可审计的长期记忆。
- **后验经验复盘系统**：用真实价格路径检验 PM 决策，自动归因涨跌主因、修正辩论流程，并把可复用规则写回记忆。
- **数据工程底座**：使用 PostgreSQL、JSONB、API 注册表和数据刷新调度承载 A 股核心数据与长尾异构数据。
- **模拟交易引擎**：支持账户、订单、成交记录、持仓、FIFO 批次账本、费用、A 股一手和 T+1 约束。
- **实时可观测体验**：异步任务、WebSocket、前端审计页和统一日志让 AI 分析过程可看、可查、可复盘。
- **Docker 一体化部署**：PostgreSQL、Redis、LiteLLM、MemoFlux、独立沙箱、网页抓取、后端、前端和 Nginx 一套 Compose 拉起。

## 先进 AI 特性

天枢智投的目标不是“给 LLM 一个 prompt，让它猜涨跌”，而是把现代 Agentic AI 的关键能力工程化到真实投研链路中。

| AI 能力 | 在项目中的体现 |
| --- | --- |
| Agentic Workflow | 使用多阶段工作流组织上下文获取、垂直分析、战略辩论和 PM 决策，让复杂任务按流程推进 |
| Tool-use Reasoning | Agent 可以调用数据查询、新闻检索、政策分析、技术指标、资金流和 Python 沙箱工具，而不是只依赖模型记忆 |
| Multi-Agent Debate | 多头、空头、风控、情绪、新闻、政策等角色互相制衡，降低单一视角直接拍脑袋的风险 |
| Structured Decision | PM 输出结构化决策，包含动作、理由、信心、仓位、风险和交易参数，便于审计和执行 |
| Long-Term Memory | 集成 MemoFlux，让系统能沉淀经验、召回历史上下文，并保留可审计召回证据 |
| Evidence-Backed Recall | 记忆和分析结果可以携带证据、引用和上下文，不只返回相似文本 |
| Post-Decision Review | 经验复盘系统把 PM 结论、真实走势、交易执行和 Agent timeline 放在一起复核，提炼可复用规则 |
| Human-in-the-loop Audit | 前端实时展示 AI 过程，后端保存 session、message、task、order 和 memory 事件，便于人工复盘 |
| Closed-loop Trading Simulation | AI 决策可以进入模拟交易、持仓账本和后续复盘，形成从分析到执行再到学习的闭环 |

这套系统更接近一个 AI 投研操作系统：数据层负责喂给模型真实上下文，Agent 层负责分工和推理，交易层负责把决策落到账户和持仓，记忆层负责让系统从历史中持续进化。

## 部署

当前推荐并支持的完整启动方式是 Docker Compose。服务会启动 PostgreSQL、Redis、LiteLLM、MemoFlux、Memory pgvector、独立 Python 沙箱、独立 WebFetch 网页渲染服务、可选 Scrapling MCP、FastAPI 后端、React 前端和 Nginx 统一入口。

完整部署步骤、环境变量、启动停止、验证和排障见 [部署指南](./docs/002-deployment.md)。
Windows 用户建议使用
[Windows WSL2 Docker Engine 部署指南](./docs/004-windows-wsl-docker-engine-deployment.md)。

## 为什么它不一样

| 能力 | 常见 LLM 交易 Demo | 天枢智投 |
| --- | --- | --- |
| 数据输入 | 少量手写 prompt 或单一行情 | 行情、财务、新闻、政策、情绪、资金流等上下文分层 |
| AI 形态 | 单模型一次性输出 | 工具增强、多角色、多阶段、多轮辩论的 Agent 工作流 |
| 决策方式 | 直接输出买卖建议 | 垂直分析、战略辩论、PM 汇总、结构化决策 |
| 长期能力 | 每次分析互相割裂 | MemoFlux 记忆历史结论，经验复盘系统把后验教训写回长期记忆 |
| 可审计性 | 结论难以回放 | session、message、task、order、memory 和 WebSocket 事件可追踪 |
| 模拟交易 | 简化买卖记录 | 订单、账户、持仓、成交、费用、T+1、FIFO 批次账本 |
| 选股流程 | 直接让模型挑股票 | 股票池过滤、因子初排、候选压缩、LLM 深研、推荐生成 |
| 工程化程度 | 单脚本或单服务 | FastAPI、React、PostgreSQL、Redis、pgvector、LiteLLM、独立沙箱、WebFetch、Nginx、Docker Compose 一体化 |

## 核心场景

### 1. AI 辩论与 PM 决策

这是系统的核心 AI 能力：把“一个模型直接给答案”升级成“多个专业角色共同完成投研决策”。前端或 API 发起分析后，后端会创建异步任务并运行 LLM 工作流：

1. 构造股票上下文；
2. 并行生成新闻、政策、情绪和垂直分析报告；
3. 进入多头、空头等战略辩论；
4. PM 汇总仓位、风险和上下文，输出最终结构化决策；
5. 过程和结果写入数据库，并实时推送到前端。

核心实现见 [LLM Debate Engine](./backend/app/ai/llm_engine/README.md)。

### 2. 模拟交易与持仓账本

AI 决策不会停留在文本层面，而是可以进入模拟账户和交易账本。交易链路把入口、数据库编排和纯计算引擎分开：

- API 或 AI 工具负责发起订单；
- `TradingService` 负责账户、持仓、订单和成交记录的一致性写入；
- `TradingEngine` 负责合法性检查、费用计算、T+1 可卖股数、FIFO 账本和持仓快照。

核心实现见 [Trading Architecture](./backend/app/trading/README.md)。

### 3. AI 智能选股

AI 智能选股不是对全市场逐只调用模型，而是把确定性因子和 LLM 深研组合起来：先用规则和数据压缩候选池，再让模型做高价值判断。

1. 根据 `warehouse / core / all` 构建股票池；
2. 按风格和因子做确定性初排；
3. 控制同一行业数量和研究候选数量；
4. 用 LLM 对候选池做整池研究；
5. 输出推荐列表、备选、风险摘要和推荐逻辑。

核心实现见 [AI 智能选股流程说明](./backend/app/ai/stock_picker/README.md)。

### 4. 经验复盘系统

经验复盘系统把 AI 决策从“生成结论”推进到“用真实结果校验结论”。它会选择已有 PM 决策的 debate session，读取决策后 K 线和相对表现，复盘原始判断到底对在哪里、错在哪里。

1. 收集 PM 决策、各 Agent timeline、订单成交和后验市场结果；
2. 对 5 日、20 日、60 日收益、回撤、相对指数和相对行业表现做复盘；
3. 分辨被验证信号、被证伪信号和噪音信号；
4. 输出改进后的动作、仓位、止损、买卖规则和 debate 流程优化；
5. 将高价值经验通过 `write_memory` 写入长期记忆。

核心实现见 [Experience Review System](./backend/app/ai/experience/README.md)。

### 5. 长期记忆

系统集成 MemoFlux，让交易系统不只是“每次重新分析”，而是可以沉淀和调用历史经验：

1. 交易辩论和经验复盘结论可以写入长期记忆；
2. 后续分析可召回同一用户、同一股票或通用经验；
3. 记忆服务按 `session` 隔离用户与股票范围，提供结构化召回、引用和 audit；
4. 通过 pgvector、LLM 和审计记录维护召回、证据和 usage 统计。

核心实现见 [`memo/`](./memo/README.md)。

## 文档导航

- [部署指南](./docs/002-deployment.md)：Docker Compose 服务架构、环境变量、启动停止、验证和排障。
- [Windows WSL2 Docker Engine 部署指南](./docs/004-windows-wsl-docker-engine-deployment.md)：不安装 Docker
  Desktop，在 WSL2 Ubuntu 内部署 Docker Engine 和本项目。
- [Backend Capability Map](./backend/app/README.md)：后端高级能力、技术点和实现路径总览。
- [Trading Architecture](./backend/app/trading/README.md)：当前真实生效的交易链路、T+1、账本和止损字段行为。
- [LLM Debate Engine](./backend/app/ai/llm_engine/README.md)：多 Agent 工作流、节点职责、状态流转和持久化。
- [AI 智能选股流程说明](./backend/app/ai/stock_picker/README.md)：股票池、因子初排、LLM 深研和推荐结果。
- [Experience Review System](./backend/app/ai/experience/README.md)：后验经验复盘、市场结果校验、事件流和记忆写入。
- [MemoFlux](./memo/README.md)：当前主系统长期记忆服务文档。
- [Skills Loader Integration Guide](./backend/app/ai/agentic/skills_loader/README.md)：如何新增 Skills、references 和 scripts，让 Agent 加载专业能力。
- [News Plugins Integration Guide](./backend/app/ai/agentic/tooling/news_plugins/README.md)：如何替换自己的新闻插件库、开发新闻源插件并验证 `search_news`。

## 支持与协作

比较有价值的改进方向包括：

- 补充真实数据源接入和字段映射；
- 完善交易引擎对 A 股细节规则的覆盖；
- 增加 AI 决策、经验复盘和 memory 集成的评测用例；
- 改进前端审计页、任务追踪和异常恢复体验；
- 补充部署、监控、备份和安全配置文档。

## 许可定位

本仓库是源码开放的非商业研究项目（source-available, non-commercial），不是 OSI 意义上的开源项目。

本项目主仓采用 [PolyForm Noncommercial License 1.0.0](./LICENSE)，仅允许非商业用途。禁止任何商业行为，
包括利用本项目产生、提供或支持的任何商业行为。不允许商业使用、不允许商业部署、不允许 SaaS 化、
不允许商业销售（包括安装服务收费）、不允许面向商业客户集成。
禁止更改本仓库的许可证。

请同时阅读：

- [Legal And Compliance Notice](./LEGAL.md)：证券投顾、自动化决策、隐私和公开部署边界。
- [Data Source Policy](./DATA_SOURCES.md)：Tushare、历史数据同步源、可配置盯盘网页源、NewsAPI、Tavily 和外部插件的数据授权要求。
- [Security Policy](./SECURITY.md)：生产部署前需要收紧的高风险接口和配置。
- [Contributing](./CONTRIBUTING.md)：贡献者许可、数据源、测试夹具和安全提交要求。

注意：当前系统正在快速迭代，作者会尽量保证兼容，不兼容时会给出升级指导文档。
