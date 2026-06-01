# 经验库字段模糊搜索设计

## 背景

经验库列表当前通过后端 `ExperienceIndexService.list_items()` 查询。筛选项中，`stock_code`、`industry`、`strategy`、`review_horizon`、`correctness`、`importance` 使用精确匹配，`tag` 使用精确标签包含，全局 `keyword` 只对 `summary` 做 `ILIKE` 包含匹配。

用户希望经验库的各个搜索字段都支持模糊搜索，即输入字段片段也能命中经验记录，而不是必须完整等于索引字段。

## 目标

- 所有经验库文本筛选字段支持模糊包含匹配。
- 全局关键词搜索覆盖更多经验索引字段，而不只搜摘要。
- 保持前端筛选表单和 API 参数不变，降低改动范围。
- 不引入 PostgreSQL 扩展或向量搜索，先实现稳定的数据库 `ILIKE` 模糊匹配。

## 非目标

- 不实现拼写纠错、相似度排序或语义搜索。
- 不改变分页、排序、复盘详情展示逻辑。
- 不调整经验索引表结构。

## 后端设计

在 `ExperienceIndexService.list_items()` 中统一将文本筛选改为模糊包含：

- `stock_code`: `ExperienceIndex.stock_code.ilike("%value%")`
- `industry`: `ExperienceIndex.industry.ilike("%value%")`
- `strategy`: `ExperienceIndex.strategy.ilike("%value%")`
- `review_horizon`: `ExperienceIndex.review_horizon.ilike("%value%")`
- `correctness`: `ExperienceIndex.correctness.ilike("%value%")`
- `importance`: `ExperienceIndex.importance.ilike("%value%")`

`tag` 保持在 Python 层过滤，因为 `tags` 是 JSON 字段。匹配逻辑从精确包含改为对标签文本做大小写不敏感的子串包含。

全局 `keyword` 改为多字段 OR 匹配，数据库层覆盖：

- `summary`
- `stock_code`
- `stock_name`
- `industry`
- `strategy`
- `review_horizon`
- `outcome_label`
- `correctness`
- `importance`

全局 `keyword` 对 tags 的匹配仍在 Python 层补充：数据库筛选先对普通字段做 OR，随后在合并结果或统一 Python 过滤中补充标签命中。实现时优先选择简单、可读的方式，避免引入复杂查询构造。

## 前端设计

前端继续使用现有筛选字段和 API 参数，不新增参数。筛选输入框的 placeholder 可以调整为“关键词，支持模糊搜索”，让用户明确知道部分输入可命中。

## 错误处理

空字符串或只包含空白的筛选值不参与过滤，保持当前行为。模糊匹配不改变 API 错误响应格式。

## 测试设计

更新 `backend/tests/test_experience_index_service.py`：

- 字段片段能命中股票代码、行业、策略、复盘周期、正确性、重要性。
- 标签片段能命中 tags 中的标签值。
- 全局关键词能命中股票名、行业或标签，而不只命中摘要。
- 不匹配的片段仍返回空列表。

## 验证

- 运行经验索引服务相关测试。
- 运行前端 TypeScript 类型检查，确认前端改动无类型错误。
