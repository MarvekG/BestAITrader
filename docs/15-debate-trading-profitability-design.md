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

状态：已完成。

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
- `backend/tests/test_debate_engine.py`
- `backend/tests/test_agentic_logic.py`

验收标准：

- PM 输出 schema 包含 `take_profit` 和 `holding_horizon_days`，且二者必须有合法值。
- 旧有 `target_position`、`stop_loss`、`execution_details` 语义不变。
- PM 仍是唯一交易决策者。

### 2. 复盘入口价优先使用真实成交价

状态：已完成。

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

状态：已完成。

现状：

- `_get_previous_pm_decision()` 已召回同用户同股票上一条 PM 决策。
- 交易不是每轮 Debate 都会发生，因此上一轮 PM 决策只能携带“是否有订单/是否有成交”的显式状态，不能假设已有交易结果。
- 经验复盘是周期性的，至少要到 5d、20d 或 60d 等 horizon 满足后才可能生成，不适合直接作为下一次 PM 的稳定输入。

必须调整：

- `_get_previous_pm_decision()` 增加上一轮交易执行摘要。
- 最小字段包括：`has_orders`、`has_trades`、`order_count`、`filled_order_count`、`avg_fill_price`、`total_quantity`、`realized_pnl`、`first_order_time`、`latest_order_time`、`first_trade_time`、`latest_trade_time`。
- 没有订单或成交时必须显式返回空状态，例如 `has_orders=false`、`has_trades=false`，避免 PM 误以为已有交易结果。
- 上一轮交易信息必须带日期，避免只有价格和数量而没有时间锚点。
- 不向 PM 注入周期性经验复盘结论；复盘结果仍留在 `experience` 系统内用于后验分析和经验库。

不做：

- 不引入 Agent 权重学习。
- 不做自动经验采纳。
- 不新增 Memory 强制写入规则。
- 不让历史交易结果自动生成新订单。
- 不把 5d/20d/60d 周期性复盘结论直接塞进 PM runtime context。

影响文件：

- `backend/app/ai/llm_engine/orchestrator.py`
- `backend/tests/test_llm_orchestrator.py`

验收标准：

- 下一次同用户同股票 Debate 的 PM runtime context 包含上一轮执行摘要。
- 上一轮无订单/无成交时，执行摘要明确标记 `has_orders=false`、`has_trades=false`。
- 上一轮有订单/成交时，执行摘要包含订单和成交时间字段。
- PM 输入不包含周期性复盘摘要。

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

1. 已完成：加 `take_profit` 和 `holding_horizon_days`，让 PM 决策的退出意图可结构化。
2. 已完成：修复经验复盘 entry price，保证交易后验优先使用真实买入成交均价。
3. 已完成：把上一轮 PM 决策的交易执行摘要注入下一次 PM context；不注入周期性经验复盘结论。

## 最小完成标准

完成后，系统应能回答三个问题：

- PM 上次决定买、卖或持有时，原计划是什么。
- 这个计划是否实际产生订单或成交，若有成交则成交时间、成交价和结果是什么。
- PM 这次是否参考了上一轮 PM 决策和可用交易结果来调整仓位或判断。

只要这三个问题可审计，Debate 交易系统就具备了最小赚钱闭环；其他优化可以后续再做。

## 后续提高买卖成功率的优化方向

当前分支已经补齐 PM 退出目标、真实成交复盘口径、上一轮执行摘要、结构化记忆时间和持仓字段说明。后续如果继续提升买卖成功率，优先做稳定性更高的硬校验和可审计闭环，而不是继续堆提示词或新增低价值字段。

### 1. PM 决策质量门禁

优先级：最高。

目标：减少明显低质量买卖，尤其是错误买入、错误卖出、仓位方向不一致，以及报告与结构化字段互相矛盾。

建议位置：PM 最终结构化输出之后、实际交易工具执行之前；或交易工具入口处做同等校验。门禁只阻断明显不一致或不可执行的交易，不替 PM 生成新仓位。

