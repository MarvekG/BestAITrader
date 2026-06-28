# WebSocket 模块设计与约束

`websocket` 提供实时事件通道，用于任务状态、行情、AI 分析、选股和经验复盘等事件推送。它是通知层，不是业务状态的唯一来源。

## 职责

- 管理连接、订阅、退订和断线清理。
- 将 Redis 或业务事件推送到订阅资源的前端客户端。
- 配合前端重连后恢复订阅。

## 设计约束

- WebSocket 鉴权必须显式校验 ticket 或 token 语义，不能默认信任连接。
- 事件必须带资源类型和资源 ID，避免广播过宽。
- 通知事件不替代持久化状态；前端错过消息后应能通过 HTTP 查询恢复。
- 不在 WebSocket manager 中写业务数据，业务写入由对应 service 完成。
- 新增事件类型时同步检查前端 `services/websocket.ts` 和订阅 hook。

## 修改入口

- 连接管理：`manager.py`
- 路由：`routes.py`

## 验证

- 运行 websocket/task 相关测试。
- 前端订阅变更运行 lint、typecheck、build。
