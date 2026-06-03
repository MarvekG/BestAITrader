# 部署指南

第一次启动只需要三步：clone 仓库、复制并修改配置、启动。

## 1. Clone 仓库

```bash
git clone <repo-url> Best-AI-Trader
cd Best-AI-Trader
git submodule update --init --recursive
```

启动前确认已安装 Docker Engine 和 Docker Compose v2：

```bash
docker version
docker compose version
```

## 2. 复制配置文件并修改配置

```bash
cp backend/.env.example backend/.env
cp memo/.env.example memo/.env
cp litellm/config.example.yaml litellm/config.yaml
```

修改 `backend/.env`，至少填写：

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

生成 `SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

修改 `litellm/config.yaml`，保留 `gpt-4o-mini`、`backend`、`backend-thinking`、`memory` 四个 `model_name`，只替换每个模型下的真实供应商配置：

```yaml
litellm_params:
  api_key: sk-your-provider-key
  api_base: https://api.your-provider.example
```

确认 `memo/.env` 中的 `MEMOFLUX_LLM_API_KEY` 等于 `litellm/config.yaml` 里的 `general_settings.master_key`。

不要提交真实的 `.env`、`litellm/config.yaml`、API key 或数据库数据。

## 3. 启动

```bash
docker compose up -d
docker compose ps
```

验证：

```bash
docker compose exec backend curl -f http://127.0.0.1:8000/health
curl -f http://localhost
curl -f http://localhost:4000/health/liveliness
```

访问：

- 主系统：`http://localhost`
- LiteLLM 管理系统：`http://localhost:4000/ui`

修改配置后不要只用 `restart`，需要重建对应服务：

```bash
docker compose up -d --force-recreate <service>
```
