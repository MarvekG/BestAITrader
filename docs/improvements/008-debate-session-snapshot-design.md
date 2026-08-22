# Debate 会话内存决策快照最小设计

**版本**: v1.3
**日期**: 2026-08-22
**状态**: 待实施
**优先级**: P0

## 1. 目标

本版本只解决单次 Debate 内的输入一致性问题：组合概览、目标标的行情、PM 仓位计算和同轮 Agent 都使用 Debate 启动时捕获的同一份内存快照。

本版本不追求历史审计、历史复盘或跨日比较，快照只服务当前工作流的运行时一致性。

这里的“启动时”指每个 Debate 会话进入 `fetch_context` 节点时，不是应用进程启动时，也不是所有会话共享一份全局快照。

本版本不改变现有 LLM 输出结构，不改变现有 Markdown 报告协议，也不把事实仲裁改成结构化 JSON。

## 2. 方案结论

当前阶段不新增数据库表、不新增 Alembic migration、不修改 PM 或订单模型。

快照只存在于当前 LangGraph 工作流内：

1. `fetch_context` 第一次执行时读取数据库并创建不可变 `DecisionSnapshot`。
2. 快照对象写入 `AnalystState.decision_snapshot`。
3. 快照重建现有 `portfolio_info` 和 `data.portfolio.overview` 中的组合字段，并覆盖 `data.realtime.market` 的目标标的行情，不新增上下文字段。
4. PM 仓位工具和 PM 待成交订单上下文从同一个 state 注入快照，不再重新读取决策敏感数据。
5. 工作流结束后快照随进程内对象释放，不作为独立快照进入 API、数据库或历史记录；原有上下文按既有逻辑处理。

如果未来需要跨进程恢复或并发会话去重，再单独设计持久化状态；本版本不提前为这些需求增加数据库结构。

## 3. 明确不做的事情

- 不修改任何 Agent 的 `get_output_model()`。
- `FactArbitrationAgent` 继续返回 Markdown 字符串，不新增 `FactArbitrationResult`、claim ID 或结构化账本。
- 不修改 prompt 的最终输出格式，不要求 LLM 输出额外 JSON 字段。
- 不新增或修改 PM 工具的 LLM 可见参数；`save_pm_decision` 和 `calculate_executable_position_plan` 保持现有参数。
- 不修改 `PMDecisionRecord`、`Order` 和 `Position` 的字段。
- 不实施 PM 跨字段 validator、订单快照绑定、计划哈希、下单前陈旧校验、纪律回填和限价撮合改造。
- 不实施结构化事实采纳、持久化快照和跨日 Delta。
- 不重构整个 `AIContextService` Provider 体系；只在 `fetch_context` 覆盖决策敏感字段。

## 4. 当前接线点

当前 Debate 是函数式 LangGraph 工作流，不新增平行编排入口：

| 职责 | 当前接线点 | 最小修改 |
| --- | --- | --- |
| 初始状态 | `backend/app/ai/llm_engine/runner.py:_build_initial_state` | 增加内部快照字段并初始化为空 |
| 会话上下文 | `backend/app/ai/llm_engine/orchestrator.py:fetch_context` | 创建或复用内存快照，覆盖决策敏感输入 |
| PM 仓位工具 | `backend/app/ai/llm_engine/agents/governance.py` | 有快照时调用纯计算函数 |
| 仓位计算 | `backend/app/ai/llm_engine/position_plan_service.py` | 增加从快照计算的内部接口 |

所有代码继续使用当前 `app...` 导入路径。

## 5. 生命周期和不变量

### 5.1 生命周期

1. `fetch_context` 读取 `state.get("decision_snapshot")`。
2. 如果已有快照，直接复用，不重新查询账户、持仓、行情或挂单。
3. 如果没有快照，调用 `build_decision_snapshot(session_id=...)` 创建一次。
4. 快照创建完成后才允许进入研究 Agent 和 PM 节点。
5. LLM 调用期间不持有创建快照时的数据库会话。

没有 `session_id` 时保留当前非交易研究流程；该流程不创建可交易 Debate 快照。

### 5.2 不变量

1. 一次工作流运行最多创建一份内存快照。
2. 同一工作流内所有 Agent 和 PM 仓位工具使用同一个 `snapshot_id`。
3. 快照对象创建后不可变，不提供更新函数。
4. 快照只保证当前工作流内的一致性，不保证同一 `session_id` 的两个并发工作流使用同一份快照。
5. 应用重启或任务重试到新工作流不会保留该快照。
6. 真实下单仍沿用现有 `TradingService` / `TradingEngine` 的最终校验；本版本不宣称下单时仍然新鲜。

