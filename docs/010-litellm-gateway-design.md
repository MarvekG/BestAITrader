# LiteLLM 统一 LLM 接入层设计

## 背景

当前后端主应用已经有一层轻量 LLM Provider 抽象，入口集中在
`backend/app/ai/llm_providers/`。实际支持的 provider 只有 DeepSeek，主流程通过
`langchain_openai.ChatOpenAI` 执行 `ainvoke` 和 `bind_tools` 工具调用循环；少量健康检查、
Market Watch JSON 判断和 Memory 服务则直接使用 OpenAI SDK 的 `AsyncOpenAI.chat.completions.create`。

这套结构能稳定服务当前 DeepSeek 场景，但模型切换仍需要理解 DeepSeek thinking、tool call replay、
provider extra body、usage 解析等实现细节。后续如果要支持不同供应商或多个模型策略，继续在业务代码里扩展
provider 会让接入层越来越复杂。

本设计引入 LiteLLM Proxy 作为统一 LLM Gateway，让业务代码继续使用 OpenAI-compatible 协议和 LangChain
工具调用能力，把真实模型供应商、密钥、base URL、fallback 和路由策略下沉到 LiteLLM 配置。

## 目标

- 后端统一通过 OpenAI-compatible gateway 访问 LLM；MemoFlux 部署时通过 `MEMOFLUX_LLM_*`
  `.env` 配置指向 LiteLLM。
- 业务侧切换模型时优先只修改 LiteLLM 配置中的模型别名、真实模型、URL 和 key。
- 后端沿用原有 `LLM_PROVIDER`、`LLM_MODEL`、`LLM_API_KEY`、`LLM_BASE_URL` 配置名，但默认值集中在
  `backend/app/core/config.py`，避免到处写常量。
- 保持现有 LangChain `ChatOpenAI.bind_tools()` 工具调用循环，不引入 LiteLLM Python SDK 到业务代码。
- 保留当前 `app.ai.llm_providers` 抽象，但将 provider 实现收敛为 OpenAI-compatible gateway provider。
- thinking 开关、推理强度、temperature 等模型参数统一放到 LiteLLM `config.yaml` 的模型别名中管理；
  后端通过选择不同别名区分思考模型和非思考模型，不再注入 thinking 参数。
- 让 Debate、Stock Picker、Experience Review、Market Watch 都走后端统一入口；MemoFlux
  通过 OpenAI-compatible SDK 接入 LiteLLM。
- 移除 DeepSeek 专用 provider，不保留 `DeepSeekProviderPlugin` 或直连 DeepSeek 路径。

## 非目标

- 不在本阶段重写 agent 工具调用循环。
- 不引入新的 agent 框架或替换 LangChain。
- 不要求所有模型都支持工具调用；只要求标记为 agent 用途的模型别名通过工具调用验证。
- 不把真实 provider API key 写入后端 `.env` 或 MemoFlux `.env`；MemoFlux `.env` 只保存 LiteLLM gateway key、
  LiteLLM base URL 和 `memory` 模型别名。
- 不在应用启动逻辑中动态修改 LiteLLM 配置。
- 不在本阶段设计企业级多租户计费、团队配额或管理后台。
- 不在后端保留 provider-specific 适配代码；DeepSeek thinking 等能力只通过 LiteLLM 模型别名和配置管理。

## 总体架构

目标链路如下：

```text
Best-AI-Trader backend
  -> app.ai.llm_providers.LiteLLMProvider
    -> LangChain ChatOpenAI / OpenAI AsyncOpenAI
      -> LiteLLM Proxy
        -> DeepSeek / OpenAI / Anthropic / Gemini / OpenRouter / local OpenAI-compatible endpoint

MemoFlux service
  -> MEMOFLUX_LLM_* config
  -> OpenAI AsyncOpenAI
    -> LiteLLM Proxy
      -> memory 对应的真实模型
```

LiteLLM Proxy 提供 OpenAI Chat Completions 格式的统一入口，并通过 `config.yaml` 维护用户可见模型别名。
后端通过 `backend/app/core/config.py` 感知内部 OpenAI-compatible endpoint、用途模型别名和 gateway 访问凭据；
MemoFlux 通过 `memo/.env` 中的 `MEMOFLUX_LLM_*` 感知 LiteLLM endpoint、`memory` 模型别名和 gateway key。
两者都不感知真实供应商。

## 组件设计

### LiteLLMProvider

新增 `backend/app/ai/llm_providers/litellm.py`，实现现有 `LLMProviderPlugin` 协议。

职责：

