# 单文档记忆系统设计（替换 MemoFlux 与经验库索引）

> 状态：已实现（2026-09）。本文档记录一用户一股票一份 Markdown 活文档的记忆系统、只读查看接口和前端展示方案。

## 1. 目标

- 降低 token 成本：prompt 只注入记忆存在性提示，全文按需读取。
- 简化记忆模型：一个用户和一只股票锚定一份自由格式的 Markdown 活文档。
- 保证并发安全：并行 Agent 写同一份文档时通过版本锁避免互相覆盖。
- 控制容量：单文档有硬上限，超限时要求 Agent 精炼合并。
- 统一事实来源：复盘经验正文直接从 `memory_documents` 读取，不再维护 `experience_indexes` 二次索引。

## 2. 方案结论

- PostgreSQL 表 `memory_documents` 通过 `(user_id, stock_code)` 唯一锚定一行，`content` 保存整份 Markdown 文档。
- Agent 工具面保持两个：
  - `read_memory()`：无参数，自动绑定当前分析股票，返回全文、版本和字符数。
  - `write_memory(content, importance, base_version)`：整文档替换，`base_version` 必须来自最近一次读取。
- 注入策略：仅对允许使用记忆的角色追加 `MEMORY_DOCUMENT` 提示行，全文不进入初始 prompt。
- 用户查看入口使用只读接口：
  - `GET /api/v1/memory-documents`
  - `GET /api/v1/memory-documents/{stock_code}`
- 经验复盘仍负责生产和写入经验；“记忆文档”Tab 直接展示当前活文档，不再展示经验索引摘要。
- MemoFlux 及其容器、配置和引用已移除。

## 3. 明确不做的事情

- 不做条目级记忆、主题分文件、向量检索和 LLM 二次加工。
- 不在业务库复制经验正文、标签、正确性或复盘摘要。
- 当前查看页不提供手动编辑、删除、版本回溯和空文档创建。
- 列表只按股票代码或名称筛选；不恢复经验库的行业、策略、周期、标签和正确性筛选。
- `version` 只表示当前乐观锁版本，不代表可查询的历史快照；如需版本历史，另行设计版本表。

## 4. 当前接线点

| 接线点 | 位置 | 说明 |
| --- | --- | --- |
| 工具构建 | `backend/app/ai/agentic/memory_tools.py` | `build_memory_tools(state=...)` 绑定当前用户和股票 |
| 存储 | `backend/app/ai/memory_documents/store.py` | 读全文、整文档写入、版本冲突和容量校验 |
| 查看查询 | `backend/app/ai/memory_documents/service.py` | 分页列表、股票名称关联和详情序列化 |
| 查看 API | `backend/app/ai/memory_documents/api.py` | 认证的列表和详情接口 |
| 查看页面 | `frontend/src/pages/experience/MemoryDocumentsPanel.tsx` | 只读列表、Markdown 渲染和原文查看 |
| 页面入口 | `frontend/src/pages/ExperiencePage.tsx` | “经验分析”页内的“记忆文档”Tab |
| 复盘写入 | `backend/app/ai/experience/workflow.py` | 复盘通过 `write_memory` 更新活文档 |
| 复盘审计 | `backend/app/models/experience_review_event.py` | 保留复盘运行、工具轨迹和结果事件 |

## 5. 生命周期和不变量

### 5.1 生命周期

1. 文档不存在：`read_memory` 返回 `exists=false`；首次 `write_memory` 使用 `base_version=0` 建立版本 1。
2. 常规写入：Agent 先读取全文，合并或精炼后整文档替换，成功写入后版本递增。
3. 用户查看：列表只读取元数据；打开详情时读取当前完整 Markdown。查看不会创建或修改文档。

### 5.2 不变量

- `(user_id, stock_code)` 唯一，所有 HTTP 查询都必须带当前用户条件。
- `write_memory` 必须携带与当前行一致的 `base_version`。
- `len(content) <= settings.MEMORY_DOC_MAX_CHARS`，默认 8000；空内容拒绝写入。
- 记忆只作为历史经验和上下文参考，不能覆盖当前行情、财务、新闻或政策事实。
- 查看列表不返回正文，详情返回未经摘要或截断的完整正文。

## 6. 查看接口契约

### 6.1 列表

`GET /api/v1/memory-documents?keyword=&page=1&page_size=20`

`keyword` 匹配股票代码或 `data.stock_basic.name`，结果按 `updated_at DESC, stock_code ASC` 排序。

列表项包含：

```text
id
stock_code
stock_name
size_chars
version
created_at
updated_at
```

不包含 `content`，避免批量加载完整文档。

### 6.2 详情

`GET /api/v1/memory-documents/{stock_code}` 返回列表元数据、`max_chars` 和完整 `content`。文档不存在或属于其他用户时统一返回 404。

股票名称通过 `StockBasic` 外连接查询，仅用于展示，不写回记忆文档表。

## 7. 前端展示

- `ExperiencePage` 保留复盘候选、复盘任务、分析结果和工具轨迹。
- 原“经验库”Tab 替换为“记忆文档”Tab。
- 列表展示股票、版本、字符数/容量和更新时间。
- 详情 Drawer 支持渲染 Markdown 与查看 Markdown 原文，并提供刷新。
- 旧 URL 的 `tab=library` 兼容映射到 `tab=memory`。
- `WrittenMemoryCards` 仅作为本次复盘写入审计，不作为当前文档正文来源。

## 8. 旧经验库清理

已移除：

- `experience/library` 列表、详情和重建接口。
- `ExperienceIndex` 模型及 `index_service.py`。
- 复盘完成后的索引同步逻辑。
- `experience_index_cleanup_scheduler`、相关配置和旧迁移脚本。
- 前端经验库组件、类型、API 方法和国际化文案。

新增迁移 `c18f2a6e9d44_drop_experience_indexes.py` 删除 `experience_indexes` 表。历史 Alembic 迁移文件保留为不可变迁移链的一部分；新环境执行到最新版本时不会创建该表。

## 9. 测试与验收

- `tests/test_memory_documents_store.py`：首写、版本递增、冲突、空内容、容量和用户隔离。
- `tests/test_memory_documents_api.py`：认证、列表不返回正文、股票名称、详情全文、404 和分页筛选。
- `tests/test_api_auth_required.py`：记忆文档接口必须认证。
- 经验工作流测试继续验证 `write_memory` 工具和复盘事件，不再验证经验索引同步。
- 前端执行 `npm run lint`、`npm run typecheck` 和 `npm run build`。
- 部署时执行 `alembic upgrade head`，确认旧 `experience_indexes` 表被删除。

## 10. 后续演进条件

- 若单股文档经常触顶，优先调整容量或增加周期性压缩，不恢复条目索引。
- 若需要全文检索，再基于 `memory_documents.content` 设计 PostgreSQL 全文索引，不重新建立经验事实表。
- 若需要审计历史版本，新增独立版本表，并保持当前活文档作为读取主入口。