如果业务以后要求第 4、5 条也成立，必须新增持久化快照和并发协调机制，不能靠进程内全局变量补足。

## 6. 内存快照对象

新增 `backend/app/ai/llm_engine/decision_snapshot.py`。该模块不包含 ORM 模型，不写数据库，只负责读取和组装快照。

```python
@dataclass(frozen=True)
class DecisionSnapshot:
    snapshot_id: UUID
    session_id: UUID
    user_id: int
    stock_code: str
    generated_at: datetime
    reference_price: Decimal
    price_source: str
    price_as_of: datetime | date | None
    account_total_assets: Decimal
    available_cash: Decimal
    portfolio_market_value: Decimal
    position: PositionSnapshot
    pending_buy_shares: int
    pending_sell_shares: int
```

`PositionSnapshot` 至少包含持仓标的、总股数、按现有 `TradingEngine.derive_share_fields()` 推导的可卖/冻结股数、均价、市值、浮动盈亏、当前仓位和旧止损兼容字段。

快照还持有同一读取版本的全部账户持仓、待成交订单和目标标的实时行情字段，用于重建现有组合概览、PM 挂单摘要和 `data.realtime.market`，避免先后两次读取产生不同的资产、权重、挂单或参考价。

`snapshot_id` 只作为本次工作流的进程内调试标识，可写入运行日志；不进入 Prompt、API 或历史记录。

模块只提供一个数据库读取接口：

```python
async def build_decision_snapshot(*, session_id: UUID) -> DecisionSnapshot:
    """在一次一致性读取中构建本轮 Debate 快照。"""
```

不提供 `get_decision_snapshot`，因为本版本没有持久化快照可供跨会话读取。

## 7. 快照读取规则

`build_decision_snapshot` 的数据库读取使用一个短事务：

1. PostgreSQL 显式使用 `REPEATABLE READ, READ ONLY`；测试数据库使用其等价的单事务读取。
2. 在同一事务中读取会话、账户、账户全部持仓、目标持仓、待成交订单、目标标的行情和各持仓估值价格。
3. 参考价优先使用有效实时行情，其次使用最新有效日线收盘价。
4. 不得使用 `Position.current_price` 冒充实时行情。
5. 动态资产在同一读取版本中计算，不直接把持久化的 `Account.total_assets` 当作最终分母。
6. 没有会话、账户或有效参考价时返回稳定错误码并终止可交易 Debate。
7. 请求标的与会话标的不一致时返回 `snapshot_stock_code_mismatch`，不得混用两只股票的研究和交易数据。
8. 已存在的内存快照必须属于当前 `session_id`；不一致时返回 `snapshot_session_mismatch`，不得跨会话复用。

快照只包含决策敏感字段和来源，不复制完整研究上下文，不保存 LLM 报告。

## 8. LangGraph 接线

### 8.1 状态字段

修改 `AnalystState` 和 `_build_initial_state`：

```text
decision_snapshot: DecisionSnapshot | None
```

这是工作流内部字段，不是 LLM 输出字段，也不要求前端新增报告字段。

### 8.2 `fetch_context`

存在 `session_id` 时顺序固定为：

1. 创建或复用 `state.decision_snapshot`。
2. 调用现有 `AIContextService` 构建研究上下文。
3. 从快照重建 `portfolio_info` 和 `data.portfolio.overview`：资产、现金、持仓、权重、行业配置、风险汇总和排名都使用同一份快照。
4. 用快照时刻的行情覆盖 `data.realtime.market`；由后续读取价格派生的实时摘要标记为 `stale`，不得混入另一份参考价。
5. 将同一个 `static_context` 传给现有研究 Agent、事实仲裁 Agent 和 PM。

现有上下文的其他研究数据保持原结构。组合绩效摘要依赖另一份账户读取时标记为 `stale`，不得与快照组合口径混用。Provider 仍可读取自己的研究数据，但不得覆盖上述快照字段。不会新增 `static_context.decision_snapshot`，避免快照成为新的 Prompt 或历史记录字段。

## 9. 仓位计划

保留现有 LLM 可见工具接口：

```python
calculate_executable_position_plan(target_position)
```

在 `position_plan_service.py` 增加纯函数：

