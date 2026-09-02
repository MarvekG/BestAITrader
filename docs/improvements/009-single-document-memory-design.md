# 单文档记忆系统设计（替换 MemoFlux）

> 状态：已实现（2026-09）。本文档记录一用户一股票一份 Markdown 活文档的记忆系统设计，
> 以及它如何替换此前的 MemoFlux（pgvector 条目式记忆）集成。

## 1. 目标

- 降低 token 成本：记忆相关内容从"每次召回经过两级 LLM 加工并返回多条合成答案"改为"prompt 只注入一行提示，全文按需读取"。
- 简化记忆模型：放弃条目/主题/向量检索模型，一个用户和一只股票锚定一份自由格式的 Markdown 活文档，结构由 LLM 自主演化。
- 保证并发安全：并行辩论 Agent 同时写同一份文档时不允许互相覆盖。
- 控制容量：单文档有硬上限，超限逼迫 LLM 精炼合并，记忆不随时间无限膨胀。

## 2. 方案结论

- 存储采用 PostgreSQL 表 `memory_documents` 模拟文件：`(user_id, stock_code)` 唯一锚定一行，`content` 为整份 Markdown 文档。
- Agent 工具面收敛为两个：
  - `read_memory()`：无参数，自动绑定当前分析股票，返回整份文档 + `version` + `size_chars`。
  - `write_memory(content, importance, base_version)`：整文档替换。`base_version` 必须来自最近一次 `read_memory`。
- 注入策略：仅对启用记忆的角色在上下文末尾追加一行 `MEMORY_DOCUMENT:` 提示（存在性、字符数、版本号），全文不进 prompt，保持按需读取。
- MemoFlux 彻底移除：backend 删除 `memory_client.py` 及全部引用；`memo/` 子模块、docker 服务定义与运行容器一并移除。

## 3. 明确不做的事情

- 不做条目级记忆、主题分文件和索引文件（`MEMORY.md` 式索引被单文档方案取代）。
- 不做向量检索与语义召回；读取是整文档原文，不做 LLM 加工。
- 不新增公开 HTTP 端点和记忆管理 UI；记忆内容通过经验复盘页面的 `written_memories` 摘要间接可见。
- 不迁移旧 MemoFlux 数据；旧数据仅存于宿主机 docker 卷 `bat.memo.pgvector17.data` 与 `bat.memo.runtime.data`，确认放弃后可用 `docker volume rm` 清除。
- 一期不动辩论报告全文级联（PM 一次接收十余份报告全文），这是后续最大的独立 token 优化点。

## 4. 当前接线点

| 接线点 | 位置 | 说明 |
| --- | --- | --- |
| 工具构建 | `backend/app/ai/agentic/memory_tools.py` | `build_memory_tools(state=...)` 入口签名不变，辩论 Agent 与复盘工作流共用 |
| 工具绑定 | `backend/app/ai/llm_engine/agents/base.py` (`get_tools`) | 与全局工具、skills 工具一并绑定 |
| 提示注入 | `backend/app/ai/llm_engine/agents/base.py` (`_build_memory_hint`) | 仅 `MEMORY_ENABLED_AGENT_NAMES` 且已绑定股票时追加一行提示 |
| 门控 | `backend/app/ai/llm_engine/roles.py` (`MEMORY_ENABLED_AGENT_NAMES`) | 与旧方案一致：风控、多空/激进/保守/中性研究员、PM |
| 写入方 | `backend/app/ai/experience/workflow.py` | 复盘工作流按整文档协议重写记忆；`written_memories` 完整保留文档全文（体积受 `MEMORY_DOC_MAX_CHARS` 与事件保留期双重封顶），前端列表折叠展示 |
| 展示索引 | `backend/app/ai/experience/index_service.py` | `experience_indexes.memory_id` 存 `md_<uuid4.hex>`，字段语义兼容 |
| 全局纪律 | `backend/app/ai/llm_engine/prompts/templates.py` | "记忆使用边界"段落描述整文档读写协议 |

## 5. 生命周期和不变量

### 5.1 生命周期

1. 文档不存在：`read_memory` 返回 `exists=false`；`write_memory(content, importance, base_version=0)` 首写自动建行，`version` 置 1。
2. 常规写入：LLM 先 `read_memory` 拿到 `version`，重写全文后作为 `content` 传入；写入成功 `version += 1`。
3. 经验复盘：每次复盘通常一次 `write_memory` 完成更新；复盘时间、周期由 LLM 按协议自行写入文档。

### 5.2 不变量

