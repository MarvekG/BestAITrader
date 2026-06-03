# Python 沙箱预热 Worker 池设计

## 背景

当前 Python 沙箱由 `backend/app/ai/agentic/tooling/python_sandbox.py` 调用 Deno 运行
`backend/app/ai/agentic/tooling/pyodide_runner.ts`。每次沙箱调用都会启动一个新的 Deno 进程，并在该进程内完成
Pyodide、numpy、pandas 的初始化，然后执行一次 Agent 生成的 Python 代码并退出。

这种模式具备良好的进程级隔离，但用户感知延迟高。历史日志中成功执行常见耗时约 6-15 秒，超时接近
`PY_SANDBOX_TIMEOUT_SECONDS`。主要成本不是 Linux 进程创建本身，而是 Pyodide/WASM 与数据分析包的冷启动。

一次投研分析通常会由多个 Agent 并发启动，每个 Agent 内部又串行触发多次 `execute_python_sandboxed` 或
`query_and_calculate`。单轮分析累计可能包含数十次沙箱调用。因此优化目标不是只提升第一次调用，而是降低有热资源时的单次响应延迟，并在热资源耗尽时保持可用性。

## 目标

- 保持当前 Pyodide + Deno 的安全边界，不新增 Deno `--allow-run`、`--allow-net`、`--allow-write` 权限。
- 通过预热 worker 池降低沙箱调用的用户感知延迟。
- 采用 one-shot worker：每个 worker 只执行一次用户代码，执行后退出，避免跨请求状态污染。
- 池耗尽时允许短暂等待，等待失败后回退到当前一次性 `deno run pyodide_runner.ts` 路径。
- 删除不再需要的同步入口 `execute_python_in_sandbox_sync`，保留异步入口 `execute_python_in_sandbox`。
- 保留现有 AST 校验、stdout/stderr 限制、超时控制和结果结构。

## 非目标

- 不把沙箱改成原生 CPython 执行器。
- 不在 Deno 内部启动新的 Deno/Python 子进程。
- 不复用执行过用户代码的 worker。
- 不为单次分析建立会话级 worker 绑定。
- 不新增前端能力或变更 Agent 工具调用签名。

## 推荐方案

采用纯 one-shot 预热池。后端维护一组已经完成 Pyodide、numpy、pandas 初始化的 Deno worker。沙箱请求到来时，后端从 ready 队列取出一个 worker，将请求通过标准输入发给 worker，worker 执行一次 Python 代码后将结果通过标准输出返回，然后进程退出。后端随后在后台补充新的预热 worker。

高层流程：

```text
后端首次需要沙箱或应用启动预热
-> 后台启动 N 个 Deno worker
-> worker 加载 Pyodide、numpy、pandas
-> worker 输出 ready 消息
-> worker 进入 ready 队列

沙箱请求到来
-> Python 后端完成 AST 校验
-> 构造请求 JSON
-> 从 ready 队列 acquire worker
-> 将请求写入 worker stdin
-> 读取 worker stdout 返回结果
-> worker 退出，不回池
-> 后台补充一个新的 worker
```

## 为什么需要协议

预热 worker 与当前一次性 runner 的根本区别是：worker 在执行用户代码之前已经常驻，并且后端需要知道 worker 的生命周期状态。不能只把代码写入 stdin 再等待任意 stdout，因为 stdout 同时承担了控制信号、执行结果和错误诊断的传输职责。必须定义明确协议，原因如下。

### 区分 worker 已预热和仍在启动

预热池只能把已经完成 Pyodide 初始化的进程放进 ready 队列。如果没有 ready 协议，后端无法可靠判断 worker 当前处于哪个阶段：

```text
进程已启动但 Deno 仍在加载模块
进程已加载 pyodide.mjs 但 numpy/pandas 未加载完成
进程已完全预热，可以接收用户代码
进程启动失败但还没有退出
```

因此 worker 启动后必须输出一条结构化 ready 消息。后端只有读到合法 ready 消息，才允许将 worker 放入 ready 队列。

### 避免 stdout 内容污染结果解析

