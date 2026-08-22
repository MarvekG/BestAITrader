# Alembic 数据库迁移引入设计

**版本**: v1.5
**日期**: 2026-08-21
**状态**: 待评审
**范围**: backend 主 trading 数据库

## 1. 目的

当前 backend 使用 `Base.metadata.create_all()` 创建表结构，已有表的字段、索引和约束变更需要手写 SQL。该方式无法提供以下能力：

- 记录数据库结构的版本和变更顺序；
- 在全新数据库中可重复构建当前 schema；
- 在部署前检查 ORM 与数据库结构是否存在差异；
- 对字段、索引、约束和 PostgreSQL enum 类型变更提供可审阅的升级与回滚脚本。

本设计引入 Alembic 作为 backend 主 trading 数据库的 schema 迁移工具。首个 revision 以当前主数据库的实际结构为基线，不追溯历史手写 SQL，也不把历史迁移脚本重新整理成 Alembic 版本链。

## 2. 目标与边界

### 2.1 目标

1. 为主数据库建立唯一的 Alembic 迁移目录和版本表。
2. 以当前主数据库在实施时的实际 schema 作为首个 baseline revision。
3. 支持全新 PostgreSQL 数据库通过 `alembic upgrade head` 创建当前 schema。
4. 支持现有主数据库在人工清理废表后由容器自动通过 `stamp` 接入版本链，不重复创建表、不修改业务数据。
5. 后续模型变更统一通过新的 Alembic revision 发布。
6. 保留当前应用的超级用户初始化逻辑，但不再让应用启动负责 schema 迁移。

### 2.2 不在范围内

- MemoFlux 的 `memo` 数据库和 `memoflux` schema；
- LiteLLM 使用的数据库对象；
- 历史手写 SQL 的逐条重放、重命名或补录；
- 业务数据回填、数据清洗和默认管理员数据迁移；
- 在 FastAPI `lifespan()` 内运行迁移；
- 将动态行情数据表全部改造成 ORM 模型；
- 将实时财务拉取逻辑改造成数据库持久化逻辑。

## 3. 当前结构盘点

### 3.1 应用侧结构

当前数据库连接配置位于 `backend/app/core/config.py`：

- 应用异步连接：`settings.ASYNC_DATABASE_URL`；
- Alembic 迁移连接：`settings.DATABASE_URL`；
- 数据库类型：PostgreSQL；
- 当前 Compose 主数据库：`trading`；
- 当前应用启动入口：`app.main.lifespan()` 调用 `initialize_database()`。

`backend/app/core/startup_db.py:initialize_database()` 当前同时负责：

1. 导入 `app.models` 注册 ORM 模型；
2. 创建 `data` 和 `stock_picker_interactive` schema；
3. 调用 `Base.metadata.create_all()`；
4. 创建默认超级用户。

引入 Alembic 后，第 1 项仍由迁移环境完成，第 4 项仍由应用启动完成，第 2、3 项由 Alembic revision 完成。

### 3.2 当前 ORM metadata

在当前代码和模型注册方式下，`import app.models` 后的 `Base.metadata` 包含 47 张表：

| schema | 表数量 |
| --- | ---: |
| `public` | 16 |
| `data` | 29 |
| `stock_picker_interactive` | 2 |
| 合计 | 47 |

模型注册入口仍是 `backend/app/models/__init__.py`，交互式选股模型来自 `app.ai.stock_picker.interactive_research.models`。

### 3.3 当前主数据库

实施前已检查当前运行中的主数据库，实际包含 61 张业务表：

| schema | 表数量 |
| --- | ---: |
| `public` | 19 |
| `data` | 35 |
| `stock_picker` | 4 |
| `stock_picker_interactive` | 3 |
| 合计 | 61 |

与 ORM metadata 对比后，多出的 14 张表全部判定为废弃表，必须在首次启动 backend 前由部署人员手动 DROP，不进入 Alembic 版本链。应用代码不包含这些表的清理逻辑，也不包含用于清理它们的 migration revision。

### 3.4 废弃表清单与依据

**无代码引用的残留表（6 张）：**

| 表 | 残留原因 |
| --- | --- |
| `data.stock_interactive_qa` | 数据源已在提交 6b4fd57f 中移除，表残留 |
| `data.stock_earnings_forecast` | 历史 AKShare 采集代码曾引用，当前代码无引用 |
| `public.debate_decision_snapshots` | 设计文档 008 的原型残留；正式实现未落地 |
| `public.pm_decision_snapshots` | 文档 004 方案已被 `PMDecisionRecord` 替代，表残留 |
| `public.portfolio_risk_control_configs` | 风控配置已改存 `system_settings` 的 key，表残留 |
| `stock_picker_interactive.research_artifacts` | 无任何代码引用的实验残留 |

