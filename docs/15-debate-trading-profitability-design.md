# Debate 股票交易系统赚钱能力最小改进设计

## 背景

本文只聚焦 Debate 股票交易主链路：`Debate -> PM 决策 -> PM 交易工具 -> TradingService -> 交易后验 -> 下一次 Debate`。

当前系统已经做了很多基础能力，不再重复设计：

- PM 是唯一暴露交易工具的 Agent。
- PM 已有结构化 `PMDecision`，包含 `decision`、`confidence_score`、`target_position`、`price_range`、`stop_loss`、`risk_assessment`、`execution_details` 和完整报告。
- PM 交易工具已按 `target_position` 计算目标股数差额，并处理 A 股 100 股一手和 T+1 可卖限制。
- 买入已要求 `stop_loss > 0`。
- 订单统一进入 `TradingService.execute_order_and_update_db()`。
- `TradingService` 已在账户和持仓锁内执行组合风控。
- 交易与订单已通过 `session_id` 关联 Debate session。
- Debate context 已包含账户和目标股票当前持仓信息。
- 下一次同用户同股票 Debate 已能召回上一条 PM 决策。
- 经验复盘已包含 PM 决策、执行摘要和行情后验。

因此，本文只保留当前必须补齐的最小缺口，不设计带外交易、不设计自动调仓、不重构外围系统。

## 核心原则

- PM 继续负责交易动作和仓位决策。
- 系统不在 PM 之外自动买入、卖出、加仓、减仓或止损。
- 风控和交易服务只做校验、阻断和执行，不替 PM 改仓位。
- 只做会直接影响 Debate 交易复盘和下一次 PM 决策质量的改动。

## 必须改进项

### 1. 给 PM 决策补两个最小结构化字段

现状：

- `PMDecision` 已有 `target_position`、`price_range`、`stop_loss` 和 `execution_details`。
- 但止盈/退出计划和持有周期主要埋在文本中，不利于后验复盘，也不利于下一次 PM 判断“是否该继续持有”。

必须新增字段：

- `take_profit`: 止盈价或目标价，必须有有效正数值。
- `holding_horizon_days`: 预期持有天数，必须有有效正整数值。

新增原因：

- `stop_loss` 已经结构化，表示 PM 对下行风险的边界；但上行目标和验证周期仍主要散落在 `price_range`、`investment_plan`、`execution_details` 这类文本字段里。
- `take_profit` 用于记录 PM 预期在哪里兑现收益，方便复盘判断“是否达到 PM 原目标”，也方便下一次 Debate 判断当前价是接近目标、已超过目标，还是仍未达到目标。
- `holding_horizon_days` 用于记录 PM 预期这笔交易看几天，作为复盘解释和下一次 PM 判断的参考。
- `holding_horizon_days` 不改变现有经验复盘的 5d、20d、60d 周期选择和触发条件；复盘周期仍由当前 `experience` 的 horizon 机制决定。
- 在复盘中，`holding_horizon_days` 只用于辅助判断“当前复盘周期是否已经覆盖 PM 原始持有预期”，不用于替代既有 5d、20d、60d 结果。
- 这两个字段不是自动交易触发器，不用于系统自动止盈、自动平仓或自动调仓，只作为 PM 原始交易意图的结构化记录。
- 如果继续只依赖 `execution_details` 文本，后验复盘和下一次 PM 召回只能解析自然语言，稳定性和可审计性都较差。

字段约束：

- `take_profit` 必须大于 0。
- 对 `buy`，以及 `hold` 且 `target_position > 0` 的场景，`take_profit` 必须高于当前可用价格或 PM 明确采用的评估价；否则这笔交易没有正向目标收益，不应作为买入或继续持有计划通过。
- 对 `sell` 或 `target_position = 0` 的清仓场景，`take_profit` 仍必须有值，但只用于记录 PM 原目标价或已放弃的目标价，不作为卖出执行条件，也不要求高于当前价。
- `holding_horizon_days` 必须大于 0。
- PM 输出缺少任一字段，或字段值非法时，本轮 PM 结构化输出应判为无效并触发重试或失败。

不做：

