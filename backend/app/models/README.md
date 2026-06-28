# Models 模块设计与约束

`models` 定义后端 SQLAlchemy 持久化结构，是数据库目标结构的代码表达。业务逻辑不应塞进 model；model 只描述表、字段、索引、关系和必要的轻量属性。

## 职责

- 定义用户、会话、账户、订单、持仓、成交、数据表、任务、AI 审计和经验复盘相关表。
- 为 CRUD、service 和测试夹具提供统一 ORM 映射。

## 设计约束

- model 不承载复杂业务流程；交易计算放 `trading_engine`，一致性写库放 service。
- 新增字段要同步检查 schema、CRUD、API 响应、前端类型和测试夹具。
- 新增表参与 SQLite 测试时，必须更新 `backend/tests/conftest.py` 中的建表列表。
- JSON 字段要明确结构和兼容性需求，不把重要业务状态藏成无约束字典。
- 不在 model import 具体业务 service，避免循环依赖和隐式副作用。

## 验证

- 模型变更运行相关数据库/CRUD/API 测试。
- 执行 `python -m flake8 <changed files>`。