**被实时拉取取代的财务存储表（4 张）：**

| 表 | 说明 |
| --- | --- |
| `data.financial_indicator` | 历史财务落库方案；当前财务数据由 Agent 工具实时拉取外部数据源 |
| `data.stock_income_statement` | 同上 |
| `data.stock_balance_sheet` | 同上 |
| `data.stock_cashflow_statement` | 同上 |

**已下线旧版选股表（4 张）：**

| 表 | 残留原因 |
| --- | --- |
| `stock_picker.stock_selection_runs` | 提交 1fa703db 已移除旧版选股实现 |
| `stock_picker.stock_selection_candidates` | 同上 |
| `stock_picker.stock_selection_events` | 同上 |
| `stock_picker.stock_recommendation_reviews` | 同上 |

这 14 张表的历史数据不迁移、不抢救。清理后的数据库结构与 `Base.metadata` 的 47 张表完全一致，baseline 不再需要任何保留对象或排除规则。

清理这 14 张表属于一次性运维操作，必须发生在 Alembic 首次接管之前。部署人员应先备份数据库，再直接执行经确认的 DROP DDL；当前财务模块已经通过 `FinancialSource` 实时调用数据源，代码中出现的旧表名仅用于字段映射和本地化配置，不构成数据库读写依赖。

## 4. 设计决策

### 4.1 一个迁移环境

backend 只建立一个 Alembic environment，服务对象是主 `trading` 数据库。建议目录如下：

```text
backend/
├── entrypoint.sh
├── migration_bootstrap.py
├── alembic.ini
└── alembic/
    ├── env.py
    ├── script.py.mako
    └── versions/
        └── <baseline_revision>_current_main_database.py
```

不为 `data`、`stock_picker_interactive` 或 `public` 分别建立独立 migration environment。三个 schema 由同一个 revision 事务管理，保证跨 schema 外键和表结构变更具有一致的版本号。

### 4.2 连接配置

Alembic 使用同步 PostgreSQL URL `settings.DATABASE_URL`，复用现有配置，不在 `alembic.ini` 或代码中写入用户名、密码、host 或真实 URL。

迁移环境需要满足以下约束：

- 不使用 `ASYNC_DATABASE_URL` 创建第二套异步迁移适配器；
- 不连接 MemoFlux 数据库；
- 不从 `.env` 之外读取或提交秘密；
- `alembic.ini` 中的 `sqlalchemy.url` 保持空值或占位值，由 `env.py` 在运行时注入配置；
- Alembic version table 固定放在 `public.alembic_version`。

### 4.3 Schema 和 metadata

迁移环境使用：

- `target_metadata = Base.metadata`；
- 在读取 metadata 前显式 `import app.models`；
- `include_schemas=True`；
- 迁移前创建 `data` 和 `stock_picker_interactive` schema；
- 开启 PostgreSQL enum、索引、约束和外键的比较。

首次接管时，入口先仅比较业务表集合，确保目标表完整且没有废表或未知表；随后标记 baseline 并执行所有后续 revision。每次 `upgrade head` 后，入口使用 Alembic metadata 比较表、列、索引、约束、默认值和类型。任何差异都会阻止 backend 启动。迁移环境只特殊排除 Alembic 自身管理的 `public.alembic_version`，避免将版本表误报为待删除对象。

### 4.4 首个 baseline revision

首个 revision 以清理废弃表之后的主数据库 schema-only 快照为输入，覆盖 47 张 ORM 管理的表：

- `public`、`data`、`stock_picker_interactive` schema；
- 当前 ORM 模型定义的表、列、默认值、索引、唯一约束、检查约束和外键；
- PostgreSQL enum 类型及其使用关系；
- `public.alembic_version` 由 Alembic 自动维护，不手写到 revision 中。

首个 revision 不包含现有表中的业务数据，也不创建默认超级用户。对于现有主数据库，baseline revision 的升级函数不应再次执行；接管操作使用 `stamp`。

当前历史主库与 baseline 存在列、索引、默认值和类型 drift，因此 baseline 之后的 `749d3c60cc58` reconciliation revision 负责将其收敛到当前 ORM metadata，包括移除未使用的交互式选股列、统一索引名、默认值、`system_settings.id` 类型和 `data.dragon_tiger_data.details` 类型。接管成功的数据库最终版本为该 revision，而非 baseline revision。

首个 revision 的 `downgrade()` 必须明确其适用范围：

- 仅用于全新、无业务数据的数据库验证；
- 不承诺对已经接管的生产数据库执行全量回滚；
- 若回滚会删除表或列，必须在函数和发布说明中明确数据损失风险；
- 不对 baseline 中的业务数据做自动恢复。

### 4.5 应用启动职责

实施完成后，`initialize_database()` 调整为数据初始化模块：

