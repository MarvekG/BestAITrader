# MCP 接入功能设计

## 1. 目标与边界

本文设计天枢智投接入 MCP（Model Context Protocol）的后端运行时、管理 API、前端配置和安全边界。目标是让外部 MCP Server 提供的工具可以被现有 Agent 统一发现、绑定和调用，用于投研补证、公告检索、数据查询、浏览器自动化或内部知识库访问等场景。

MCP 接入不替代现有内置工具、新闻插件和 Skills Loader：

- 内置工具继续承载核心 A 股数据、交易、PDF、浏览器、Python 沙箱能力。
- 新闻插件继续用于明确来源的新闻检索，不把新闻源改造成 MCP 插件。
- Skills Loader 继续用于本地说明书、reference 和脚本型扩展。
- MCP 只负责接入外部标准化工具服务，并通过统一安全策略进入 Agent 工具池。

v1 不做自动发现局域网服务、不内置公开 MCP 市场、不允许模型动态新增 MCP Server。MCP Server 只能由已鉴权用户在运行时扩展入口中显式配置，且必须受 `ENABLE_RUNTIME_EXTENSIONS` 控制。

## 2. 现有接入点

当前 Agent 工具链路具备良好的统一入口：

| 模块 | 现状 | MCP 接入方式 |
| --- | --- | --- |
| `backend/app/ai/agentic/tools.py` | `get_all_tools()` 返回内置 LangChain tools | 追加 MCP 动态工具，或新增聚合函数统一返回 |
| `backend/app/ai/agentic/skills_loader/runtime.py` | 生成 Skills prompt 和 loader tools | 参考其 catalog prompt 模式生成 MCP 工具说明 |
| `backend/app/ai/llm_engine/agents/base.py` | Debate Agent 默认绑定内置工具、Memory tools、Skills tools | 不改工具调用循环，只扩展工具列表 |
| `backend/app/ai/stock_analysis/runner.py` | 单股分析绑定固定工具和 Skills tools | 在工具装配层按需引入 MCP |
| `backend/app/ai/stock_picker/service.py`、`backend/app/ai/experience/workflow.py` | 自主研究和复盘也绑定同类工具 | 统一复用 MCP runtime |
| `backend/app/api/__init__.py` | `ENABLE_RUNTIME_EXTENSIONS` 下挂载 news plugins 和 skills | 新增 `/api/v1/mcp`，同样要求登录 |

因此 MCP 不需要侵入各业务 Agent 的 tool-call 执行循环，只需要把远端 MCP tool 包装成 LangChain tool，并在工具装配层接入。

## 3. 总体架构

```text
Frontend Settings
  -> /api/v1/mcp
    -> MCP Server Registry
      -> langchain-mcp-adapters MultiServerMCPClient
        -> list_tools / call_tool
          -> Agent bind_tools(...)
```

新增后端包：`backend/app/ai/agentic/mcp/`。

建议文件划分：

| 文件 | 职责 |
| --- | --- |
| `models.py` | MCP Server 配置、创建/更新请求和工具调用请求 schema |
| `registry.py` | 读取、保存、校验 MCP Server 配置 |
| `runtime.py` | 通过官方 adapter 对 Agent 暴露 `get_mcp_tools()`、`build_mcp_catalog_prompt()`、工具列表和试调用 |
| `api.py` 或 `backend/app/api/endpoints/mcp.py` | 管理 API：增删改查、测试连接、列工具、试调用 |

依赖使用官方 `langchain-mcp-adapters`。如果引入新依赖，需要加入后端依赖清单，并在 Docker 构建中安装；不在运行时自动安装 MCP 依赖。

## 4. 配置模型

MCP Server 配置存储在系统配置表 `system_settings`，使用全局 key `mcp.servers`，不再写运行时 JSON 文件。

当系统配置不存在时，系统预置一个默认禁用的 HTTP MCP Server：`name=网页抓取`、`url=http://scrapling.mcp:8765/mcp`。默认只允许 Scrapling 的 3 个基础网页抓取工具：`fetch`、`get`、`stealthy_fetch`。用户删除或保存配置后，以系统配置为准，不在每次启动时反复重建默认项。

配置字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `name` | string | 唯一名称，非空且最长 64 字符，允许中文 |
| `enabled` | bool | 是否进入 Agent 工具池 |
| `url` | string | MCP HTTP Server 绝对 URL，只接受 `http://` 或 `https://` |
| `token` | string | 可选鉴权 token，只写入配置文件，不在管理 API 列表中回显 |
| `allowed_tools` | list[string] | 提交前通过工具预览获得并选择的工具名；只有选中的工具会暴露给 Agent |

