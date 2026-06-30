# 聊天式 Deep Research 选股设计

本模块提供一个类似 opencode 的单聊天窗口选股研究流。用户通过同一个输入框提交初始需求、确认计划、追加约束、回答追问或取消任务；前端以 run 和 messages 为唯一展示数据，不展示 Artifacts、独立结果页或工作台。

## 目标

- 单聊天流承载计划、工具调用、追问、进度和最终结果。
- LLM 根据聊天上下文和非交易工具证据决定研究方向，不预构造基础股票池、候选列表或本地评分。
- 运行中用户输入不打断当前工具调用；消息先排队，工具结束后的安全点进入下一轮上下文。
- 需要用户确认时，由当轮 LLM 输出决定提问，workflow 停在 `awaiting_user_input`，用户回答后继续。
- 后端复用现有全局 WebSocket 连接，通过 `interactive_stock_picker_update` 通道推送交互式选股事件。
- 前端收到 `domain=interactive_research` 的推送后刷新当前 run 和 messages；轮询保留为兜底。

## 不做

- 不下单，不生成组合权重或调仓计划。
- 不复用固定本地候选池、搜索策略或评分管线。
- 不提供 `/result`、`/artifacts`、`revise`、`resume` API。
- 不保存完整 LLM 上下文或外部工具大 payload。
- 不允许调用交易、订单、账户、组合或仓位操作工具。

## 持久化

只保留两张表：

| 表 | 用途 |
| --- | --- |
| `stock_picker_interactive.research_runs` | run 状态、标题、原始需求、pending message、checkpoint、错误和时间戳。 |
| `stock_picker_interactive.research_messages` | 用户、assistant、system、tool 消息流，按 `sequence_no` 渲染。 |

`checkpoint_payload` 保存最小恢复状态，例如 `parsed_requirement`、`plan_payload`、`answer_message_id`、最后工具摘要和排队消息 ID。最终结果直接写在 `final_result` 消息 payload 中。

## LLM 流程控制

计划阶段和研究阶段的无工具响应都使用首行动作协议，不使用 JSON：

```text
ACTION: CONTINUE|ASK|DONE
给用户看的内容
```

字段含义：

- `ACTION: CONTINUE`：继续当前阶段。计划阶段表示更新计划；研究阶段表示写一条过程说明并继续下一轮。
- `ACTION: ASK`：向用户提问并暂停。正文就是问题内容。
- `ACTION: DONE`：当前阶段完成。计划阶段表示可以开始研究；研究阶段表示最终答案完成。

工具调用不属于这个文本协议。研究阶段继续使用 LangChain 原生 `bind_tools()` / `tool_calls` 机制：

- 如果 LLM 返回原生 `tool_calls`，后端优先执行工具，不解析 `ACTION`。
- 只有当本轮没有原生 `tool_calls` 且没有 invalid tool calls 时，后端才解析 `ACTION`。
- 计划阶段不绑定工具，LLM 只允许返回首行动作协议。
- 如果 LLM 未按协议输出，后端最多追加 2 次纠错提示要求重输。
- 如果工具循环达到预算，后端要求 LLM 停止工具调用并用 `ACTION: DONE` 输出最终答案。
- 计划中的 `research_budget.max_tool_calls` 会被当前实现上限 `MAX_INTERACTIVE_RESEARCH_ITERATIONS = 8` 截断，因此单次后台执行最多 8 轮 agent/tool 循环。

## 状态

Run 状态：

- `awaiting_plan_approval`：计划确认阶段。run 创建后直接进入该状态；用户可在同一聊天框继续补充要求，多轮迭代 `plan_card`，也可 approve 或 cancel。
- `researching`：后台 Agent 研究阶段。approve 后进入该状态；LLM 使用可绑定的非交易工具执行 tool-calling loop。
- `awaiting_user_input`：后台 Agent 已释放执行，等待用户回答追问。用户回答后重新进入 `researching` 并启动新的后台执行。
- `synthesizing`：workflow 写入 `final_result` 前后的短暂状态，用于推送最终结果消息；随后同一流程会切到 `completed`。
- `completed`：最终结果已写入 `final_result` 消息。
- `cancelled`：用户取消 run。
- `failed`：后台 workflow 异常失败。

兼容/预留状态：

- `drafting_plan`、`reflecting` 当前只作为状态枚举预留；当前实现不会把 run 主状态切到这些状态。

终态：`completed`、`cancelled`、`failed`。

状态转换：

