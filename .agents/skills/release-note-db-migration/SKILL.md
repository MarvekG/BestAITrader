---
name: release-note-db-migration
description: 为 Best-AI-Trader 生成或审阅 release note。适用于用户要求写发布说明、版本说明、升级说明或回滚说明；默认范围是上次 release 到当前 main，尤其关注数据库字段、索引、约束、SQLAlchemy model 或 schema 变更。重点是产出清晰的 release note；数据库内容只保留“数据库迁移的升级和回滚”章节、简短字段变更摘要和两行 Docker SQL 命令。
---

# Release Note 生成

## 目标

生成面向发布人员的 release note，重点写清楚本次发布改了什么、影响什么、如何升级、如何回滚、如何验证。数据库相关内容只占一个短章节，不展开实现细节。

## 输出结构

使用这个结构：

```markdown
# Release Note: <版本或标题>

## 变更摘要

## 变更范围

## 影响范围

## 数据库迁移的升级和回滚

## 升级步骤

## 回滚步骤

## 验证清单
```

## 写作规则

- `变更摘要`：用 3-6 条 bullet 概括用户可感知或运维需要关注的变化。
- `变更范围`：写清楚本次 release note 覆盖从上次 release 到当前 `main` 的变更；如果上次 release 标签不明确，先标成 `<LAST_RELEASE>` 占位符。
- `影响范围`：写受影响的后端服务、前端页面、异步任务、数据源、配置或部署组件。
- `数据库迁移的升级和回滚`：只写简短摘要和两行命令，不解释字段设计细节。
- `升级步骤`：说明先执行数据库升级命令，完成后再升级应用。
- `回滚步骤`：说明先执行数据库回滚命令，完成后再回滚应用。
- `验证清单`：列出 3-6 条发布后检查项。

## 范围判断

默认按上次 release 到当前 `main` 生成 release note。优先使用仓库中可见的最新 release tag、版本 tag 或用户指定的上次 release 点；如果无法确定，不要猜测，使用 `<LAST_RELEASE>..main` 占位并提醒发布人员确认。

## 数据库迁移章节

`## 数据库迁移的升级和回滚` 章节保持简短：

````markdown
## 数据库迁移的升级和回滚

- 字段变更：<一句话概括；没有则写“无”>
- 升级命令：
```bash
docker compose exec -T postgres psql -U <DB_USER> -d <DB_NAME> -c "<UPGRADE_SQL>"
```
- 回滚命令：
```bash
docker compose exec -T postgres psql -U <DB_USER> -d <DB_NAME> -c "<ROLLBACK_SQL>"
```
````

约束：

- 两条 Docker 命令使用字符串 SQL：`psql -c "<SQL字符串>"`。
- 两条 Docker 命令必须分别用三反引号 `bash` 代码块包裹。
- 不使用 `-f` SQL 文件路径。
- 不写数据库备份命令。
- 不在数据库章节展开字段类型、nullable、默认值、索引细节或迁移原理，除非用户明确要求。
- 不确定数据库名、用户、SQL 字符串时使用占位符，不编造。

## 顺序要求

release note 必须表达这两个顺序：

- 升级：先完成数据库升级，再升级应用。
- 回滚：先完成数据库回滚，再回滚应用。

不要把应用容器升级或回滚命令写进数据库章节；只有用户明确要求完整部署命令时，才在升级/回滚步骤中补充应用命令。