用户代码可以执行 `print(...)`。这些输出需要作为沙箱结果的 `stdout` 字段返回，而不能直接写到 worker 进程 stdout。worker 进程 stdout 必须只承载协议消息，否则后端按行解析时会无法区分哪一行是用户输出，哪一行是控制消息或最终结果。

协议要求 worker 内部继续使用 `redirect_stdout` 和 `redirect_stderr` 捕获用户 Python 输出，并将捕获结果放入响应 JSON。worker 自己的 stdout 只输出 ready 和 result 两类协议消息。

### 关联请求与响应

虽然 one-shot worker 每次只执行一个请求，但响应仍应携带 request id。这样做有三个价值：

- 避免未来扩展或异常情况下出现响应错配。
- 日志可以把后端 acquire、worker 执行和最终结果串起来。
- 协议错误时可以明确知道是哪个请求失败。

因此每个请求包含 `id`，响应必须回传同一个 `id`。

### 支持可诊断的失败语义

没有协议时，后端只能看到进程退出码、stdout 和 stderr，很难判断失败原因。预热池需要区分：

```text
worker 启动失败
worker 预热超时
worker ready 消息非法
请求执行超时
用户代码运行时异常
worker 返回非法 result JSON
worker 进程异常退出
```

这些失败的处理策略不同。例如预热失败应丢弃 worker 并补池；执行超时应 kill worker 并返回 `timed_out=true`；池 acquire 超时可以回退旧路径；已经发送用户代码后的协议错误不应再重复执行同一段代码。

结构化协议可以让后端以稳定字段判断错误类型，而不是依赖字符串或日志内容。

### 保证权限最小化

协议走 stdin/stdout JSON Lines，不需要端口监听，也不需要网络权限。worker 仍只需要：

```bash
deno run --quiet --allow-read=<runner_dir>,<pyodide_root> pyodide_one_shot_worker.ts <pyodide_root>
```

如果改成 HTTP 或 worker 内部再启动子进程，就会引入 `--allow-net` 或 `--allow-run`。stdin/stdout 协议可以保留当前最小 Deno 权限边界。

## 协议设计

协议采用 JSON Lines。每条协议消息占 stdout 或 stdin 的一行。worker 的 stdout 不输出非协议文本；worker 的 stderr 可以用于 Deno/Pyodide 启动诊断，但后端不依赖 stderr 判断业务结果。

### Ready 消息

worker 完成 Pyodide、numpy、pandas 初始化后输出：

```json
{"type":"ready","worker_id":"sandbox-worker-uuid","metadata":{"python_runtime":"pyodide","sandbox_runtime":"deno_prewarmed_worker","pyodide_version":"0.29.3"}}
```

字段说明：

- `type`: 固定为 `ready`。
- `worker_id`: worker 唯一标识，用于日志与排障。
- `metadata.python_runtime`: 固定为 `pyodide`。
- `metadata.sandbox_runtime`: 固定为 `deno_prewarmed_worker`。
- `metadata.pyodide_version`: 实际加载的 Pyodide 版本。

后端只有读到合法 ready 消息才将 worker 放入 ready 队列。

### 请求消息

后端向 ready worker 的 stdin 写入一行请求 JSON：

```json
{"type":"execute","id":"request-uuid","code":"print(2 + 2)","limits":{"stdout_max_bytes":32768,"stderr_max_bytes":16384}}
```

字段说明：

- `type`: 固定为 `execute`。
- `id`: 请求唯一标识，响应必须原样返回。
- `code`: 已通过后端 AST 校验的 Python 代码。
- `limits.stdout_max_bytes`: stdout 最大字节数。
- `limits.stderr_max_bytes`: stderr 最大字节数。

### Result 消息

worker 执行一次代码后输出一行 result JSON，然后退出：

```json
{"type":"result","id":"request-uuid","success":true,"stdout":"4\\n","stderr":"","error":null,"execution_time_ms":42,"timed_out":false,"truncated":false,"metadata":{"python_runtime":"pyodide","sandbox_runtime":"deno_prewarmed_worker","pyodide_version":"0.29.3","worker_id":"sandbox-worker-uuid","result_type":"none","output_bytes":2}}
```

