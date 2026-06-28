# Tasks 模块设计与约束

`tasks` 负责异步任务、定时任务、任务状态持久化和通知。耗时数据刷新、AI 分析、复盘、撮合和清理任务都应该通过这里形成可观察的执行过程。

## 职责

- 记录异步任务状态、输入、输出、错误和归属用户。
- 运行后台任务并通过 Redis/WebSocket 通知前端。
- 管理定时任务注册、启动、停止和清理。
- 承接盘后复盘、持仓纪律、挂单撮合、权益快照和用量清理等周期任务。

## 设计约束

- 任务必须显式携带 `user_id` 或说明为什么是系统级任务。
- 任务状态要能支持前端恢复展示：pending、running、success、failed、cancelled 等语义不能混淆。
- 失败要记录可排查错误信息，并发出失败通知，不能只写日志。
- 长任务不要直接占住 HTTP 请求；API 只负责创建任务和返回任务 ID。
- 定时任务必须能重复启动而不产生重复注册或重复执行。
- 任务函数不要绕过业务 service 直接改核心表，除非该任务本身就是对应 service 的最小写入边界。

## 修改入口

- 任务状态：`task_manager.py`
- 异步执行：`async_task_runner.py`、`process_executor.py`
- 调度框架：`async_scheduler.py`、`scheduled_task_registry.py`
- 业务调度：`*_scheduler.py`

## 验证

- 修改任务状态或通知时运行相关 task/websocket 测试。
- 修改具体业务调度时补跑对应业务模块测试。
