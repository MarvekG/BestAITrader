# 经验复盘系统设计

`experience` 是单股 AI 辩论后的后验复盘系统。它不重新选股，也不直接执行交易；系统将已有 PM 决策与决策后的真实市场表现结合，判断原始结论是否正确，解释涨跌主因，并把可复用经验写回当前股票的长期记忆文档。

## 1. 产品定位

经验复盘持续回答三个问题：

1. 原始 PM 判断是否被后续市场验证？
2. 当时使用的信号哪些被验证、哪些被证伪、哪些只是噪音？
3. 下一次 Debate、PM 或风控流程应该具体改什么？

复盘模块生产经验，`memory_documents` 保存经验正文和证据链，后续 Debate/PM 按需读取同一股票的整份 Markdown 文档。复盘事件表保存运行审计，不再维护独立经验索引表。

## 2. 设计原则

- 不做自动选股，不改变研究和模拟交易定位。
- 不直接触发真实资金交易。
- 当前行情、财务、新闻和政策事实优先于历史记忆。
- 每次复盘必须输出原始判断、信号验证和流程改进三件套。
- 经验写入 `memory_documents` 前必须符合整文档替换和版本锁协议。
- 用户只能访问自己的复盘事件和记忆文档。

## 3. 端到端链路

```text
Debate Session
  -> 满足 5D/20D/60D 复盘条件
  -> ExperienceService 校验并构造上下文
  -> LangGraph 复盘工作流调用 LLM 和记忆工具
  -> ExperienceReviewEvent 保存运行结果和审计事件
  -> write_memory 更新 (user_id, stock_code) 活文档
  -> 记忆文档 Tab 直接读取当前 Markdown
  -> 后续 Debate / PM 按需 read_memory
```

数据职责：

| 数据 | 职责 |
| --- | --- |
| `memory_documents` | 保存当前用户某只股票的完整 Markdown 记忆，是经验正文唯一来源。 |
| `ExperienceReviewEvent` | 保存复盘运行状态、工具调用、结构化结果和错误信息。 |
| `DebateMessage` | 保存原始辩论和 PM 决策，作为复盘输入。 |

## 4. 复盘候选和周期

后端扫描当前用户已完成的 Debate Session，读取最新 PM 决策时间，统计决策后可用日 K，并返回每个周期的状态：

```text
not_ready
ready_5d
ready_20d
ready_60d
reviewing
reviewed
failed
```

同一个 session 可以分别执行 5D、20D、60D 复盘。20D 是默认优先周期，5D 适合早期纠错，60D 适合中期逻辑验证。

复盘 API 挂载在 `/api/v1/experience`：

- `POST /analyze`
- `GET /review-candidates`
- `GET /debate-sessions`
- `GET /review-events/{session_id}`
- `GET /review-runs`
- `GET /review-run-events/{review_run_id}`
- `GET /review-run-result/{review_run_id}`
- `DELETE /review-runs/{review_run_id}`
- `DELETE /review-runs`

## 5. 复盘输出

工作流用 Pydantic 约束三类核心输出：

### 5.1 原始判断

综合绝对收益、基准相对收益、行业相对收益、最大回撤、PM 置信度、目标仓位和交易周期，输出 `correct`、`partially_correct`、`incorrect` 或 `inconclusive`，并解释原因。

### 5.2 信号验证

将原始 Debate 中的信号分为：

- 被验证信号；
- 被证伪信号；
- 噪音信号。

每个信号尽量包含市场证据、影响程度和未来可复用教训。

### 5.3 流程改进

输出 Debate、PM 和风控下一次需要补充的证据、仓位纪律、否决条件和失效边界，必须是可执行规则，而不是泛泛总结。

## 6. 记忆写入

复盘工作流通过 `build_memory_tools(state=...)` 获取当前股票绑定的 `read_memory` 和 `write_memory` 工具。

- `read_memory()` 返回整份 Markdown、`version` 和 `size_chars`。
- `write_memory(content, importance, base_version)` 以完整文档替换当前内容。
- 写入前必须先读取最新版本；版本冲突时合并最新全文后重试。
- 文档超过 `MEMORY_DOC_MAX_CHARS` 或为空时拒绝写入。
- 没有新增可复用经验时跳过写入。
- 记忆只能作为历史经验参考，不能伪造未来市场结果或覆盖当前事实。

## 7. 用户查看记忆文档

经验分析页 `/experience` 保留复盘分析 Tab，并新增“记忆文档”Tab。查看接口位于 `/api/v1/memory-documents`：

- `GET /`：分页返回当前用户文档摘要，不返回正文；支持股票代码或名称关键词。
- `GET /{stock_code}`：返回当前用户指定股票的完整 Markdown 文档。

列表展示股票、版本、字符数/容量和更新时间；详情页用 Markdown 渲染器展示正文，同时允许查看原文并刷新。查看接口只读，不创建、修改或删除文档。

股票名称通过 `data.stock_basic` 外连接获取，不写回记忆表。列表只展示已经存在的文档，不为股票仓库批量创建空文档。

旧 URL 的 `tab=library` 映射到 `tab=memory`，避免历史书签失效。

复盘结果中的“本次记忆文档写入”仍保留，作用是审计本次工具写入状态；当前文档正文以记忆文档查看接口为准。

## 8. 旧经验库清理

经验库索引已从运行链路中移除：

- 删除 `/experience/library` 列表、详情和重建接口。
- 删除 `ExperienceIndex` 模型和 `index_service.py`。
- 删除复盘完成后的索引同步。
- 删除索引清理调度任务、相关配置和旧 SQL 脚本。
- 删除前端经验库组件、类型、API 方法和国际化文案。
- 迁移 `c18f2a6e9d44_drop_experience_indexes.py` 删除 `experience_indexes` 表。

历史 Alembic 文件保留为不可变迁移链的一部分；新环境执行到最新版本时不会创建经验索引表。

## 9. 测试和验收

- 记忆存储测试覆盖首写、版本递增、冲突、容量、空内容和用户隔离。
- 记忆文档 API 测试覆盖认证、分页、股票名称、列表不返回正文、详情全文和 404。
- 经验工作流继续覆盖工具调用、结构化输出和复盘事件，不再依赖经验索引。
- 经验分析页面保留复盘候选、任务、结果、工具轨迹和 WebSocket 更新。
- 前端执行 `npm run lint`、`npm run typecheck`、`npm run build`。
- 部署时执行 `alembic upgrade head`，确认旧 `experience_indexes` 表被删除。
