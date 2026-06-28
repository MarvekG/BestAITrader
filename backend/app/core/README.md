# Core 模块设计与约束

`core` 存放后端基础设施：配置、数据库、Redis、安全、日志、i18n、环境变量、request context 和通用工具。业务模块可以依赖 `core`，但 `core` 不应该反向依赖具体业务。

## 职责

- 提供 `settings`、数据库会话、Redis client 和初始化逻辑。
- 管理认证、密码/JWT、WebSocket ticket 和 request id。
- 提供结构化日志、敏感字段脱敏、i18n 文案加载和 `.env` 读写。
- 承载跨模块通用工具，例如日期、股票代码、JSON 安全序列化。

## 设计约束

- `core` 只能放通用能力，不放交易、选股、复盘等业务判断。
- 日志额外字段必须使用 `extra={...}`，不要拼接到日志消息正文。
- 敏感配置只从环境或本地配置读取，不能写入代码和默认文档样例中的真实值。
- 通用工具要保持无副作用；涉及 I/O 的能力应在调用处显式体现。
- 数据库会话在最小实际更新范围内创建和关闭，避免跨层长期持有。
- 新增安全能力时同时检查 HTTP API 和 WebSocket 两条路径。

## 修改入口

- 配置：`config.py`
- 数据库：`database.py`、`init_db.py`
- 安全：`security.py`、`websocket_ticket.py`
- 日志：`logger.py`
- i18n：`i18n.py`、`system_language.py`
- 通用工具：`utils/`

## 验证

- Python 修改先运行最小范围 `python -m flake8 <changed files>`。
- 认证或路由安全变更运行 `PYTHONPATH=backend pytest backend/tests/test_api_auth_required.py`。