- 使用 `langchain_openai.ChatOpenAI` 创建 chat model。
- 使用当前 settings 构造 OpenAI SDK `chat.completions.create` 参数。
- 不主动注入 DeepSeek、Anthropic、Gemini 等任何供应商专属参数。
- 允许调用方显式传入 `extra_body`，并原样透传给 LiteLLM；provider 不再生成默认 thinking 参数。
- 不依赖 LiteLLM Python SDK。
- 对 invalid tool calls 提供通用清理逻辑；不保留 DeepSeek 专属 replay 逻辑。

默认模型别名由 `backend/app/core/config.py` 集中配置，仅保留 `backend` 和 `memory` 两个别名。
这些别名的真实模型、真实 provider key 和真实 base URL 均由 LiteLLM 配置决定。

### 移除 DeepSeekProviderPlugin

迁移完成后删除现有 `DeepSeekProviderPlugin` 和 `DeepSeekChatOpenAI`。

删除范围：

- `backend/app/ai/llm_providers/deepseek.py`。
- factory 中对 `DeepSeekProviderPlugin` 的注册。
- 后端业务代码对 `LLM_DEEPSEEK_REASONING_EFFORT`、`LLM_DEEPSEEK_THINKING_MODE`、`LLM_THINKING_MODE`
  的依赖。
- 仅服务后端 thinking 参数注入的 helper、常量和测试。
- 仅服务 DeepSeek direct path 的测试和文档描述。

删除后，后端不再支持 `LLM_PROVIDER=deepseek` 直连模式。所有模型访问必须经过 LiteLLM gateway。

### LiteLLM Proxy

新增独立服务 `litellm`，建议通过 Docker Compose 管理。

示例结构：

```text
litellm/
  config.example.yaml
```

仓库只提交 `config.example.yaml`。运行时所需的真实 `config.yaml` 由开发者或部署环境在本地创建，不进入版本库。

`config.example.yaml` 示例：

```yaml
model_list:
  - model_name: gpt-4o-mini
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: sk-your-provider-key
      api_base: https://api.deepseek.com

  - model_name: backend
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: sk-your-provider-key
      api_base: https://api.deepseek.com

  - model_name: backend-thinking
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: sk-your-provider-key
      api_base: https://api.deepseek.com

  - model_name: memory
    litellm_params:
      model: deepseek/deepseek-v4-flash
      api_key: sk-your-provider-key
      api_base: https://api.deepseek.com

general_settings:
  master_key: sk-your-litellm-gateway-key
```

模型别名按服务边界命名，不按供应商命名。后端主应用引用 `backend`，Memory 服务在 `.env` 中引用 `memory`，
真实模型替换只发生在 LiteLLM 配置中。

LiteLLM 不再使用 `.env` 文件。真实 `config.yaml` 是运行时密钥文件，包含 provider key 和 proxy master key，
必须加入 `.gitignore`；仓库只提交不含真实密钥的 `config.example.yaml`。

## 配置设计

### 后端主应用

后端保留原有配置名，默认值集中在 `backend/app/core/config.py`：

```env
LLM_PROVIDER=litellm
LLM_MODEL=backend
LLM_THINKING_MODEL=backend-thinking
LLM_API_KEY=sk-your-litellm-gateway-key
LLM_BASE_URL=http://litellm:4000/v1
```

后端将 LiteLLM 视为固定的内部基础设施依赖：

- `LLM_PROVIDER` 保留原名用于兼容旧配置，但支持值固定为 `litellm`；`deepseek` 直连不再可用。
- 后端访问 LiteLLM 的 URL、gateway key、用途模型别名必须集中定义在 `backend/app/core/config.py`。
- `backend/app/core/config.py` 沿用原有配置名并提供固定默认值，例如 `LLM_PROVIDER`、`LLM_BASE_URL`、
  `LLM_API_KEY`、`LLM_MODEL`、`LLM_THINKING_MODEL`。默认 `LLM_PROVIDER=litellm`、`LLM_MODEL=backend`、
  `LLM_THINKING_MODEL=backend-thinking`。
- 业务代码只能通过 `settings` 读取上述配置，禁止在 agent、endpoint、service 中硬编码 LiteLLM URL、key 或模型别名。
- Docker 部署默认 URL 为 `http://litellm:4000/v1`；本地开发默认 URL 可在 `config.py` 中集中定义为
  `http://localhost:4000/v1` 或通过部署层覆盖。
- 如果生产环境启用 LiteLLM virtual key，后端仍通过 `backend/app/core/config.py` 读取该值；真实 provider key
  只存在于 LiteLLM 环境中，不进入后端 `.env` 示例。

