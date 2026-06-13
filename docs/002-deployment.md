# 部署指南

推荐使用根目录的交互式部署脚本。脚本只依赖 Python 标准库，会校验外部 API Key，生成本地配置，然后一键启动 Docker Compose。

## 1. 准备环境

确认已安装 Docker Engine、Docker Compose v2 和 Python 3.11+：

```bash
docker version
docker compose version
python3 --version
```

获取代码：

```bash
git clone <repo-url> Best-AI-Trader
cd Best-AI-Trader
git submodule update --init --recursive
```

## 2. 准备密钥

运行脚本前准备以下信息：

- 初始超级用户密码：至少 12 位。
- LLM API Base URL、API Key、供应商真实模型名：要求兼容 OpenAI `chat/completions` 接口。
- LiteLLM provider/model 写法：例如 DeepSeek 可填 `deepseek/deepseek-v4-flash`；后端仍使用固定别名 `backend`、
  `backend-thinking` 和 `memory`。
- Tushare Token：用于 A 股数据接入，建议确认已有足够接口权限。
- Tavily API Key：用于搜索。
- NewsAPI API Key：用于新闻检索。

申请地址：

- Tushare：https://tushare.pro/
- Tavily：https://www.tavily.com/
- NewsAPI：https://newsapi.org/

## 3. 一键部署

在项目根目录运行：

```bash
python3 deploy.py
```

脚本会执行以下步骤：

1. 交互式读取初始用户、LLM、Tushare、Tavily 和 NewsAPI 配置。
2. 调用对应服务验证 Key 是否可用；验证失败会要求重新输入。
   Tushare 默认用低门槛 `daily` 日线接口校验；如果返回频率超限，脚本会视为 Token 已被服务端识别但当前暂时限流，并允许继续。
3. 生成 `backend/.env`、`memo/.env`、`litellm/config.yaml`。
4. 执行 `docker compose pull`、`docker compose up -d`、`docker compose ps`。
5. 检查 backend、sandbox、webfetch 和 memo 健康状态，并用生成的 LiteLLM master key 调用 `backend` 模型别名。

已有本地配置时，脚本会先询问是否覆盖。确认要覆盖也可以直接运行：

```bash
python3 deploy.py --overwrite
```

只生成配置、不启动容器：

```bash
python3 deploy.py --no-start
```

启动后跳过健康检查：

```bash
python3 deploy.py --no-health-check
```

## 4. 访问系统

部署完成后访问：

- 主系统：`http://localhost`
- LiteLLM 管理系统：`http://localhost:4000/ui`

默认生成的后端配置会关闭运行时扩展和 OpenAPI 文档。`/api/v1/testing/*` 仍按后端鉴权边界挂载。需要临时安装
Skill 或新闻插件时，手动修改 `backend/.env` 后重建后端容器。

## 5. 常用命令

查看服务：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f backend
docker compose logs -f litellm
```

修改配置后重建服务，不要只用 `restart`：

```bash
docker compose up -d --force-recreate backend memo litellm
```

停止服务：

```bash
docker compose down
```

备份主数据库：

```bash
scripts/database-maintenance.sh backup
```

恢复主数据库：

```bash
scripts/database-maintenance.sh restore backups/best-ai-trader-trading-YYYYMMDD-HHMMSS.dump
```

## 6. 注意事项

- 生产或公网部署前建议按 `SECURITY.md` 收紧 Nginx、LiteLLM 暴露面、上传大小、超时和访问控制。
- `sandbox`、`webfetch` 和 `scrapling.mcp` 默认只在 Compose 内部网络访问。
- 当前 Compose 使用已发布镜像，不需要在本机源码构建镜像。
