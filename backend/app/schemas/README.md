# Schemas 模块设计与约束

`schemas` 是后端 API 的 Pydantic 契约层。它隔离外部请求/响应结构和内部 ORM 结构，是前后端协作的稳定边界。

## 职责

- 定义请求体、响应体、枚举和可复用 DTO。
- 承接输入校验和输出序列化。
- 隐藏内部数据库字段和敏感字段。

## 设计约束

- 不把 SQLAlchemy model 直接暴露给前端；响应结构应由 schema 明确声明。
- 输入 schema 要校验外部输入，特别是股票代码、日期、分页、枚举和金额数量。
- 响应 schema 不返回密码、token、provider key、内部异常堆栈等敏感信息。
- 字段重命名或删除要同步检查前端 `api/*.ts` 和页面使用方。
- schema 不承载业务计算；派生字段由 service 计算后填充。

## 验证

- 运行相关 API 测试。
- 前端契约受影响时运行 `cd frontend && npm run typecheck`。
