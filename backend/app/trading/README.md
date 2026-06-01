# Trading Architecture

本文档说明 `backend/app/trading` 这块模块是干什么的、交易请求从哪里进来、如何写库，以及 AI 下单和手动下单如何共用同一套账本。

Trading 模块是系统的模拟交易与持仓账本核心。它采用“入口层发起订单、服务层写库编排、引擎层纯计算”的结构，核心目标是让 AI 决策和手动下单都落到同一套账户、订单、成交和持仓账本口径。

## 1. 核心文件

- [`backend/app/api/endpoints/trading.py`](../api/endpoints/trading.py)：交易 API，主要入口是 `POST /api/v1/trading/orders`。
- [`backend/app/ai/agentic/tools.py`](../ai/agentic/tools.py)：AI 下单核心工具 `execute_trading_order(...)`。
- [`backend/app/ai/llm_engine/agents/governance.py`](../ai/llm_engine/agents/governance.py)：PM 专属下单工具包装层，隐藏 `session_id`。
- [`backend/app/trading/service.py`](./service.py)：订单执行的数据库编排层。
- [`backend/app/trading/trading_engine.py`](./trading_engine.py)：纯计算交易引擎。
- [`backend/app/api/endpoints/accounts.py`](../api/endpoints/accounts.py)：账户、资产和持仓查询 API。

## 2. 下单入口

### 2.1 手动 / 前端 / API 下单

`POST /api/v1/trading/orders` 接收订单字典，当前会处理：

- `session_id`：可选；传入时会校验该 session 是否属于当前用户。
- `stock_code`：会通过 `StockCodeStandardizer` 标准化。
- `action`：`buy` 或 `sell`。
- `shares`：成交股数，必须满足 A 股一手规则。
- `price`：限价单价格；市价单最终会从行情表取最新价。
- `order_type`：`market` 或 `limit`，默认 `market`。
- `stop_loss`：买入时可选，必须为正数；卖出时会被忽略。

这个入口只做参数整理、账户校验和返回格式适配，不直接撮合，也不直接更新持仓。真正执行统一交给 `TradingService.execute_order_and_update_db(...)`。

### 2.2 AI 下单

AI 下单核心函数是 `execute_trading_order(...)`。它不是让模型直接给股数，而是让 PM 给出：

- `stock_code`
- `action`
- `target_position`
- `session_id`
- `stop_loss`

实际 PM Agent 看到的是 `governance.py` 中的包装工具，不需要自己传 `session_id`，但必须传明确的 `stop_loss`。工具当前行为：

1. 检查 `settings.ENABLE_AUTO_TRADE`。
2. 校验 `stop_loss > 0` 和 `session_id` 格式。
3. 读取 session、用户和账户。
4. 优先从最新实时行情取价格，失败时回退到最新日 K 收盘价。
5. 根据账户总资产、当前持仓和 `target_position` 计算买卖差额。
6. 按 100 股一手向下取整；卖出不超过 T+1 可卖股数。
7. 调用 `TradingService.execute_order_and_update_db(...)`。

注意：`get_all_tools()` 不把 `execute_trading_order` 暴露给所有 Agent。它只由 PM 的 `PortfolioManagerAgent.get_tools()` 追加，避免普通分析师直接下单。

## 3. TradingService

`TradingService` 是交易链路的数据库一致性层，当前职责是：

1. 对账户和目标持仓加 `SELECT ... FOR UPDATE` 锁。
2. 先创建 `orders.status = pending` 的订单。
3. 构造账户快照和持仓快照。
4. 解析并合并止损价。
5. 调用 `TradingEngine.execute_order(...)`。
6. 将引擎结果写回 `accounts / positions / orders / trade_records`。
7. 推送订单、持仓和成交 WebSocket 事件。

它不承担复杂撮合计算，但负责让数据库状态和引擎返回结果保持一致。

## 4. TradingEngine

`TradingEngine` 是纯计算层，不直接访问数据库写入。当前负责：

- 订单合法性检查。
- 市价单价格缺失后的拒单逻辑。
- 手续费计算。
- A 股 100 股一手约束。
- T+1 可卖股数计算。
- FIFO 买入批次账本维护。
- 买卖后账户与持仓快照更新。
- `should_auto_sell(...)` 止损 / 止盈判断函数。

当前成功订单按整笔成交处理，不模拟订单簿排队、滑点、盘口深度、部分成交或涨跌停约束。

## 5. 持仓账本

`positions.purchase_details` 是当前交易系统最关键的 JSON 字段。典型结构：

```json
{
  "ledger": [
    {
      "time": "2026-03-17T00:52:45.657804",
      "shares": 1000,
      "price": 60.48,
      "cost_basis": 60.5123
    }
  ],
  "stop_loss": 58.0
}
```

`ledger` 表示当前仍留在仓位里的买入批次，不是历史成交流水。规则：

- 买入时追加一个批次。
- 卖出时按 FIFO 扣减最早批次。
- 被扣空的批次会从账本移除。
- `ledger.shares` 总和应等于当前持仓股数。

`available_shares` 和 `frozen_shares` 是持仓快照字段。查询和交易时会通过 `build_position_snapshot(...)` 基于账本重新推导，避免盲信旧缓存。