LiteLLM `config.yaml` 是模型供应商、真实模型、真实 key、真实 base URL 的唯一配置来源。

### MemoFlux 服务

MemoFlux 使用 `MEMOFLUX_LLM_*` 配置和 OpenAI-compatible SDK。部署时将这些配置指向
LiteLLM 内部 endpoint 和 `memory` 模型别名：

```env
MEMOFLUX_LLM_MODEL=memory
MEMOFLUX_LLM_API_KEY=sk-your-litellm-gateway-key
MEMOFLUX_LLM_BASE_URL=http://litellm:4000/v1
```

这里的 `MEMOFLUX_LLM_API_KEY` 是 LiteLLM gateway key，不是真实 provider key。真实 provider key 仍只存在于
`litellm/config.yaml`。

MemoFlux 结构化召回链路继续显式要求可解析 JSON 输出。`memory` 别名应映射到 JSON 输出稳定的模型。

### LiteLLM 服务

LiteLLM 服务不读取 `.env`。真实 provider key、LiteLLM proxy master key、模型别名和 provider URL 都写在
`litellm/config.yaml` 中。

安全约束：

- `litellm/config.yaml` 是本地或部署环境的密钥文件，不提交到仓库。
- 仓库只提交 `litellm/config.example.yaml`，示例值必须是占位符。
- `general_settings.master_key` 是后端访问 LiteLLM 的 gateway key，应与 `backend/app/core/config.py`
  中的 `LLM_API_KEY` 保持一致。
- 生产部署必须使用非示例值的 `general_settings.master_key`，并避免把真实 provider key 提交到仓库。

## 调用流程

### Debate / Stock Picker / Experience Review

```text
业务流程
  -> get_llm_provider()
  -> LiteLLMProvider.build_chat_model()
  -> ChatOpenAI(model=settings.LLM_MODEL, base_url=settings.LLM_BASE_URL)
  -> bind_tools(tools)
  -> ainvoke(messages)
  -> LiteLLM
  -> 真实模型
```

工具调用循环继续消费 LangChain 标准化后的 `AIMessage.tool_calls`。模型别名 `backend` 必须通过工具调用
smoke test 后才能用于 agent 工作流。

### Market Watch / Health Check / Probe

```text
_request_llm_completion()
  -> build_chat_completion_kwargs()
  -> AsyncOpenAI(base_url=settings.LLM_BASE_URL)
  -> chat.completions.create(model=settings.LLM_MODEL)
```

健康检查、probe、Market Watch 和投研流程默认都使用 `backend` 别名。若以后需要拆分更多场景，
应先在 `config.py` 增加集中配置项，并同步扩展 LiteLLM 模型别名。

### MemoFlux Structured Recall

```text
OpenAICompatibleLLMClient
  -> AsyncOpenAI(base_url=settings.MEMOFLUX_LLM_BASE_URL)
  -> chat.completions.create(model=settings.MEMOFLUX_LLM_MODEL)
  -> LiteLLM
  -> 真实模型
```

MemoFlux 不依赖后端主应用的 provider 工厂。部署时只需要在
`memo/.env` 中将 `MEMOFLUX_LLM_*` 指向 LiteLLM。

## Provider-Specific 能力策略

后端不再直接适配 DeepSeek thinking 或其他供应商扩展参数。思考开关、推理强度、temperature 等默认行为
由 LiteLLM 模型别名配置承载；后端只选择 `backend`、`memory` 等别名。

策略如下：

- `backend` 只映射到已经通过工具调用 smoke test 的模型。
- `memory` 只映射到 JSON 输出稳定的模型。
- `backend` 默认配置为 thinking disabled，并设置适合常规 agent、JSON/摘要任务的 `temperature`。
- `backend-thinking` 配置为 thinking enabled，并设置 `reasoning_effort`，供需要 reasoning 内容的探活和思考场景使用。
- `memory` 只映射到 Memory 服务使用的稳定 JSON/摘要模型。
- 若某个供应商能力会破坏 OpenAI-compatible 工具调用 replay，则不能作为 `backend`。
- 若确实要测试 DeepSeek thinking、Anthropic thinking 或其他扩展能力，应创建单独模型别名，并通过 LiteLLM 配置隔离。
- LiteLLM provider 不读取 `LLM_THINKING_MODE`，也不生成 `extra_body.thinking`。
- 少数确实需要一次性透传 provider-specific 参数的调用点仍可传 `extra_body`，但不能用它作为全局 thinking 策略。
- 后端只消费 OpenAI-compatible 标准响应字段和 LangChain 标准化后的 `AIMessage.tool_calls`。