配置安全策略：

- MCP 配置存储在 `system_settings`，管理 API 不回显 `token`。
- v1 只支持 HTTP MCP Server，不支持本地 stdio 命令。
- 前端新增/编辑必须先用 `url` 和可选 `token` 刷新可用工具，并选择至少一个工具后才能保存。

## 5. 工具命名与 Schema 映射

MCP tool 名称需要转换成 LangChain tool 名称，避免跨 server 冲突：

```text
mcp__{name}__{tool_name}
```

示例：`mcp__local_docs__search`。

映射规则由 `langchain-mcp-adapters` 负责，项目侧不维护 JSON Schema 转换、LangChain `StructuredTool` 封装或 MCP SDK session/call_tool 适配逻辑。单次输出大小后续应复用或对齐现有 `tool_output_summarizer`，避免大型工具结果塞爆上下文。

## 6. Agent 接入策略

新增 `runtime.py` 对外提供：

```python
async def get_mcp_tools() -> list[Any]:
    """返回已启用 MCP Server 暴露的 MCP LangChain 工具。"""

def build_mcp_catalog_prompt() -> str:
    """生成当前可用 MCP Server 摘要，注入系统提示词。"""
```

推荐接入点：

- Debate：在异步运行入口里 `await get_mcp_tools()`，再执行 `bind_tools()`。
- 单股分析：在 `run_single_stock_analysis()` 中异步追加 `await get_mcp_tools()`。
- AI 选股：在 LLM 深研异步流程中追加 `await get_mcp_tools()`。
- 经验复盘：默认先不接入 MCP，除非后续明确需要复盘访问外部系统。
- LLM 测试页：新增 `mcp`、`tools_and_mcp`、`skills_and_mcp` 测试场景，便于验证真实模型 tool-call。

MCP 配置不做 Agent 场景级筛选。Server 只有 `enabled` 开关：启用后进入 MCP 工具池，接入 MCP 的 Agent 都可获得该 Server 的工具；禁用后不进入工具池。

Prompt 规则需要明确：

- MCP 工具属于外部系统，结果必须标注来源和工具名。
- MCP 结果与内置数据库冲突时，不得静默覆盖，应在报告中说明冲突。
- 不允许通过 MCP 工具下单；交易仍必须走 `TradingService` 和 PM 专用交易工具。

## 7. 管理 API

新增路由：`/api/v1/mcp`，只在 `ENABLE_RUNTIME_EXTENSIONS=true` 时挂载，并复用 `get_current_user` 鉴权。

API 草案：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/v1/mcp/servers` | 列出 MCP Server 完整配置 |
| `POST` | `/api/v1/mcp/servers` | 新增 Server 配置 |
| `POST` | `/api/v1/mcp/tools/preview` | 用未保存的 url/token 预览可用 MCP tools |
| `PUT` | `/api/v1/mcp/servers/{name}` | 更新 Server 配置 |
| `DELETE` | `/api/v1/mcp/servers/{name}` | 删除 Server 配置 |
| `POST` | `/api/v1/mcp/servers/{name}/test` | 测试连接并返回 server info |
| `GET` | `/api/v1/mcp/servers/{name}/tools` | 通过官方 adapter 拉取 MCP tools 列表 |
| `POST` | `/api/v1/mcp/servers/{name}/tools/{tool_name}/invoke` | 管理页试调用，要求显式参数 |
| `GET` | `/api/v1/mcp/prompt` | 返回当前 MCP catalog prompt |

错误返回统一使用 `status: error` 和 `message`，保持 skills/news plugins 管理接口风格。

## 8. 前端设计

前端优先放在现有 `SettingsPage`，新增 `MCP` tab，而不是新增独立页面。

新增 API 文件：`frontend/src/api/mcp.ts`。

页面能力：

- Server 列表：name、enabled、url、已选择工具、最近连接状态。
- 新增/编辑抽屉：填写 name、enabled、url、可选 token；刷新工具后选择允许暴露的工具。
- 连接测试：调用 `/test`，展示 server info、耗时、错误堆栈摘要。
- 工具试调用：根据 JSON schema 提供 JSON 参数编辑器，显示结构化结果。
- Prompt 预览：复用 Skills prompt 预览模式，展示注入 Agent 的 MCP catalog。

交互上必须明显提示 MCP Server 可以访问外部系统，只有可信 server 才应启用。

## 9. 安全与风控

MCP 是高风险扩展面，v1 必须默认保守：

1. **鉴权边界**：只在已登录业务路由下开放；继续受 `ENABLE_RUNTIME_EXTENSIONS` 控制。
2. **URL 安全**：只接受 `http://` 或 `https://` 绝对 URL；生产环境可增加内网/公网 denylist。
3. **工具暴露**：新 server 默认 `enabled=false`，启用后该 server 的全部工具会暴露给接入 MCP 的 Agent。
4. **最小配置**：不做 Agent 场景筛选或工具级 allowlist/denylist，避免运行时配置系统过度复杂。
5. **输出控制**：大结果截断或摘要；工具错误不得被模型当成成功事实。
6. **审计日志**：记录 name、tool_name、user_id、耗时、成功/失败。
7. **交易隔离**：禁止 MCP 工具绕过 PM 和 `TradingService` 下单；与交易相关的 MCP tool 默认不允许进入工具池。

