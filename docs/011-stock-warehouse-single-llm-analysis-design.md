# AI交易分析页单 LLM 投研分析设计

## 背景

当前系统已经有两类 AI 投研入口：

- 股票仓库页：管理股票池、同步数据、发起现有多 Agent AI 分析、配置自动分析。
- AI交易分析页：`frontend/src/pages/SessionManagerPage.tsx`，菜单和页面标题均为“AI交易分析”，当前展示辩论管理列表，并可恢复会话或查看决策审计报告。

现有“开始 AI 分析”会创建会话并进入 Debate 工作流，适合正式多 Agent 辩论链路。用户还需要一个轻量入口：输入自然语言投研问题，让一个 LLM 自主探索信息并直接回答。问题可以指向个股、行业、主题、板块、新股机会或市场事件；股票选择只是可选上下文，不是必填条件。

本设计在“AI交易分析”页面新增 `AI投研分析` Tab。后端使用单个 LLM 绑定股票数据工具、市场工具、网页/新闻/PDF/Python 沙箱工具和外部 skill loader，由 LLM 自主决定探索路径。该链路明确不绑定记忆工具，不读写历史经验记忆。

## 目标

- 在“AI交易分析”页面增加 `AI投研分析` Tab，不在股票仓库页新增按钮。
- Tab 上方支持可选标的/股票输入，用户可以选择股票代码或名称，也可以留空。
- Tab 下方提供主问题输入框，默认问题从 i18n 文案 `session.stock_analysis_default_question` 读取。
- 后端运行一个异步单 LLM 自主研究任务，允许工具调用和 skill 调用。
- LLM 自主判断分析对象，支持个股、行业、主题、板块、新股机会或市场事件。
- 不绑定记忆工具，不召回、不写入用户历史经验。
- 问题长度限制为 100000 字符。
- 工具循环上限为 60 次，并启用与 LLM Debate 相同的工具输出压缩。
- 分析结果以任务结果返回，前端在同一个 Tab 内展示最终 Markdown 报告。
- 刷新页面后，`AI投研分析` Tab 自动拉取当前用户最近一次 `stock_analysis` 任务并恢复展示。
- 不影响现有 Debate 工作流、自动分析调度、模拟交易和记忆系统。

## 非目标

- 不替换现有多 Agent Debate 流程。
- 不改变股票仓库页的现有按钮和操作。
- 不把本次分析结果写入记忆系统。
- 不新增长期分析历史表；首版复用现有 `AsyncTask.result` 保存任务结果。
- 不做流式输出；首版使用异步任务状态轮询或现有任务通知。
- 不做复杂报告编辑、收藏、对比和导出。

## 总体方案

采用异步单 LLM 自主投研任务。

沿用 `stock_analysis` 后端模块、`POST /api/v1/stock-analysis/run` 路由和 `stock_analysis` 任务类型，避免迁移老任务。请求中的 `stock_code` 改为可选：存在时后端标准化并校验股票，缺省或空白时直接把用户问题交给 LLM。任务内部构造单 LLM 工具调用循环，工具集合使用 `get_all_tools()` 和 `get_skills_loader_tools()`，不调用 `build_memory_tools()`。结果写入 `AsyncTask.result`，前端通过任务接口查询并展示。

## 前端设计

### 页面位置

目标页面：`frontend/src/pages/SessionManagerPage.tsx`

页面主体使用 Ant Design `Tabs`：

- Tab 1：`辩论管理`
  - 放入当前已有会话列表、批量归档、批量删除、刷新和搜索功能。
  - 现有行为保持不变。
- Tab 2：`AI投研分析`
  - 新增单 LLM 自主投研入口。
  - 不依赖已有 session。
  - 允许用户可选选择股票，并输入投研问题后提交任务。

### AI投研分析 Tab 布局

Tab 内使用一个表单区和一个结果区，避免再弹出 Modal。

表单区：

- 可选标的/股票：`AutoComplete`
  - 复用 `marketApi.getDbStocks({ query, limit: 20 })`
  - 展示格式：`股票代码 - 股票名称`
  - 选择后保存 `stock_code`
  - 可以留空，留空时由问题决定分析对象
- 用户问题：`Input.TextArea`
  - 默认问题：从 i18n 文案 `session.stock_analysis_default_question` 读取
  - 最大长度：100000 字符
  - 空白输入提交时前端先填入 i18n 默认问题；后端不补默认问题
- 操作按钮：
  - `开始投研分析`
  - 运行中显示 loading，避免重复提交

结果区：