- 保留默认超级用户的幂等创建；
- 保留启动后的运行状态清理逻辑；
- 删除 `Base.metadata.create_all()`；
- 删除由迁移负责的 schema 创建逻辑；
- 不在 `lifespan()` 中调用 Alembic。

迁移由 backend 容器入口在应用进程启动前自动执行，并通过 PostgreSQL advisory lock 避免多副本同时迁移。迁移失败会阻止应用进程启动，避免不兼容的应用版本上线。

## 5. 现有主数据库接管流程

当前主数据库由 backend 容器入口自动接管，不要求用户手动执行 Alembic；废表清理是启动前的人工前置条件。入口按以下流程处理：

1. 停止会写主数据库的 backend、定时任务和相关 worker，或确保迁移窗口内没有 schema 写入。
2. 备份主数据库，至少保留一次可恢复的 `pg_dump` 数据备份和 schema-only 快照。
3. 部署人员手动 DROP 第 3.4 节的 14 张废弃表。
4. 启动 backend 容器；入口检查业务表集合，发现未知表或缺失表时直接失败。
5. 表集合检查通过后，入口自动 `stamp` baseline，并执行 reconciliation 与后续 `upgrade head`。
6. 入口完成完整 metadata 校验；存在列、索引、约束、默认值或类型差异时停止启动。
7. 启动应用进程；迁移失败则不启动 backend。

如果第 5 步发现差异，不得直接 `stamp` 掩盖差异，也不得直接 `upgrade` baseline。应先把差异分类为：

- 当前主库已有但 baseline 快照遗漏的对象；
- ORM metadata 与实际数据库不一致的对象；
- 环境残留或未发布对象；
- 需要作为下一条正式 migration 的业务变更。

分类完成前停止接管操作，避免 Alembic 版本号与实际结构失真。

## 6. 全新数据库流程

全新主数据库由 backend 容器入口自动初始化：

1. 创建 PostgreSQL 数据库和数据库用户。
2. 启动 Compose backend 容器。
3. 入口自动执行 `alembic upgrade head`。
4. `initialize_database()` 幂等创建默认超级用户。
5. 通过 backend 健康检查和核心 API 验证结果。

Alembic baseline 只负责结构，应用启动只负责必要的初始业务数据。两者不能互相替代。

## 7. 后续迁移工作流

后续数据库字段或结构变更遵循以下流程：

1. 修改 ORM 模型或明确编写 SQL DDL 变更。
2. 在 backend 环境生成 revision，例如：

   ```bash
   alembic revision --autogenerate -m "add <short description>"
   ```

3. 人工审阅生成的 upgrade 和 downgrade，尤其检查：
    - 是否误删当前 ORM 模型定义的表或约束；
   - PostgreSQL enum 是否需要显式处理；
   - 非空字段是否需要分阶段添加和回填；
   - 现有数据是否满足新约束；
   - 索引创建是否需要并发方式；
   - 跨 schema 外键和删除顺序是否正确。
4. 在 disposable PostgreSQL 数据库执行 `upgrade head`。
5. 在包含当前结构和代表性数据的测试库执行升级、业务测试和必要的回滚测试。
6. 将 revision 与应用版本一起发布，backend 容器入口会在应用进程前自动运行 `alembic upgrade head`。
7. 生产回滚优先回滚应用版本；数据库回滚由发布流程按已验证方案执行，不要求终端用户手动操作。

Alembic revision 是 schema 的唯一变更记录。`backend/scripts/` 中未来新增的表结构 SQL 不再作为独立迁移入口；确需原生 SQL 时，必须由 Alembic revision 的 `op.execute()` 或等价 Alembic 操作承载。

## 8. 测试与验收

### 8.1 静态检查

- backend 容器入口可以从 backend 工作目录正常加载 Alembic 配置；
- `env.py` 能导入全部当前 ORM 模型；
- `target_metadata` 包含 47 张当前 ORM 表和正确 schema；
- 首次启动前手动清理第 3.4 节的 14 张废弃表；
- 接管后数据库与 `target_metadata` 的表、列、索引、约束、默认值和类型一致；
- 迁移命令不要求连接 MemoFlux 数据库；
- 空库自动升级、清理后的已有库自动接管均可在容器启动阶段完成；
- 仓库中不存在真实 `.env`、密码、token 或数据库 dump。

### 8.2 全新 PostgreSQL 验证

在一次性 PostgreSQL 17 测试数据库中验证：

- `alembic upgrade head` 成功；
- 三个业务 schema 和 baseline 中的 47 张表存在；
- 关键外键、唯一约束、索引和 enum 类型存在；
- `alembic current` 显示当前 head revision；
- `alembic downgrade base` 只在空测试库验证，并确认删除行为符合文档；
- 再次执行 `alembic upgrade head` 成功。