这确保模型切换不会把供应商专属逻辑重新带回业务代码。

## 错误处理与观测

后端继续使用现有 `record_llm_usage` 记录业务维度：

- workflow
- stage
- call_kind
- role
- cache_lane
- api_key_alias
- token usage

LiteLLM 负责网关侧观测：

- 模型路由。
- provider 错误。
- fallback 命中。
- 请求成本。
- 网关访问控制。

错误处理原则：

- LiteLLM 不可用时，后端应返回明确的 LLM gateway 连接错误，不吞掉异常。
- provider 返回不支持 tool calling 时，probe 和 smoke test 必须失败，不能在生产 agent 流程中静默降级。
- JSON 输出解析失败仍由现有重试逻辑处理，不由 LiteLLM 修正业务 schema。
- usage 字段缺失时允许记录空 token，但必须保留调用次数和角色维度。

## Docker Compose 设计

真实 `config.yaml` 不通过 bind mount 直接挂载，也不在构建阶段 COPY 进镜像：

- bind mount 可能因为宿主文件 owner/mode 与容器用户不一致而读取失败。
- COPY 进镜像会把真实 key 固化到镜像层和构建缓存，不利于密钥轮换和镜像分发。

推荐使用 Docker secrets/configs 或部署平台的 secret file 机制，在运行时把配置文件注入容器，并挂载到固定路径。

Docker Compose 服务示例：

```yaml
services:
  litellm:
    image: docker.litellm.ai/berriai/litellm-non_root:latest
    command: ["--config", "/run/secrets/litellm_config", "--port", "4000"]
    secrets:
      - litellm_config
    ports:
      - "4000:4000"

secrets:
  litellm_config:
    file: ./litellm/config.yaml
```

使用 LiteLLM 官方提供的 `litellm-non_root` 镜像，并为 Admin UI、virtual key 和 spend tracking 配置独立
PostgreSQL 数据库。数据库 URL 和 master key 都放在真实 `litellm/config.yaml` 中；该文件不提交到仓库，
部署时由 Docker secret 或平台 secret 注入。

如果本地 Docker Compose 的 secret 文件权限导致容器无法读取，应在宿主机把 `litellm/config.yaml`
设置为只读但对容器用户可读，例如 `chmod 0444 litellm/config.yaml`。生产环境优先使用平台原生 secret 管理。

后端使用 Docker 内部服务名访问：

```text
http://litellm:4000/v1
```

本地非 Docker 运行时，开发者可以通过本机 LiteLLM 进程暴露同样的 OpenAI-compatible endpoint：

```text
http://localhost:4000/v1
```

这些 endpoint 是部署拓扑，不作为项目 `.env` 中的 LLM 参数示例出现；后端代码统一从
`backend/app/core/config.py` 读取，禁止在调用点直接写 URL 字符串。

## 迁移步骤

1. 新增 `LiteLLMProvider`，并在 provider factory 注册 `litellm`。
2. 将 provider factory 默认支持项收敛为 `litellm`。
3. 删除 `DeepSeekProviderPlugin`、`DeepSeekChatOpenAI` 和 DeepSeek provider 专用常量。
4. 删除后端默认 thinking 注入逻辑，保留 provider 的显式 `extra_body` 透传能力。
5. 新增 `litellm/config.example.yaml`，并将真实 `litellm/config.yaml` 加入 `.gitignore`。
6. 在 Docker Compose 中新增 `litellm` 和 `litellm-postgres` 服务，使用 `litellm-non_root` 镜像和 Docker secret 注入真实配置。
7. 避免使用 volumes 直接挂载真实 `config.yaml`，也不要把真实配置 COPY 进镜像。
8. 在 `backend/app/core/config.py` 集中新增 LiteLLM URL、key、思考模型别名和非思考模型别名配置。
9. 修改部署文档，说明后端 `.env` 不再配置 LLM provider/model/key/base URL，模型配置统一放在 LiteLLM。
10. 将后端默认 OpenAI-compatible endpoint 指向 LiteLLM 内部服务；MemoFlux 通过 `memo/.env` 指向 LiteLLM。
11. 运行 LLM probe，验证普通文本、JSON、工具调用和 skills 工具。
12. 运行 Debate、Stock Picker、Experience Review 的 smoke test。
13. 确认 usage 统计在 LiteLLM 返回下仍能写入。

## 测试方案

### 单元测试