## 6. T+1 规则

当前 T+1 基于 `ledger` 中每个批次的买入日期：

- 今天买入的批次不可卖。
- 昨天及更早买入的批次可卖。
- 可卖股数不会超过当前持仓股数。

核心函数：

- `normalize_purchase_details(...)`
- `derive_share_fields(...)`
- `get_sellable_shares(...)`
- `get_executable_sell_shares(...)`
- `sync_position_share_fields(...)`

## 7. 买入逻辑

买入前会检查：

- `action` 必须是 `buy` 或 `sell`。
- `order_type` 必须是 `market` 或 `limit`。
- 股数必须大于 0。
- 股数必须是 100 的倍数。
- 限价单价格必须大于 0。
- 资金必须覆盖成交额和手续费。

买入成功后：

- 扣减现金。
- 增加账户市值。
- 计算包含手续费摊薄后的 `avg_cost`。
- 向 `purchase_details.ledger` 追加批次。
- 当天买入股份计入 `frozen_shares`，不计入 `available_shares`。
- 若传入或解析到 `stop_loss`，写入 `purchase_details.stop_loss`。

## 8. 卖出逻辑

卖出时会优先按账本计算真实可卖股数。规则：

1. 有 `ledger` 时，按 `ledger + T+1` 计算可卖数量。
2. 没有 `ledger` 时，回退到持仓快照里的 `available_shares`。
3. 实际可卖数量不会超过 `total_shares`。

卖出成功后：

- 按 FIFO 扣减 `ledger`。
- 用 `turnover - matched_cost - total_fee` 计算已实现盈亏。
- 更新现金、总资产、持仓市值和浮盈亏。
- 如果持仓股数归零，`TradingService` 会删除对应 `positions` 记录。
- 部分减仓会尽量保留原有 `purchase_details.stop_loss`。

## 9. 费用规则

费用常量定义在 `TradingEngine`：

- 佣金：`0.0002`
- 最低佣金：`5`
- 印花税：卖出收 `0.001`
- 过户费：`0.00002`
- 最低过户费：`0.01`

买入成本中的单股 `cost_basis` 会包含买入手续费；卖出已实现盈亏会扣除卖出费用。

## 10. 止损价

当前止损价会真实落库到 `purchase_details.stop_loss`。来源优先级：

1. 显式传入 `stop_loss`。
2. 如果没有显式值，则从当前 session 最近一条 `portfolio_manager` 的 `DebateMessage.analysis.stop_loss` 提取。
3. 如果仍然没有，则不写入。

手动 `/orders` 买入已经支持直接传 `stop_loss`；AI PM 下单要求必须传 `stop_loss`。

`should_auto_sell(...)` 当前会先读取 `purchase_details.stop_loss`，当 `current_price <= stop_loss` 时触发止损判断；没有显式止损价时，才回退到默认 5% 止损。止盈仍使用默认 10% 阈值。

当前应用启动时不会启动自动止损后台扫描任务。`main.py` 明确记录止损/止盈检查任务已移除，由 AI 自主决策是否卖出。因此当前状态是：止损数据已落库，判断函数可用，但不会定时自动卖出。

## 11. 查询口径

账户与持仓查询集中在 `accounts.py`：

- `GET /api/v1/accounts/my-assets`
- `GET /api/v1/accounts/my-total-funds`
- `PUT /api/v1/accounts/my-total-funds`
- `GET /api/v1/accounts/my-positions`
- `POST /api/v1/accounts/reset-account`
- `GET /api/v1/accounts/positions/{session_id}`
- `GET /api/v1/accounts/positions/single/{position_id}`

持仓查询会：

- 用最新行情重算当前价和浮盈亏。
- 用交易引擎动态推导 `available_shares / frozen_shares`。
- 从 `purchase_details.stop_loss` 提取止损价。

## 12. 不变量

交易链路正常时应长期满足：

1. `sum(purchase_details.ledger[*].shares) == positions.total_shares`
2. `available_shares + frozen_shares == total_shares`
3. `available_shares == 昨天及更早买入批次的剩余股数`
4. 卖出后 `ledger` 只保留剩余批次
5. 全部卖完后持仓记录被删除
6. 若存在 `purchase_details.stop_loss`，持仓查询接口应返回该值

## 13. 当前边界

- 这是模拟交易，不是真实券商撮合。
- 市价单依赖数据库中可用的最新行情。
- 限价单校验通过后按整笔成交处理。
- 没有后台自动止损任务。
- 没有统一接入真实市场冲击成本、滑点、涨跌停、停复牌和成交量约束。

## 14. 阅读顺序

维护交易链路时建议按以下顺序阅读：

1. [`backend/app/api/endpoints/trading.py`](../api/endpoints/trading.py)
2. [`backend/app/trading/service.py`](./service.py)
3. [`backend/app/trading/trading_engine.py`](./trading_engine.py)
4. [`backend/app/api/endpoints/accounts.py`](../api/endpoints/accounts.py)
5. [`backend/app/ai/llm_engine/agents/governance.py`](../ai/llm_engine/agents/governance.py)
6. [`backend/app/ai/agentic/tools.py`](../ai/agentic/tools.py)