字段说明：

- `type`: 固定为 `result`。
- `id`: 与请求 `id` 一致。
- `success`: 用户代码是否执行成功。
- `stdout`: 捕获到的用户 Python stdout。
- `stderr`: 捕获到的用户 Python stderr。
- `error`: 用户代码异常或 worker 执行错误。
- `execution_time_ms`: 用户代码执行耗时，不包含 worker 预热耗时。
- `timed_out`: worker 内部不主动标记超时；后端等待超时时 kill worker 并构造超时响应。
- `truncated`: stdout/stderr 是否被截断。
- `metadata`: 运行时、worker 与结果元信息。

### 协议错误处理

- ready 前 stdout 不是合法 ready JSON：kill worker，记录 `sandbox_boot_error`，补池。
- ready 超时：kill worker，记录 `sandbox_boot_timeout`，补池。
- 请求发送后 worker 返回非法 JSON：kill worker，返回 `protocol_error`，不重复执行同一请求。
- 响应 `id` 与请求 `id` 不一致：kill worker，返回 `protocol_error`，不重复执行同一请求。
- 请求执行等待超时：kill worker，返回 `timeout_error`，不回退旧路径。
- acquire ready worker 超时：未发送用户代码，可以回退旧的一次性 subprocess 路径。

## 后端执行策略

`execute_python_in_sandbox(code)` 保持现有公开入口。执行顺序：

```text
1. 检查 PY_SANDBOX_ENABLED。
2. validate_python_code(code)。
3. 检查 Deno 可执行文件。
4. 构造请求 JSON。
5. 如果预热池启用，尝试从池中 acquire ready worker。
6. acquire 成功则走 worker 协议执行。
7. acquire 超时或 worker 池不可用时，回退现有一次性 subprocess runner。
8. 统一使用 _normalize_response 生成响应。
```

当前同步入口 `execute_python_in_sandbox_sync` 不再保留。测试中对同步入口的覆盖需要删除或改写为异步路径。生产调用方继续使用 `execute_python_in_sandbox`。

## Worker 生命周期

worker 状态机：

```text
starting -> ready -> executing -> exited
starting -> failed
ready -> killed
executing -> killed
```

规则：

- `starting` worker 不接收用户请求。
- 只有输出合法 ready 消息的 worker 可以进入 `ready`。
- `ready` worker 被 acquire 后立即从 ready 队列移除。
- `executing` worker 无论成功、失败还是运行时异常，返回一次 result 后都退出。
- 超时、协议错误或进程异常退出时，worker 被 kill 或丢弃。
- worker 退出后后台补充新的 worker，直到 ready + starting 达到目标池大小。

## 配置建议

新增配置：

```python
PY_SANDBOX_PREWARM_POOL_ENABLED: bool = True
PY_SANDBOX_PREWARM_ON_STARTUP: bool = True
PY_SANDBOX_PREWARM_POOL_SIZE: int = max(1, (os.cpu_count() or 1) // 2)
PY_SANDBOX_PREWARM_MIN_READY: int = max(2, PY_SANDBOX_PREWARM_POOL_SIZE // 2)
PY_SANDBOX_PREWARM_MAX_STARTING: int = PY_SANDBOX_PREWARM_POOL_SIZE
PY_SANDBOX_WORKER_ACQUIRE_TIMEOUT_SECONDS: int = 3
PY_SANDBOX_WORKER_STARTUP_TIMEOUT_SECONDS: int = 30
PY_SANDBOX_WORKER_RUNNER_PATH: str = str(PROJECT_ROOT / "app/ai/agentic/tooling/pyodide_one_shot_worker.ts")
```

预热池默认开启并在后端启动时预热，优先降低首轮 Agent 沙箱调用延迟。`PY_SANDBOX_PREWARM_POOL_SIZE` 默认等于 `max(1, (os.cpu_count() or 1) // 2)`，即默认按 CPU 核心数的一半维持预热 Deno worker。内存充足且沙箱调用密集时，可以通过环境变量继续调高；内存紧张或只做轻量开发调试时，可以关闭 `PY_SANDBOX_PREWARM_ON_STARTUP` 或降低池大小。