## 10. 数据与状态管理

v1 推荐文件存储，理由是当前 runtime extensions 已以文件系统管理外部能力为主，且 MCP Server 属于部署级配置，不是核心业务数据。

运行时状态：

- Server 配置：持久化到 `system_settings` 全局 key `mcp.servers`。
- 调用审计：先写结构化日志；如需要前端历史，再新增 DB 表，不在 v1 强行落表。

## 11. 实施步骤

### Phase 1：后端最小闭环

1. 新增 MCP runtime 包和配置 registry。
2. 基于 `langchain-mcp-adapters` 实现 HTTP 连接测试、list tools、call tool。
3. 保持项目侧 runtime 薄封装，不自建 LangChain tool adapter。
4. 新增 `/api/v1/mcp` 管理 API，并挂到 `ENABLE_RUNTIME_EXTENSIONS` 下。
5. 在 LLM 测试端点增加 MCP 测试场景，先不接入真实 Debate。

### Phase 2：Agent 接入

1. 在 Debate base agent 工具列表追加 MCP tools。
2. 在单股分析和 AI 选股深研工具列表追加 MCP tools。
3. 注入 MCP catalog prompt，说明工具来源、冲突处理和交易边界。
4. 对工具输出加入大小控制和错误结构化。

### Phase 3：前端管理

1. 新增 `frontend/src/api/mcp.ts`。
2. 在 `SettingsPage` 增加 MCP tab。
3. 实现 server CRUD、连接测试和 prompt 预览。
4. 增加 AI 功能测试页的 MCP 场景入口。

### Phase 4：增强项

1. 增加更细的 URL 网络边界策略。
2. 增加按用户或角色的管理权限。
3. 增加 MCP 调用历史表和前端审计视图。
4. 增加 URL denylist。

## 12. 测试计划

后端测试：

- registry：name 校验、保存/读取/删除配置。
- URL 校验：拒绝空 URL、非 HTTP scheme 和缺失 host 的 URL。
- runtime：mock 官方 adapter 返回工具和调用结果，禁止访问真实外部服务。
- API：鉴权、`ENABLE_RUNTIME_EXTENSIONS` 挂载行为、CRUD、test、tools、invoke。
- 路由鉴权：更新 `backend/tests/test_api_auth_required.py` 白名单，确保 `/api/v1/mcp/*` 需要登录。

前端验证：

- 运行 `cd frontend && npm run lint && npm run typecheck && npm run build`。
- 手工验证 Settings MCP tab：新增 server、测试连接、拉取工具、prompt 预览、试调用错误展示。

集成验证：

- 用本地 fake HTTP MCP server 暴露一个 `echo` tool。
- 在 LLM 测试页选择 MCP 场景，确认模型能调用 `mcp__fake__echo` 并把结果写入回答。
- 在 Debate/单股分析中启用 fake tool，确认已接入 MCP 的 Agent 能绑定对应工具。

## 13. 风险与取舍

- **只做 HTTP**：配置最小，不允许后端启动本地命令，降低运行时扩展面的命令执行风险。
- **优先文件配置**：符合当前 runtime extensions 风格；如果后续需要多用户隔离或审计，再迁移到 DB。
- **默认不暴露工具**：牺牲一点配置便利性，换取更低的误调用和越权风险。
- **不做自动降级**：MCP server 不可用时应返回明确工具错误，由 Agent 报告信息缺口，不静默改用其他来源。
- **不替代交易链路**：任何 MCP 交易或券商工具都不能绕过现有 PM 决策、风控预检和 `TradingService`。

## 14. 建议优先级

推荐先完成 Phase 1 和 Phase 3 的管理闭环，再把 MCP 接入真实投研 Agent。这样可以先用 fake server 验证安全、schema 和 tool-call 稳定性，避免外部 MCP 问题直接影响 Debate 主流程。