最小校验项：

- `decision`、`target_position` 与当前仓位方向一致：
  - `target_position > current_position` 才能是 `buy`。
  - `target_position < current_position` 才能是 `sell`。
  - `target_position == current_position` 应为 `hold`。
- `buy` 必须有正向收益空间：`take_profit` 高于当前可用价格或 PM 明确采用的评估价。
- `buy` 的 `stop_loss` 必须低于当前可用价格或 PM 明确采用的评估价，且风险收益比不能明显不合理。
- `hold` 禁止调用交易工具。
- `sell` 不能因为 `available_shares=0` 或可卖数量不足被改写成 `hold`；可卖限制只影响执行计划和成交结果说明。
- 组合风控明确 `block` 的买入不得实际下单。
- `target_position` 不得突破启用状态下的单股仓位上限。
- `take_profit`、`stop_loss`、`holding_horizon_days`、`decision`、`target_position` 必须与 `report_markdown` 的执行计划保持一致。

不做：

- 不让门禁自动改仓位。
- 不让门禁自动止盈、止损或反向下单。
- 不新增第四类动作，例如 `next_day_sell` 或 `opportunistic_sell`。

### 2. PM 买入/卖出反证检查

优先级：高。

目标：减少追涨杀跌、回本心态、锚定成本和情绪驱动交易。

建议做法：要求 PM 在最终报告中显式说明反证，不新增结构化 schema。

买入前必须回答：

- 这次买入最可能失败的路径是什么。
- 当前价格是否已经充分反映利好。
- 如果买错，最早哪个信号能证明错了。
- 是否存在更好的等待条件。

卖出前必须回答：

- 是基本面破坏、趋势失效、估值过高、组合风控，还是短期波动。
- 如果卖错，最可能错过什么。
- 应该减仓、清仓，还是等待可卖数量释放后执行。

### 3. Memory 召回更聚焦

优先级：中。

目标：让 PM 在关键交易场景召回相似经验，而不是泛泛检索历史。

适合触发 recall 的场景：

- 当前已有持仓且准备加仓。
- 当前已有持仓且准备卖出或清仓。
- 上一轮有 PM 决策但未成交，本轮考虑继续执行。
- 当前止盈、止损或持有周期设计与历史场景相似。
- 当前行业、交易频率、交易策略和历史记忆高度相似。

召回要求：query 应包含真实股票名、股票代码、交易频率、交易策略和当前关键变量，不写“查一下历史经验”这类宽泛请求。

### 4. 真实 Debate smoke 和人工审计

优先级：高。

目标：在继续扩大改动前，确认现有闭环是否被 PM 正确使用。

每轮 smoke 至少检查：

- PM 是否正确读取当前持仓、可卖数量、成本、浮盈浮亏和当前仓位。
- PM 是否正确读取上一轮 `execution_summary`。
- 无成交是否被误认为已经建仓。
- `decision` 与 `target_position` 是否相对当前仓位一致。
- `take_profit` 是否具备正向收益空间。
- `stop_loss` 是否可执行。
- 买入或卖出时是否按要求调用交易工具。
- 复盘写入 Memory 是否带时间，并包含退出设计评价。

## 后续暂不建议做

- 不新增更多持仓字段；当前 `total_shares`、`available_shares`、`avg_cost`、`profit_loss`、`profit_loss_pct` 和 `current_position` 已足够。
- 不把 `purchase_details.ledger` 直接塞给 PM，避免底层账本噪声干扰判断。
- 不新增自动止盈、自动止损或后台自动调仓。
- 不把周期性经验复盘结论直接塞进 PM 输入。
- 不把退出设计评价结构化；当前由经验复盘师写入 Memory 即可，除非后续要做统计报表。
- 不为 prompt 文案新增 pytest 或把 prompt 字符串断言塞进既有测试。
