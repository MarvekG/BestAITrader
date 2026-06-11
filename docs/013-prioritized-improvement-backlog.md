# 优先改进事项清单

本文档记录当前项目后续可落地的改进事项。内容基于系统性审查结果，并已排除暂不处理的安全暴露、Redis、镜像固定、AI 工具循环重构、AI 日志脱敏、路由错误边界等事项。

## 1. 建议实施顺序

1. 交易可靠性：严格请求 schema、锁内风控、价格与费用一致性、订单通知副作用隔离。
2. AI 稳定性：优化 Memory 客户端、校准选股因子评分。
3. 前端架构：统一 WebSocket 管理、修复交易事件订阅映射、统一认证 token 来源、迁移硬编码文案。
4. 部署配置：按需收紧 Nginx 请求体、超时和限流配置。

## 2. 交易可靠性

### 2.1 下单和订单更新使用严格 Pydantic Schema

现状：

- `backend/app/api/endpoints/trading.py` 中部分接口接受 `Dict[str, Any]`，再在 endpoint 内做字段转换。
- 字段缺失、非法枚举、数值边界和股票代码格式问题可能在较晚阶段才暴露。

建议：

- 增加 `PlaceOrderRequest`、`OrderUpdateRequest` 等请求 schema。
- 对 `action`、`order_type` 使用枚举或 `Literal`。
- 对 `shares`、`price`、`stop_loss` 设置明确数值边界。
- 明确 `stock_code` 规范化和校验规则。
- 订单更新只允许修改明确安全的字段；若修改价格、股数或订单方向，必须重新风控。

优先级：高。

状态：已完成，交易下单和订单更新入口已使用严格 Pydantic schema 校验请求体。

### 2.2 风控检查移动到交易锁内

现状：

- 风险评估在交易服务加锁前执行。
- 并发订单可能同时读取同一现金或持仓状态并通过预检，随后在执行阶段发生冲突。

建议：

- 在 `TradingService.execute_order_and_update_db()` 内获取账户和持仓锁后重新执行风控。
- API 层可以保留轻量预检用于快速反馈，但最终执行前必须以锁内风控为准。
- 风控使用锁定后的账户、持仓和订单快照。

优先级：高。

### 2.3 统一价格解析、手续费和现金校验

现状：

- 风控和交易引擎可能使用不同价格来源或不同现金计算逻辑。
- 市价单、手续费、印花税、过户费等若不统一，会导致风控通过但执行失败，或风控拒绝但实际可执行。

建议：

- 将市场价解析封装为统一函数或服务。
- 风控和交易执行复用同一价格解析结果。
- 风控现金占用包含交易费用。
- 风控响应中返回评估价格、交易金额、费用和总现金需求。

优先级：高。

### 2.4 WebSocket 通知失败不应影响已提交订单

现状：

- 如果订单数据库事务已提交，后续 WebSocket 通知失败不应导致 API 对客户端返回 500。
- 否则用户可能误以为订单失败并重复提交。

建议：

- DB commit 成功后，订单提交结果即为成功。
- WebSocket 通知作为 best-effort 副作用处理。
- 通知失败只记录结构化日志，不回滚订单，不改变 API 成功响应。

优先级：高。

### 2.5 增加持仓唯一性约束

现状：

- 服务层假设一个账户对同一股票只有一条活跃持仓。
- 如果模型层没有唯一约束，重复持仓会破坏估值、卖出匹配、集中度风控和资产统计。

建议：

- 对 `(account_id, stock_code)` 增加唯一约束或针对活跃持仓的部分唯一索引。
- 数据库变更前提供重复数据排查和修复 SQL。
- 更新相关测试夹具和 `_sqlite_test_tables()`。

优先级：中高。

### 2.6 统一账户资产口径

现状：

- 组合估值接口使用动态市场数据。
- 部分账户接口和绩效快照使用持久化字段，可能导致页面和历史快照口径不一致。

建议：

- 复用 `build_portfolio_valuation()` 作为账户资产展示的统一口径。
- 明确区分“账面值”和“实时估值”。
- 绩效快照记录估值来源和版本，避免历史数据语义不清。

优先级：中。

## 3. AI 与 Agent 稳定性

### 3.1 优化 Memory 客户端

现状：

- `backend/app/ai/memory_client.py` 中多处请求创建新的 `httpx.AsyncClient`，连接复用不足。
- Memory 失败语义不够清晰，调用方难以区分服务不可用、超时、鉴权失败、schema 错误和语义失败。