- `get_llm_provider("litellm")` 返回新 provider。
- `build_chat_model()` 从 `backend/app/core/config.py` 集中读取默认 model、api_key、base_url。
- `build_chat_completion_kwargs()` 不注入任何 provider-specific 字段。
- `build_chat_completion_kwargs()` 默认不注入任何 provider-specific 字段。
- 调用方传入 `extra_body` 时仅做原样透传。
- 代码检查不应在 agent、endpoint、service 中出现硬编码的 LiteLLM URL、key 或模型别名。
- 通用 invalid tool call 清理不会破坏合法 tool calls。

### 集成测试

- `/api/v1/llm/health` 通过 LiteLLM 返回正常响应。
- LLM one-click probe 覆盖普通文本、tool call、skills call。
- Market Watch AI gate 能通过 LiteLLM 获取 JSON 字符串。
- Memory structured worker 能解析 `response_format={"type": "json_object"}` 输出。

### Smoke Test

- 单股 Debate 至少完成一轮带工具调用的分析。
- Stock Picker 能对候选池完成工具调用并返回合法 JSON。
- Experience Review 能执行 memory tools 和普通工具并写出复盘结果。
- LiteLLM 停止服务时，后端返回可诊断错误。

## 风险与缓解

### 模型工具调用能力差异

统一 gateway 不能保证所有模型都稳定支持 tool calling。缓解方式是按服务边界定义模型别名，并要求 `backend`
必须通过工具调用 smoke test。

### Provider-specific 行为差异

不同供应商的扩展字段和工具调用 replay 语义可能不同。缓解方式是后端只支持 OpenAI-compatible 标准字段，
并要求所有 `backend` 候选模型通过工具调用 smoke test。

### Usage 字段格式差异

不同 provider 经 LiteLLM 后仍可能返回不同 usage 字段。缓解方式是复用现有宽松 usage 解析，缺失 token 时记录调用维度。

### Gateway 单点故障

LiteLLM 成为后端和 Memory LLM 访问的共同依赖。缓解方式是 Docker health check、明确错误提示、部署层重启策略和 LiteLLM
自身的 fallback 路由配置。后端不保留直连 provider 回退路径。

### 配置泄露

真实 provider key 集中在 `litellm/config.yaml` 中。缓解方式是只提交 `config.example.yaml`，真实 `config.yaml`
不进入版本库，并在部署文档中强调。

## 验收标准

- 后端主应用通过固定的 `litellm` provider 访问 LiteLLM。
- 后端 LiteLLM URL、key 和模型别名只在 `backend/app/core/config.py` 集中定义和读取。
- MemoFlux 服务通过 `memo/.env` 指向 LiteLLM 内部 endpoint 和 `memory` 模型别名。
- LiteLLM 通过 Docker secret 或部署平台 secret file 读取真实 `config.yaml`，不使用 volumes 直接挂载，也不把真实配置 COPY 进镜像。
- 后端项目 `.env` 示例不再出现 `LLM_PROVIDER`、`LLM_MODEL`、`LLM_API_KEY`、`LLM_BASE_URL` 等 LLM 参数。
- Memory 模块代码不变；部署时通过 `MEMORY_LLM_PROVIDER`、`MEMORY_LLM_MODEL`、`MEMORY_LLM_API_KEY`、
  `MEMORY_LLM_BASE_URL` 指向 LiteLLM。
- 后端不保留 `LLM_THINKING_MODE` 开关；thinking enabled/disabled 由 LiteLLM 模型别名配置决定。
- 调用方不再通过 `THINKING_ENABLED`、`THINKING_DISABLED` 等后端常量控制 thinking。
- LangChain `bind_tools` 工具调用循环在 `backend` 上通过 smoke test。
- Market Watch 和健康检查通过 OpenAI SDK 路径访问 LiteLLM。
- 切换真实模型时，不需要修改业务代码，只需要修改 LiteLLM `config.yaml`。
- LiteLLM 不依赖 `.env`，真实 key 和 `general_settings.master_key` 都在 `litellm/config.yaml` 中配置。
- Docker Compose 使用 LiteLLM `litellm-non_root` 镜像，并配置独立 PostgreSQL 数据库。
- `LLM_PROVIDER=deepseek` 不再可用，后端实际 provider 固定为 `litellm`。

## 参考资料

- LiteLLM Proxy Quick Start: https://docs.litellm.ai/docs/proxy/quick_start
- LiteLLM Supported Models & Providers: https://docs.litellm.ai/docs/providers
- LiteLLM Input Params: https://docs.litellm.ai/docs/completion/input
- LangChain ChatOpenAI integration: https://docs.langchain.com/oss/python/integrations/chat/openai
