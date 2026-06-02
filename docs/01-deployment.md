# 部署指南

默认使用 `docker-compose.yml` 拉取预构建镜像部署。

Windows 用户如果不想安装 Docker Desktop，建议走
[Windows WSL2 Docker Engine 部署指南](./04-windows-wsl-docker-engine-deployment.md)：在 WSL2 Ubuntu 内安装
Docker Engine 和 Docker Compose Plugin，再按本文的 Compose 部署方式启动项目。

## 1. 环境要求

- Linux、macOS 或 Windows WSL2
- Docker Engine / Docker Desktop
- Docker Compose v2
- Git

检查：

```bash
docker version
docker compose version
git --version
```

确认宿主机端口 `80` 未被占用。

## 2. 初始化项目

```bash
cd Best-AI-Trader
git submodule update --init --recursive
```

## 3. 配置

创建配置文件：

```bash
cp backend/.env.example backend/.env
cp memo/.env.example memo/.env
```

最少配置 `backend/.env`：

```env
FIRST_SUPERUSER=tradeuser
FIRST_SUPERUSER_EMAIL=tradeuser@example.com
FIRST_SUPERUSER_PASSWORD=<your-unique-password>
SECRET_KEY=<random-secret>

TUSHARE_API=http://api.waditu.com/dataapi
TUSHARE_TOKEN=your-tushare-token
TAVILY_API_KEY=your-tavily-api-key
NEWS_API_KEY=your-newsapi-key
```

> 上述 `FIRST_SUPERUSER_PASSWORD` 和 `SECRET_KEY` 不能留空；应用启动时由 Pydantic 校验。
> 生成 SECRET_KEY 示例：`python -c "import secrets; print(secrets.token_urlsafe(48))"`。

LiteLLM 是后端和记忆服务的统一 LLM 网关。后端 LLM 不再在 `backend/.env` 中配置 provider、模型、key 和
base URL；真实模型、真实 provider key 和供应商 base URL 只写到本地 `litellm/config.yaml`。

创建 LiteLLM 运行时配置：

```bash
cp litellm/config.example.yaml litellm/config.yaml
```

只需要修改 `litellm/config.yaml` 中这些必填项：

- `model_list[*].litellm_params.model`：真实上游模型名。
- `model_list[*].litellm_params.api_key`：真实 provider key。
- `model_list[*].litellm_params.api_base`：真实 provider base URL。

`general_settings.master_key` 保持示例默认值，不需要修改；下面 `MEMOFLUX_LLM_API_KEY` 填同一个值。

`model_name` 保持默认即可：

- `gpt-4o-mini`：LiteLLM UI Ask AI 的默认兼容别名。
- `backend`：后端默认非思考模型。
- `backend-thinking`：后端思考模型。
- `memory`：记忆服务模型。

MemoFlux 模块在 `memo/.env` 中把 `MEMOFLUX_LLM_*` 指向 LiteLLM：

```env
MEMOFLUX_LLM_MODEL=memory
MEMOFLUX_LLM_API_KEY=sk-litellm-gateway-key
MEMOFLUX_LLM_BASE_URL=http://litellm:4000/v1
```

其中 `MEMOFLUX_LLM_API_KEY` 必须等于 `litellm/config.yaml` 里的 `general_settings.master_key`。

Compose 会通过 Nginx 把 LiteLLM 暴露到宿主机 `4000` 端口。OpenAI-compatible API 地址是
`http://localhost:4000/v1`，管理界面地址是 `http://localhost:4000/ui`。

不要提交真实的 `.env` 文件。

## 4. 配置项说明

### 4.1 CORS 是否需要配置

默认 Docker Compose 部署通过 Nginx 对外暴露 `http://localhost`，前端页面和后端 API 都走同一个 Origin：

- 前端：`http://localhost`
- API：`http://localhost/api/v1/...`

这种同源部署不需要浏览器跨域访问，`BACKEND_CORS_ORIGINS` 可以保持默认空列表 `[]`。

只有在前端和后端分开部署时才需要配置 CORS，例如：

- 前端部署在 `https://trader.example.com`
- 后端 API 部署在 `https://api.example.com`
- 本地开发时前端开发服务器是 `http://localhost:5173`，后端是 `http://localhost:8000`

此时需要在 `backend/.env` 中显式写允许访问后端的前端 Origin：

```env
BACKEND_CORS_ORIGINS=["https://trader.example.com"]
```

本地开发可按实际端口配置：

```env
BACKEND_CORS_ORIGINS=["http://localhost:5173"]
```

不要配置为 `["*"]`。如果允许任意 Origin，浏览器中的恶意页面也能向后端发起跨域请求；在登录态、Bearer token
或未来 Cookie 配置处理不当时，会扩大攻击面。

### 4.2 密钥和初始账号

必须替换示例值：

| 配置项 | 说明 |
| --- | --- |
| `FIRST_SUPERUSER` | 初始化登录用户名。当前项目不开放注册，因此这是首次登录入口。 |
| `FIRST_SUPERUSER_EMAIL` | 初始化用户邮箱。 |
| `FIRST_SUPERUSER_PASSWORD` | 初始化用户密码。默认示例值为 `tradepassword`，部署前必须修改。 |
| `SECRET_KEY` | JWT 签名密钥。必须使用足够长的随机字符串，不要使用 `your-secret-key-here`。 |
| `litellm/config.yaml` | LLM provider、模型、真实 key 和 base URL 的唯一部署配置文件；不要提交。 |
| `MEMOFLUX_LLM_*` | MemoFlux LLM 配置；部署时指向 LiteLLM 的 `memory` 别名和 gateway key。 |
| `TUSHARE_TOKEN` | Tushare 数据源 token。部署方需自行确认数据源授权和使用边界。 |
| `TAVILY_API_KEY` | Tavily 搜索 API Key。 |
| `NEWS_API_KEY` | NewsAPI Key。必填。 |

修改 `backend/.env`、`memo/.env`、`litellm/config.yaml` 或 `nginx.conf` 后，不使用 `restart`。这些配置需要重新创建容器：

```bash
docker compose up -d --force-recreate backend
docker compose up -d --force-recreate memoflux
docker compose up -d --force-recreate litellm
docker compose up -d --force-recreate nginx
```

## 5. 启动

```bash
docker compose up -d
```

访问：

- 前端：`http://localhost`
- API 文档：`http://localhost/api/v1/docs`

## 6. 验证

```bash
docker compose exec backend curl -f http://127.0.0.1:8000/health
docker compose exec memoflux python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/v1/health', timeout=5).read()"
curl -f http://localhost
```

## 7. 常用命令

```bash
# 停止，保留数据卷
docker compose down

# 停止并删除数据卷
docker compose down -v

# 启动或应用镜像更新
docker compose up -d

# 修改配置后重新创建指定服务
docker compose up -d --force-recreate litellm
```

## 8. 排障

- 端口冲突：释放宿主机 `80`，或修改 `docker-compose.yml` 端口映射。
- 镜像拉取失败：检查 Docker registry / GHCR 网络访问。
- LLM 异常：确认 `litellm/config.yaml` 存在且包含 `gpt-4o-mini`、`backend`、`backend-thinking`、`memory` 四个模型别名。
- Memory 异常：确认 `memo/.env` 的 `MEMOFLUX_LLM_*` 已指向 LiteLLM。
- 容器间访问不要使用 `localhost`，使用 Compose 服务名，如 `postgres`、`redis`、`memoflux`。

## 9. 访问地址

- 主系统：`http://localhost`
- LiteLLM 管理系统：`http://localhost:4000/ui`