- 待提交：展示空状态。
- 运行中：展示任务 ID、任务状态和 loading。
- 完成：展示 `answer_markdown`。
- 失败：展示 `AsyncTask.error_message`，保留表单内容，允许重新提交。

刷新恢复：

- `AI投研分析` Tab 首次挂载时调用任务列表接口，查询当前用户最近一次 `task_type=stock_analysis` 的任务。
- 如果最近任务状态为 `completed`，直接展示 `AsyncTask.result.answer_markdown`，并回填问题。
- 如果最近任务状态为 `pending` 或 `running`，展示运行态并继续按任务 ID 轮询。
- 如果最近任务状态为 `failed`，展示错误信息和该任务的问题，允许用户重新提交。
- 如果没有历史任务，展示待提交空状态。

### 前端 API

`frontend/src/api/stockAnalysis.ts`：

```ts
export interface StockAnalysisRequest {
  stock_code?: string | null;
  question: string;
}
```

任务状态继续复用 `frontend/src/api/tasks.ts` 的 `tasksApi.getTask(taskId)` 和 `tasksApi.listTasks(...)`。

## 后端设计

### API 设计

`POST /api/v1/stock-analysis/run`

请求示例：

```json
{
  "stock_code": "600519.SH",
  "question": "分析贵州茅台最近基本面和资金面"
}
```

无股票请求示例：

```json
{
  "question": "分析半导体行业最近有没有机会，并推荐几个观察方向"
}
```

校验规则：

- `stock_code` 可选；空白时归一化为 `null`。
- `stock_code` 存在时，标准化后校验股票存在于 `StockBasic`。
- `question` 必填；空白时返回 422，后端不补默认问题。
- `question` 最大 100000 字符。
- 只允许当前登录用户提交任务。

响应：

```json
{
  "task_id": "string",
  "task_name": "AI Research Analysis",
  "status": "pending",
  "message": "Task submitted successfully: task queued",
  "new_task": true
}
```

任务结果写入 `AsyncTask.result`：

```json
{
  "question": "分析半导体行业最近有没有机会",
  "answer_markdown": "# 半导体行业分析\n\n## 结论\n...",
  "tool_trace": [
    {
      "name": "query_market_data",
      "args": {"topic": "半导体"},
      "success": true
    }
  ],
  "model": "backend",
  "completed_at": "2026-05-26T10:00:00"
}
```

任务 `parameters` 保存恢复 UI 所需的最小输入：

```json
{
  "stock_code": null,
  "stock_name": null,
  "question": "分析半导体行业最近有没有机会"
}
```

### LLM 工具边界

绑定工具：

- `get_stock_analysis_tools()` 返回的基础工具列表
  - `execute_python_sandboxed`
  - `browse_web_page_html`
  - `parse_pdf_to_markdown`
  - `search_news`
- `get_skills_loader_tools()`
  - `list_skills`
  - `load_skill`
  - `read_skill_file`
  - `run_skill_script`

明确不绑定：

- `build_memory_tools()`
- `recall_memory`
- `write_memory`
- `query_stock_data`
- `query_market_data`
- `sync_market_data`
- `get_database_schema`
- `query_and_calculate`

### LLM 运行循环

`runner.py` 实现轻量工具循环：

- 使用 `build_chat_model(model=settings.LLM_MODEL, temperature=0.2)`。
- `llm.bind_tools(bound_tools)` 后循环调用。
- 最大工具迭代次数为 60。
- 启用与现有 LLM Debate 相同的工具输出压缩逻辑：工具结果过长时复用 `should_summarize_tool_output()` 和 `summarize_tool_output()`，压缩后的内容再作为 `ToolMessage` 回填给 LLM。
- 每次 LLM 调用通过 `record_llm_usage()` 记录：
  - `workflow="stock_analysis"`
  - `stage="single_llm_analysis"`
  - `call_kind="agent"` 或 `call_kind="final_no_tools"`
- 达到迭代上限时追加“禁止继续调用工具，基于已有证据输出最终报告”的用户消息，再做一次无工具最终回答。
- 捕获工具异常后以 `ToolMessage` 返回错误，让 LLM 决定是否换路径。

### 系统提示

系统提示核心要求：

- 你是 AI 投研分析助手，目标是回答用户的投研问题。
- 如果有可选股票上下文，应把它作为分析线索。
- 如果用户未指定固定股票，应根据用户问题判断分析对象，可分析个股、行业、主题、板块、新股机会或市场事件。
- 可以自主选择数据库、市场、新闻、网页、PDF、Python 沙箱和外部 skill。
- 如果信息不足，必须说明缺口和不确定性，不得编造。
- 需要输出 Markdown 报告，至少包含：
  - 结论。
  - 关键证据。
  - 主要风险。
  - 后续观察点。
  - 数据缺口。