SQLite 不作为 Alembic baseline 的结构验收数据库。当前模型包含 PostgreSQL schema、JSONB、ARRAY、UUID、enum、部分索引和跨 schema 外键，必须使用 PostgreSQL 验证。

### 8.3 当前主数据库验证

在真实当前主数据库上由容器入口执行接管检查和版本写入：

- 当前业务表集合完整且不存在未声明对象；
- 入口确认废弃表已由部署人员清理，执行 reconciliation 后数据库与 ORM metadata 完全一致；
- 入口遇到缺失表或未知对象时直接失败，不执行 `stamp`；
- 应用启动后仍能幂等创建或识别默认超级用户；
- 核心登录、账户、行情读取和交易模拟接口通过定向回归测试。

已有主库发现的历史列、默认值或索引命名差异由 `749d3c60cc58` reconciliation revision 统一修正；未来发现的同类差异必须新增并审阅 revision，不能绕过 metadata 校验。

## 9. 回滚和故障处理

### 9.1 迁移前

- 必须先备份主数据库；
- 必须保留升级前 schema-only 快照；
- 必须确认本次 revision 的锁等待、执行时间和数据量影响；
- 生产环境不把 `--autogenerate` 输出直接当作可执行发布物。

### 9.2 迁移失败

1. 停止继续启动新应用版本。
2. 查看 Alembic 日志和 PostgreSQL 锁等待信息。
3. 确认事务是否已回滚；PostgreSQL DDL 默认在事务中执行，但不能假设所有自定义操作都具备相同语义。
4. 对失败 revision 做修复提交，不修改已经发布的 revision。
5. 重新在 disposable PostgreSQL 和预发布数据库验证后再部署。

### 9.3 应用回滚

应用版本回滚不等于数据库回滚。若新应用只读取新增字段，可以先回滚应用而保留向前兼容的数据库结构。只有旧版本无法运行且已完成数据兼容评估时，才执行对应的 `alembic downgrade`。

## 10. 实施顺序

| 阶段 | 内容 | 产物 |
| --- | --- | --- |
| 1 | 备份主数据库并由部署人员手动 DROP 第 3.4 节的 14 张废弃表 | 数据备份、清理记录 |
| 2 | 导出清理后的 schema-only 快照并生成对象清单 | baseline 输入、对象 fingerprint |
| 3 | 添加 Alembic 依赖和配置骨架 | `backend/alembic.ini`、`backend/alembic/env.py` |
| 4 | 接入 `Base.metadata` 和 schema 配置 | 可加载的 target metadata |
| 5 | 编写当前主数据库 baseline revision | `backend/alembic/versions/<baseline>.py` |
| 6 | 将启动建表职责移出 `initialize_database()` | 启动只做数据初始化 |
| 7 | 在全新 PostgreSQL 和当前主数据库分别验证 | migration smoke test、接管记录 |
| 8 | 更新部署文档和发布流程 | 升级、stamp、检查和回滚说明 |

## 11. 验收标准

本设计实施完成的最低标准如下：

1. 当前主数据库可以在完成废弃表清理后，在不重建表、不迁移数据的前提下被 `stamp` 接入 Alembic。
2. 全新 PostgreSQL 数据库可以从 baseline revision 创建当前主数据库结构。
3. `Base.metadata.create_all()` 不再是生产 schema 变更入口。
4. 未来模型变更可以生成、审阅、执行和回滚 Alembic revision。
5. 清理后的当前主数据库只包含 `Base.metadata` 对应的 47 张表；第 3.4 节的 14 张废弃表不出现在任何环境中。
6. MemoFlux 数据库不出现在 Alembic 迁移连接和版本记录中。
7. 迁移失败不会被应用启动流程吞掉，且不会通过自动创建缺失表掩盖 schema 版本错误。

## 12. 主要风险

| 风险 | 影响 | 处理方式 |
| --- | --- | --- |
| 当前数据库与 ORM metadata 已存在差异 | autogenerate 可能生成误删或误改 | 先清理 14 张废弃表，再以 schema-only 快照校验对象集合与 `Base.metadata` 一致 |
| 废弃表误判 | 删除了仍被代码访问的表，运行时查询失败 | 清理前强制数据备份；手动清理后运行财务 Context、核心登录和 API 回归 |
| baseline revision 过大 | 首次审阅和回滚困难 | 只建立一条当前结构基线，不重放历史；后续按业务变更拆分 |
| 多副本同时执行迁移 | 锁等待或发布竞态 | 入口使用 PostgreSQL advisory lock，且不在 FastAPI lifespan 内运行 |
| 非空字段直接加入已有大表 | 长时间锁表或升级失败 | 采用加 nullable、回填、加约束的分阶段 migration |
| 回滚删除业务数据 | 不可逆数据损失 | baseline downgrade 只在空库测试，生产优先前向兼容，不自动回滚数据 |