```python
def build_executable_position_plan_from_snapshot(
    snapshot: DecisionSnapshot,
    target_position: float,
) -> dict[str, Any]:
    """只基于固定内存快照计算整手数量、实际目标仓位和可执行性。"""
```

PM 工具闭包从 `self.state["decision_snapshot"]` 注入快照，调用上述纯函数。没有快照时保留现有实现，供旧的非 Debate 调用继续使用。

计划计算不得重新查询账户、持仓、行情或待成交订单。计划结果沿用当前字段和错误原因，不增加 LLM 必须理解的新参数。

本版本不修改 `TradingService` 和 `TradingEngine` 的下单逻辑。

## 10. LLM 输出结构保护

实施 PR 必须增加回归断言：

- 所有现有 Agent 的 `get_output_model()` 结果不变。
- `FactArbitrationAgent.get_output_model()` 仍为 `str`。
- PM 最终报告仍为 Markdown 字符串。
- `save_pm_decision` 的工具参数集合不变。
- `calculate_executable_position_plan` 对 LLM 仍只暴露 `target_position`。
- `debate_messages.reasoning` 仍保存原始 Markdown 文本。

内存快照不增加 Agent 输入字段，只覆盖现有组合字段，不要求 LLM 在最终结果中新增字段。

## 11. 文件清单

| 文件 | 修改 |
| --- | --- |
| `backend/app/ai/llm_engine/decision_snapshot.py` | 新增内存快照对象和一次性读取 |
| `backend/app/ai/llm_engine/runner.py` | 初始化内部快照字段 |
| `backend/app/ai/llm_engine/orchestrator.py` | `AnalystState` 和 `fetch_context` 接线 |
| `backend/app/ai/llm_engine/position_plan_service.py` | 增加从内存快照计算计划的纯函数 |
| `backend/app/ai/llm_engine/agents/governance.py` | PM 仓位工具注入快照，保持工具签名 |
| `backend/tests/test_decision_snapshot.py` | 快照读取、不可变性和上下文覆盖测试 |
| `backend/tests/test_trade_tool.py` | 现有工具参数和旧路径回归测试 |
| `backend/tests/test_debate_engine.py` | 工作流快照接线和 LLM 输出兼容测试 |

本版本明确不新增或修改：

- `backend/app/models/*.py`
- `backend/alembic/versions/*.py`
- `backend/app/ai/llm_engine/agents/strategic.py`
- `backend/app/ai/llm_engine/agents/base.py`
- `backend/app/ai/llm_engine/prompts/templates.py`
- `backend/app/trading/service.py`
- `backend/app/models/pm_decision.py`
- `backend/app/models/order.py`

## 12. 测试与验收

所有测试使用本地数据库 fixture、mock 行情和 mock LLM，不访问真实外部服务。

### 12.1 内存快照

- 有效会话、账户和行情可以创建一份内存快照。
- 同一个 state 重复进入 `fetch_context` 时复用原快照，不重新查询决策敏感数据。
- 快照创建后修改行情、账户或挂单，快照字段和仓位计划不变。
- 没有会话、账户或有效参考价时返回稳定错误码。
- 动态资产、完整组合概览、PM Context 和仓位计划使用同一份快照分母。
- 持仓可卖数量与既有 `TradingEngine.derive_share_fields()` 口径一致。
- 快照创建后出现的新实时行情不会覆盖 `data.realtime.market.price`；价格派生实时摘要明确标记为 `stale`。
- PM 运行时待成交订单与快照时刻一致，不在 PM 节点重新读取实时挂单。
- 会话标的与启动请求标的不一致时工作流以稳定错误码失败。
- 将其他会话的快照注入当前工作流时以稳定错误码失败。
- 快照只存在于当前工作流，不写入数据库。

### 12.2 LLM 输出兼容

- 现有分析 Agent 输出仍为原类型。
- 事实仲裁仍返回 Markdown 字符串。
- PM 最终结果仍为 Markdown 字符串。
- 既有 Markdown 持久化和前端展示测试继续通过。
- 现有 PM 工具和仓位工具的 LLM 可见参数集合不变。

## 13. 后续演进条件

只有出现以下实际运行时需求时，才重新评估是否需要持久化状态：

- 任务重启后必须恢复原决策输入；
- 订单执行需要跨进程验证快照陈旧性；
- 同一会话的并发启动必须选出唯一胜者。

届时新增表、PM/订单关联和迁移脚本应作为独立设计和独立 PR，不在本版本预先实现。