## 数据流

```text
用户进入“AI交易分析”
  -> 切换到“AI投研分析”Tab
  -> 可选选择股票，输入投研问题
  -> POST /api/v1/stock-analysis/run
  -> 后端按需校验股票并校验问题
  -> task_manager 创建 AsyncTask
  -> async_task_runner 执行 run_stock_analysis_task
  -> 单 LLM 自主调用工具和 skill
  -> task_manager 写入 completed / failed
  -> 前端轮询 /api/v1/tasks/{task_id}
  -> “AI投研分析”Tab 展示 Markdown 结果

刷新页面恢复:
  -> “AI投研分析”Tab 挂载
  -> GET /api/v1/tasks?task_type=stock_analysis&limit=1
  -> 找到最近任务
  -> 根据任务 status 展示 completed / failed / running 状态
  -> running 或 pending 时继续轮询 /api/v1/tasks/{task_id}
```

## 错误处理

- 提供了股票但股票不存在：接口返回 404。
- 未提供股票：不报错，由用户问题决定分析对象。
- 问题超过 100000 字符：接口返回 422。
- LLM 配置缺失或调用失败：任务置为 failed，错误写入 `AsyncTask.error_message`。
- 工具失败：不立刻终止任务，作为工具结果交给 LLM；只有运行循环异常才置为 failed。
- skill 脚本超时：返回工具错误，不中断任务。
- 用户切换 Tab 或离开页面：任务继续运行，可通过全局任务通知和任务状态接口查询结果。
- 刷新页面后没有最近任务：展示待提交空状态，不报错。
- 最近任务缺少 `result.answer_markdown`：展示任务状态和原始错误或空结果提示。

## 权限与安全

- 所有接口使用现有登录鉴权。
- 任务记录写入当前用户 ID，查询任务时继续使用现有用户隔离。
- 不把用户问题拼接进日志正文；如需日志上下文，使用 `extra={...}`。
- 工具链不绑定记忆系统入口。
- 外部 skill 仍受现有 skill loader 的路径、命令和环境变量 allowlist 限制。

## 测试策略

### 后端单元测试

- `StockAnalysisRequest` 允许缺省或空白 `stock_code`。
- `POST /stock-analysis/run` 不传股票时仍能创建投研任务。
- `POST /stock-analysis/run` 空问题时返回 422。
- `POST /stock-analysis/run` 的 `question` 超过 100000 字符时返回 422。
- 股票代码存在但不存在于库中时返回明确错误。
- 提交任务时 `AsyncTask.user_id` 绑定当前用户。
- runner 构造工具列表时包含 `list_skills`，不包含 `recall_memory`、`write_memory`。
- LLM 返回工具调用时能执行工具并把结果追加回消息。
- 工具结果超过压缩阈值时调用与 Debate 相同的工具输出压缩逻辑，并把压缩结果回填为 `ToolMessage`。
- 达到 60 次迭代上限时进入 final no-tools 模式。

### 前端验证

- “AI交易分析”页面显示 `辩论管理` 和 `AI投研分析` 两个 Tab。
- `辩论管理` Tab 保留原有会话列表、批量归档、批量删除、刷新和搜索功能。
- `AI投研分析` Tab 的可选标的输入调用 `marketApi.getDbStocks` 并展示 `代码 - 名称`。
- 不选择股票也可以提交投研问题。
- `AI投研分析` Tab 首次挂载时调用 `tasksApi.listTasks({ task_type: 'stock_analysis', limit: 1 })`。
- 最近任务为 completed 时，刷新页面后展示最近 `answer_markdown`。
- 最近任务为 pending/running 时，刷新页面后进入运行态并继续轮询。
- 最近任务为 failed 时，刷新页面后展示错误信息并允许重新提交。
- 前端默认问题从 i18n 文案 `session.stock_analysis_default_question` 读取，提交给后端后作为普通必填问题处理。
- 提交后调用 `stockAnalysisApi.run`。
- 任务完成后展示 `answer_markdown`。
- 任务失败时展示错误信息。

## 风险与边界

- LLM 自主探索会带来耗时和成本不确定性，因此必须有最大迭代次数。
- skill 脚本和网页检索可能失败，最终报告必须暴露数据缺口。
- 首版结果只在任务结果中保存，不保证形成长期报告库。
- 如果后续需要流式输出，应再设计 WebSocket 或 SSE，不在本阶段混入。