建议：

- 在 FastAPI lifespan 中管理共享 async client。
- 定义 typed result/error 对象。
- 区分 timeout、auth、schema、service unavailable、LLM failure 等错误类型。
- 在 Memory 工具层保留清晰的用户可见失败提示。

优先级：中。

### 3.2 校准智能选股因子评分

现状：

- 智能选股中存在手工权重、流动性分、技术动量分、估值惩罚和风格权重。
- 若缺少文档和校准，很难解释推荐变化。

建议：

- 将评分配置集中为可审计结构。
- 写明每个因子的业务含义、范围和权重依据。
- 优先使用行业相对分位或市场状态归一化。
- 增加边界测试，避免极端值扭曲排序。

优先级：中。

## 4. 前端体验与架构

### 4.1 统一 WebSocket 管理

现状：

- 前端存在全局 WebSocket manager 和页面内 raw WebSocket 并存的情况。
- ticket、重连、心跳、订阅、清理和错误处理存在重复实现。

建议：

- 抽取 `useWebSocketSubscription` 和 `useResourceSubscription`。
- 统一 30 秒一次性 ticket 申请、URL 构造、重连退避、handler 注册与清理。
- 为后端事件建立 typed message registry。
- 页面只消费 typed event，不直接操作 WebSocket 细节。

优先级：高。

### 4.2 修复交易事件订阅映射

现状：

- 交易页面订阅 `position_update`、`order_status`、`trade_executed`。
- 通用事件映射中可能未包含这些后端事件名，导致订阅只是本地 handler 注册，未真正通知后端。

建议：

- 核对后端 WebSocket 事件名称和订阅协议。
- 补齐 `getBackendEventType()` 映射。
- 增加前端 WebSocket contract 测试，确保订阅消息发到后端。

优先级：高。

### 4.3 统一认证 Token 来源

现状：

- 前端 token 同时存在 Zustand persist 和 `localStorage.token`。
- API client 与路由守卫读取来源不同，存在状态漂移风险。

建议：

- 引入统一 auth/session service。
- 登录、登出、401 清理、持久化恢复都通过同一入口。
- API client、WebSocket ticket、路由守卫都消费同一认证状态。

优先级：中高。

### 4.4 迁移前端硬编码文案

现状：

- 前端已有 `react-i18next`，翻译来源为后端 `/api/v1/general/i18n/{lng}`。
- 部分页面仍存在用户可见的硬编码中文或英文文案。

建议：

- 只迁移硬编码文案，不调整 i18n 加载时序。
- 优先处理按钮、提示、错误信息、空状态和页面标题。
- 文案 key 按页面或功能域分组，并同步后端 i18n catalog。

优先级：低。

### 4.5 强化 API Typing 和错误归一化

现状：

- 部分 API 返回 `unknown` 或 `any`。
- 页面中仍可能直接检查 `error.response`，长期会增加类型漂移和错误展示不一致。

建议：

- 增加统一 `ApiError` helper。
- 所有 API 模块定义明确 response type。
- 页面统一使用 `getApiErrorMessage()` 或等价工具。
- 逐步移除 ESLint 中对 `no-explicit-any` 的例外。

优先级：中。

## 5. 部署配置

### 5.1 按需收紧 Nginx 限制

现状：

- 根 `nginx.conf` 存在较大的 `client_max_body_size` 和长超时配置。
- 过宽限制会放大慢连接、超大请求、磁盘/内存耗尽和 LLM 滥用风险。

建议：

- 按路由拆分上传、普通 API、WebSocket、LLM streaming 的 body size 和 timeout。
- 普通 API 使用较保守的请求体大小和超时。
- 只对确实需要长连接的 WebSocket 或流式接口保留较长 read timeout。
- 按需增加基础 rate limit，尤其是登录、测试、插件和维护相关路径。

优先级：中。

## 6. 可拆分落地包

1. 交易 API schema 与风控一致性：请求 schema、锁内风控、价格/费用统一、通知 best-effort。
2. AI 稳定性：Memory 客户端优化、因子评分校准。
3. 前端 WebSocket 与认证统一：WebSocket hook、事件映射、token 单一来源、硬编码文案迁移。
4. 部署配置优化：按需调整 Nginx 请求体、超时和限流配置。

每个任务包都应包含对应测试或质量门禁，并避免一次性跨越多个子系统的大型重构。