| 触发 | 起始状态 | 结束状态 | 说明 |
| --- | --- | --- | --- |
| `POST /runs` | 无 | `awaiting_plan_approval` | 写入初始 `user_input`、首个 `plan_card` 和 `plan_payload` checkpoint。 |
| `POST /messages` + LLM `ACTION: CONTINUE` | `awaiting_plan_approval` | `awaiting_plan_approval` | 追加用户输入，合并到 `plan_payload.user_inputs`，再写一个新的 `plan_card`。 |
| `POST /messages` + LLM `ACTION: ASK` | `awaiting_plan_approval` | `awaiting_plan_approval` | 写 `assistant_question`，等待用户在计划阶段继续补充。 |
| `POST /messages` + LLM `ACTION: DONE` | `awaiting_plan_approval` | `researching` | LLM 判断计划阶段结束，写 `system_status`，提交事务后通过 `BackgroundTasks` 启动后台 workflow。 |
| `approve` action | `awaiting_plan_approval` | `researching` | 显式按钮确认计划，直接启动后台 workflow。 |
| `cancel` action | 任意非终态 | `cancelled` | 写入取消系统消息和 checkpoint。 |
| `POST /messages` 运行中输入 | `researching`/`reflecting`/`synthesizing` | 原状态 | 用户消息以 `queued` 状态保存；当前工具调用不中断。 |
| 工具安全点处理 queued 输入 | `researching` | `researching` | workflow 把 queued 消息标记为 `completed`，写系统状态消息，并加入下一轮 LLM 上下文。 |
| LLM 无工具调用且输出 `ACTION: CONTINUE` | `researching` | `researching` | 写 `assistant_text` 过程说明，并提示 LLM 继续研究、调用工具、提问或完成。 |
| LLM 无工具调用且输出 `ACTION: ASK` | `researching` | `awaiting_user_input` | 写 LLM 生成的 `assistant_question`，设置 `pending_message_id`，后台任务返回并释放资源。 |
| 用户回答追问 | `awaiting_user_input` | `researching` | 用户消息挂到 `pending_message_id` 下，清空 pending，写 `answer_message_id` checkpoint，并启动新的后台 workflow。 |
| LLM 无工具调用且输出 `ACTION: DONE` | `researching` | `synthesizing` -> `completed` | 把正文写入 `final_result`，再写完成系统消息。 |
| workflow 异常 | `researching` | `failed` | 写失败系统消息、错误字段和 checkpoint。 |

## 消息类型

| 类型 | 说明 |
| --- | --- |
| `user_input` | 初始需求、追加约束、计划阶段补充、追问回答。 |
| `plan_card` | 轻量研究计划、工具策略、预算和 approve/cancel 动作。 |
| `tool_start` | 工具调用开始。 |
| `tool_result` | 工具调用结果摘要。 |
| `progress_update` | 阶段性说明。 |
| `assistant_text` | LLM 在研究阶段选择继续但不调用工具时写入的过程说明。 |
| `assistant_question` | workflow 需要用户确认时写入的问题。 |
| `final_result` | 最终 LLM-driven 结果 payload。 |
| `system_status` | 状态变化、取消、失败等系统消息。 |

## API

