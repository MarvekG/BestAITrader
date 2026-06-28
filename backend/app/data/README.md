# Data 模块设计与约束

`data` 是 A 股数据工程底座，负责外部数据接入、标准化、落库、刷新调度和指标计算。AI、交易、组合估值和前端数据页都应该从这里取得可信数据，而不是各自临时抓取和清洗。

## 职责

- 接入基础资料、行情、财务、资金流、指数、公告和 A 股特色数据。
- 通过 ingestor 插件统一外部数据源。
- 将 DataFrame 标准化、去重、upsert 到结构表或通用数据表。
- 计算技术指标并为 AI context 提供可复用数据片段。
- 通过调度器错峰刷新数据。

## 设计约束

- 新数据源按 `ingestors/plugins/README.md` 做插件，不新增平行采集框架。
- DataFrame 入库统一走 `ingestion/service.py`，避免每个采集器手写 upsert。
- 股票代码、日期和 JSON 清洗优先复用 `core/utils/`。
- 外部源失败要保留错误或缺失状态，不能把部分数据伪装为完整数据。
- 大批量刷新放到调度或异步任务，不放进普通 HTTP 请求链路。
- 数据层只提供事实和派生指标，不直接给出买卖建议。

## 修改入口

- 数据源插件：`ingestors/`
- 落库服务：`ingestion/service.py`
- 自动刷新：`refresh_scheduler.py`
- 技术指标：`analytics/`
- 财报本地化和元数据：`metadata/`

## 验证

- 修改 ingestor 或落库逻辑时运行相邻 data/ingestion 测试。
- 新增结构表时检查 `backend/tests/conftest.py` 的 SQLite 测试建表列表。