## 池耗尽策略

预热池不应该让请求无限等待。推荐行为：

```text
ready worker 可用 -> 立即执行
ready worker 不可用 -> 最多等待 PY_SANDBOX_WORKER_ACQUIRE_TIMEOUT_SECONDS
等待后仍不可用 -> 回退当前一次性 deno subprocess runner
```

这种策略保证方案是优化而不是新的可用性风险。对于一次分析内的数十次调用，前几次和有 LLM 思考间隔的调用可以受益；若调用密度超过补池速度，则逐步退化为旧路径。

## 状态隔离

one-shot worker 的状态隔离接近当前一次性进程模式。用户代码执行后 worker 退出，以下状态不会跨请求传播：

- 用户定义的普通变量。
- 对 `np`、`pd`、`math`、`statistics` 等模块对象的 monkey patch。
- 对 safe builtins 字典的修改。
- Pyodide 内部模块缓存污染。
- WASM 内存增长。

仍需保留现有静态和运行时防护：

- AST 校验禁止危险 import、危险调用和 dunder 属性访问。
- Deno 权限仅允许读取 runner 目录和 Pyodide 目录。
- 用户 stdout/stderr 必须被捕获到 result JSON。
- 执行超时由后端 kill worker。
- 数据查询仍受 `query_and_calculate` 的 `limit` 限制。

## 可观测性

建议记录以下字段，日志上下文通过 `extra={...}` 传递：

- `sandbox_runtime`
- `worker_id`
- `pool_ready`
- `pool_starting`
- `acquire_ms`
- `startup_ms`
- `execution_time_ms`
- `fallback_reason`
- `timed_out`
- `request_id`

这些字段用于回答三个问题：

- 本次调用是否命中预热 worker。
- 池是否经常被打空。
- Pyodide 预热时间和用户代码执行时间分别是多少。

## 测试计划

- pool enabled 且有 ready worker 时，`execute_python_in_sandbox` 使用预热 worker。
- worker 执行一次后不会回到 ready 队列。
- 执行完成、超时或协议错误后会触发补池。
- acquire 超时且未发送用户代码时回退旧 subprocess 路径。
- 请求发送后 worker 超时返回 `timed_out=true`，且不回退旧路径。
- ready 消息非法时 worker 被丢弃并补池。
- result `id` 与请求不一致时返回 `protocol_error`。
- 删除 `execute_python_in_sandbox_sync` 后无生产代码引用。
- Deno 可用时连续执行两次真实沙箱代码，结果正确，并验证变量不会跨 worker 残留。

## 风险与缓解

- 内存占用增加：Pyodide + pandas worker 常驻内存较高，`PY_SANDBOX_PREWARM_POOL_SIZE` 默认等于 CPU 核心数的一半，允许通过环境变量调低或关闭启动预热。
- 补池速度跟不上调用峰值：池空时短等后回退旧路径，避免分析任务卡死。
- 协议实现错误导致响应错配：使用 JSON Lines、`type` 和 `id` 校验，协议错误不重复执行已发送的用户代码。
- 启动时预热拖慢后端：默认启用 startup prewarm，以换取首轮沙箱调用延迟下降；开发环境可关闭 `PY_SANDBOX_PREWARM_ON_STARTUP`。
- worker stdout 被污染：worker 内部捕获用户 stdout/stderr，进程 stdout 只允许输出协议消息。

## 结论

纯 one-shot 预热池是当前 Pyodide + Deno 架构下隔离优先的加速方案。它不减少 Pyodide 初始化的总 CPU 成本，但能将初始化前置到后台，在 ready worker 可用时显著降低用户感知延迟。通过明确的 stdin/stdout JSON Lines 协议，后端可以可靠管理 worker 预热、请求执行、超时、协议错误和 fallback，同时保持 Deno 最小权限边界。
