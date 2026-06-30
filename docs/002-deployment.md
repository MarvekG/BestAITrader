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
- LiteLLM provider/model 写法：例如 DeepSeek 可填 `deepseek/deepseek-v4-flash`；系统默认暴露固定别名
  `gpt-4o-mini`、`openai-compatible` 和 `openai-compatible-thinking`。

Tushare、Tavily、NewsAPI 配置不在部署阶段采集。部署完成后进入 UI 系统设置 > 数据源设置填写：

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

1. 交互式读取初始用户和 LLM 配置。
2. 调用 OpenAI-compatible `chat/completions` 接口验证 LLM Key 和模型是否可用；验证失败会要求重新输入。
3. 生成 `backend/.env`、`memo/.env`、`litellm/config.yaml`。
4. 执行 `docker compose pull`、`docker compose up -d`、`docker compose ps`。
5. 检查 backend、sandbox、webfetch 和 memo 健康状态，并用生成的 LiteLLM master key 调用 `openai-compatible` 模型别名。

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

默认生成的后端配置会关闭 OpenAPI 文档。`/api/v1/testing/*`、新闻插件和 Skills 管理仍按后端鉴权边界挂载。

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

备份主系统数据库和 Memo 数据库。脚本会先提示将停止 `backend`、`memo`，输入 `BACKUP` 确认后继续，完成后自动拉起服务：

```bash
scripts/database-maintenance.sh backup
```

恢复主系统数据库和 Memo 数据库。脚本会先提示将停止 `backend`、`memo`，输入 `RESTORE` 确认后继续，完成后自动拉起服务：

```bash
scripts/database-maintenance.sh restore backups/bat.YYYYMMDD.HHMMSS
```

## 6. 注意事项

- 生产或公网部署前建议按 `SECURITY.md` 收紧 Nginx、LiteLLM 暴露面、上传大小、超时和访问控制。
- `sandbox`、`webfetch` 和 `scrapling.mcp` 默认只在 Compose 内部网络访问。
- 当前 Compose 使用已发布镜像，不需要在本机源码构建镜像。
