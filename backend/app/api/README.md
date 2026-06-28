# API 模块设计与约束

`api` 是后端 HTTP 边界，负责把前端请求、安全上下文和业务服务连接起来。它不应该承载复杂业务状态机，也不直接实现交易、数据刷新、AI 工作流或持仓计算。

## 职责

- 在 `register_api_routes()` 中集中挂载 `/api/v1` 路由。
- 为 endpoint 绑定认证依赖、请求 schema、响应 schema 和 tags。
- 将请求转换为 service/workflow 调用，并把业务结果整理为前端契约。
- 保留少量明确公开入口，例如登录、通用 i18n 和必要健康信息。

## 设计约束

- 新增业务路由默认加 `get_current_user`；公开路由必须能解释为什么不需要用户身份。
- endpoint 保持薄入口：参数校验、权限检查、调用 service、返回结果。
- 不在路由函数里写事务状态机、批量数据处理、LLM 工具循环或交易撮合逻辑。
- 不把 SQLAlchemy model 直接作为响应返回；外部契约放到 `schemas`。
- Runtime extension 相关路由继续受 `ENABLE_RUNTIME_EXTENSIONS` 控制。
- WebSocket endpoint 必须显式处理鉴权，不复用 HTTP bearer 的隐式假设。

## 修改入口

- 路由聚合：`__init__.py`
- endpoint：`endpoints/`
- 资源归属校验辅助：`ownership.py`

## 验证

- API 鉴权边界：`PYTHONPATH=backend pytest backend/tests/test_api_auth_required.py`
- 修改具体业务 endpoint 时，补跑相邻 endpoint/service 测试。