- 不新增复杂 `TradePlan` 表。
- 不要求 PM 输出订单股数。
- 不改变 `target_position` 作为 PM 仓位表达的主接口。
- 不新增带外仓位计算层。

影响文件：

- `backend/app/ai/llm_engine/models.py`
- `backend/app/ai/llm_engine/prompts/templates.py`
- `backend/tests/test_llm_orchestrator.py`
- `backend/tests/test_llm_runner.py`

验收标准：

- PM 输出 schema 包含 `take_profit` 和 `holding_horizon_days`，且二者必须有合法值。
- 旧有 `target_position`、`stop_loss`、`execution_details` 语义不变。
- PM 仍是唯一交易决策者。

### 2. 复盘入口价优先使用真实成交价

现状：

- 经验复盘的 `market_outcome_summary.entry_price` 主要使用 PM 决策日的 K 线 close。
- 但 Debate 交易系统已经有 `Order` 和 `TradeRecord`，已执行交易时应优先使用真实成交价。

必须调整：

- 如果当前 session 有买入成交，后验 entry price 使用成交均价。
- 如果没有成交，再使用现有决策日 close 口径。
- `market_outcome_summary` 增加 `entry_price_source`，例如 `trade_fill_price` 或 `decision_day_close`。
- 复盘仍保留现有 5d、20d、60d 行情收益逻辑，不扩大范围。

不做：

- 不新增完整回测系统。
- 不新增真实撮合模型。
- 不调整 stock picker 后验。
- 不重写经验复盘 workflow。

影响文件：

- `backend/app/ai/experience/service.py`
- `backend/tests/test_experience_service.py`

验收标准：

- 有成交记录时，复盘 entry price 来自实际成交均价。
- 无成交记录时，沿用现有 close 口径。
- 复盘输出明确 entry price 来源。

### 3. 下一次 Debate 召回上一轮 PM 决策的交易结果

现状：

- `_get_previous_pm_decision()` 已召回同用户同股票上一条 PM 决策。
- 但召回内容主要是 PM 原始观点，缺少该决策后是否成交、成交价、盈亏和复盘结论。

必须调整：

- `_get_previous_pm_decision()` 增加上一轮交易执行摘要。
- 最小字段包括：`order_count`、`filled_order_count`、`avg_fill_price`、`total_quantity`、`realized_pnl`、`latest_trade_time`。
- 如果已有经验复盘结果，可附带最近一次复盘的 `original_judgment.verdict` 和 `decision_process_improvement.pm_changes`。
- PM prompt 中明确要求：再次分析同一股票时，必须先说明上一轮交易结果如何影响本次仓位和决策。

不做：

- 不引入 Agent 权重学习。
- 不做自动经验采纳。
- 不新增 Memory 强制写入规则。
- 不让历史交易结果自动生成新订单。

影响文件：

- `backend/app/ai/llm_engine/orchestrator.py`
- `backend/app/ai/llm_engine/prompts/templates.py`
- `backend/tests/test_llm_orchestrator.py`

验收标准：

- 下一次同用户同股票 Debate 的 PM runtime context 包含上一轮执行摘要。
- 若上一轮已有复盘结论，PM 可看到最小复盘结论。
- PM 输出报告中能审计到是否吸收上一轮结果。

## 明确不做

- 不做自动止损卖出。
- 不做后台自动调仓。
- 不新增系统替 PM 计算最终仓位的决策层。
- 不改 PM 交易工具的核心接口语义。
- 不重构风控规则体系。
- 不重构交易撮合模型。
- 不扩展 stock picker。
- 不新增大规模数据质量体系。
- 不新增复杂绩效平台。

## 实施顺序

1. 先加 `take_profit` 和 `holding_horizon_days`，让 PM 决策的退出意图可结构化。
2. 再修复经验复盘 entry price，保证交易后验用真实成交价。
3. 最后把上一轮执行摘要和复盘结论注入下一次 PM context。

## 最小完成标准

完成后，系统应能回答三个问题：

- PM 上次决定买、卖或持有时，原计划是什么。
- 这个计划是否实际成交，成交价和结果是什么。
- PM 这次是否基于上次交易结果调整了仓位或判断。

只要这三个问题可审计，Debate 交易系统就具备了最小赚钱闭环；其他优化可以后续再做。