挂载路径：`/api/v1/ai-stock-picker/interactive`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/runs` | 创建 run，写入 `user_input` 和 `plan_card`。 |
| `GET` | `/runs` | 列出当前用户 run。 |
| `GET` | `/runs/{run_id}` | 获取 run 摘要。 |
| `GET` | `/runs/{run_id}/messages` | 获取聊天消息流。 |
| `POST` | `/runs/{run_id}/messages` | 追加用户消息；可用于回答追问或运行中排队输入。 |
| `POST` | `/runs/{run_id}/actions` | 仅支持 `approve` 和 `cancel`。 |

除上述接口外，不提供交互式研究专用 WebSocket、`/result` 或 `/artifacts`。

## 端到端流程

1. 用户创建 run。
   - 前端在没有 active run 时调用 `POST /runs`。
   - 后端只做轻量解析和计划生成，不构造股票池、候选列表或本地评分。
   - 数据库写入一条 `user_input`、一条 `plan_card`，run 状态为 `awaiting_plan_approval`，`checkpoint_payload.plan_payload` 保存当前计划。

2. 用户多轮迭代 plan。
   - 在 `awaiting_plan_approval` 状态下，用户继续在同一聊天框输入补充要求。
   - 后端追加新的 `user_input`，调用计划阶段 LLM。LLM 返回 `ACTION: CONTINUE|ASK|DONE` 首行动作协议。
   - `ACTION: CONTINUE` 时，后端把补充内容写入 `plan_payload.user_inputs`，用动作正文更新计划摘要，再追加新的 `plan_card`。
   - `ACTION: ASK` 时，后端写 `assistant_question`，仍停留在 `awaiting_plan_approval`。
   - `ACTION: DONE` 时，后端认为计划阶段结束，切到 `researching` 并启动后台 workflow。
   - 这不是 `revise` action，也不创建 artifact；消息流里自然保留每一轮 plan 版本。

3. 用户确认或取消 plan。
   - 用户点击 approve/cancel 按钮。
   - approve 只负责把 run 切到 `researching`、写 `plan_approved` checkpoint，并注册 FastAPI `BackgroundTasks`。
   - HTTP 请求不会等待 LLM 或工具执行完成；前端继续轮询 run 和 messages。
   - cancel 直接进入 `cancelled` 终态。

4. 后台 workflow 启动 Agent loop。
   - `execute_workflow_background()` 创建独立 DB session，读取 run 和已确认的 `plan_payload`。
   - `workflow.execute()` 先处理历史 queued 用户输入，再把 run 标记为 `researching`，构造 LLM 上下文。
   - 工具来自 `tool_registry.py`：普通 agentic tools、Skills Loader tools、MCP tools；过滤交易、订单、账户、组合、仓位相关工具；不加载 Memory tools。
   - LLM 通过 `bind_tools()` 选择工具；每次工具调用写 `tool_start`，工具返回后写 `tool_result`，结果以 `ToolMessage` 放回 LLM 上下文。
   - 每个工具结果后写一条 `progress_update`，并在工具安全点处理 queued 用户输入。
   - 如果本轮没有原生 `tool_calls`，后端解析首行动作协议决定继续说明、提问暂停或完成。

5. 运行中用户继续输入。
   - 如果 run 处于 `researching`、`reflecting` 或 `synthesizing`，`POST /messages` 不打断当前工具调用。
   - 用户消息以 `status=queued` 保存，并写入 checkpoint。
   - workflow 在工具调用结束后的安全点扫描 queued 消息，把它们标记为 `completed`，写入一条 `system_status` 说明“New user input was appended...”，并作为 `Additional user input: ...` 加入下一轮 LLM 上下文。
   - 运行中追加要求是否导致暂停提问仍由后续 LLM 自主决定；系统不硬编码强制 checkpoint。

6. Agent 暂停等待用户输入。
   - 暂停由当轮 LLM 在无工具调用时输出 `ACTION: ASK` 决定。
   - workflow 将动作正文写入 `assistant_question`，设置 `run.pending_message_id`，把 run 切到 `awaiting_user_input`，写 checkpoint 后直接返回。
   - 暂停不是协程挂起，也不是后台任务 sleep；后台任务已经结束，状态由数据库持久化。

7. 用户回答追问后继续。
   - `awaiting_user_input` 下用户发送消息时，后端把该 `user_input.parent_message_id` 设为 `run.pending_message_id`。
   - 后端清空 `pending_message_id`，写入 `answer_message_id` checkpoint，把 run 切回 `researching`，再注册一个新的后台 workflow。
   - 新 workflow 从消息流和 checkpoint 重新构造上下文继续执行。

8. 研究完成或失败。
   - LLM 不再请求工具且输出 `ACTION: DONE` 时，workflow 将正文写为 `final_result`，payload 中包含 `answer_markdown`、`tool_trace` 和 `selection_mode=llm_driven`，run 随后进入 `completed`。
   - 如果达到工具循环预算，workflow 会要求 LLM 基于已收集证据停止调用工具并以 `ACTION: DONE` 产出最终答案。
   - 如果 workflow 抛出异常，service 写入 `failed` 状态、错误消息和失败 checkpoint。

9. 实时推送。
   - 后台 workflow 每写入关键消息后提交当前事务，并通过 `ws_manager.send_interactive_stock_picker_update()` 推送。
   - 推送 payload 中 `domain=interactive_research`，并包含本次 `message` 和当前 `run` 摘要。
   - 前端订阅 `interactive_stock_picker_update`，收到当前 run 的 interactive 事件后立即静默刷新消息流，方便用户及时看到 `assistant_question` 并输入。

## 前端

前端 `InteractiveResearchTab` 只展示一个聊天卡片：

- 顶部选择历史 run 和刷新按钮。
- 消息列表按 `sequence_no` 渲染不同消息类型。
- 底部输入框在没有 run 时创建 run，在已有非终态 run 时追加消息。
- 计划阶段展示最新 `plan_card` 的 approve/cancel 按钮，同时允许用户继续输入补充要求迭代 plan。
- active run 通过轮询 run 和 messages 刷新。

## 安全边界

- 所有接口必须鉴权。
- 用户输入只代表需求和偏好，不作为市场事实。
- 最终结果必须保持研究建议属性，不执行交易。
- 工具证据只保存摘要和必要引用，不落完整外部 payload。