- `(user_id, stock_code)` 唯一；文档按用户隔离，跨用户不可见。
- `write_memory` 必须携带与当前行一致的 `base_version`（乐观锁用 `UPDATE ... WHERE version = :base_version` 实现，冲突返回最新全文）；`base_version` 非负整数，文档不存在时必须为 0。
- `len(content) <= settings.MEMORY_DOC_MAX_CHARS`（默认 8000，环境变量可调）；空内容拒绝写入。
- 辩论角色对记忆只读或按门控写入；辩论内部写入不得伪造未来后验结果（由 prompt 纪律约束）。

## 6. 数据模型

`memory_documents`（迁移 `8dde1ec39dcf`，`backend/app/models/memory_document.py`）：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | UUID PK | |
| `user_id` | Integer FK users.id CASCADE | 索引 |
| `stock_code` | String(20) | 与 `user_id` 组成唯一约束 |
| `content` | Text | 整份 Markdown 文档 |
| `size_chars` | Integer | 冗余字符数，用于快速回报用量 |
| `version` | Integer | 乐观锁版本号，每次成功写入 +1 |
| `created_at` / `updated_at` | DateTime | |

## 7. 失败语义

| 场景 | 返回 | 期望的 LLM 行为 |
| --- | --- | --- |
| `base_version` 与当前不一致 | `error` + 最新 `content`/`version` | 把新增经验合并进最新全文后用返回版本号重试 |
| 并发首写撞唯一约束 | `error` + 最新 `content`/`version` | 同上；辩论循环的逐工具异常兜底不触发，复盘工作流不中断 |
| 内容超过 `MEMORY_DOC_MAX_CHARS` | `error` + `size_chars`/`max_chars` | 同一轮内精炼合并旧内容后重试，不得直接放弃 |
| 空内容 | `error` | 补充有效内容 |
| 文档不存在但 `base_version != 0` | `error` | 先 `read_memory` 再写入 |
| 数据库异常 | store 记录日志后抛出；提示块构建处降级为空 | 主决策流程不受记忆故障阻塞 |

## 8. 文件清单

新增：

- `backend/app/models/memory_document.py`
- `backend/app/ai/memory_documents/store.py`
- `backend/alembic/versions/8dde1ec39dcf_create_memory_documents.py`
- `backend/tests/test_memory_documents_store.py`

修改：

- `backend/app/ai/agentic/memory_tools.py`（原位重写为整文档工具）
- `backend/app/ai/llm_engine/agents/base.py`（提示注入）
- `backend/app/ai/llm_engine/prompts/templates.py`（记忆边界协议，中英）
- `backend/app/ai/experience/workflow.py`、`backend/app/ai/experience/service.py`（写入协议与 payload 摘要）
- `backend/app/core/config.py`（`MEMORY_DOC_MAX_CHARS`；移除 `MEMORY_SERVICE_*`）
- `backend/app/api/endpoints/llm.py`、`testing.py`、`backend/app/main.py`（移除 MemoFlux 引用）
- `frontend/src/pages/SettingsPage.tsx`、`frontend/src/api/testing.ts`、`frontend/src/api/prompt.ts`（移除记忆测试/预览面板）
- `backend/app/locales/zh.json`、`en.json`（清理停用键，保留经验库仍在用的 `memory_column_memory_id`）

删除：

- `backend/app/ai/memory_client.py`
- `backend/tests/test_memory_client.py`
- `memo/` 子模块（含 `.gitmodules` 登记）及 `docker-compose.yml` / `docker-compose.dev.yml` 中的 `memo`、`memo-postgres` 服务与卷定义；`deploy.py` 的 memo 配置渲染与健康检查；`scripts/database-maintenance.sh` 的 memo 库备份恢复

## 9. 测试与验收

- `tests/test_memory_documents_store.py`：首写建行、版本递增、版本冲突返回最新全文、空内容拒绝、容量上限、跨用户隔离。
- `tests/test_agentic_logic.py`：角色门控、整文档读写透传、`base_version` 校验、importance 校验、工具 docstring 协议断言。
- `tests/test_experience_workflow.py`：复盘系统提示词中英协议断言、`written_memories` 元数据（`version`/`size_chars`/`content_chars`）。
- `tests/test_llm_usage_endpoint.py`：用量统计不再合并 memo。
- 全量后端套件与前端 `lint`/`typecheck`/`build` 门禁通过；`alembic upgrade head` 在 dev 环境验证迁移。

## 10. 后续演进条件

- 若单股文档经常触顶且精炼频繁丢信息，考虑提高 `MEMORY_DOC_MAX_CHARS` 或引入周期性总结压缩。
- 若要降低 PM 一次性接收十余份辩论报告全文的 token 峰值，可复用"索引常驻 + 按需读取"模式做报告级联文件化（二期候选）。
