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
3. 生成 `backend/.env`、`litellm/config.yaml`。
4. 执行 `docker compose pull`、`docker compose up -d`、`docker compose ps`。
5. 检查 backend、sandbox、webfetch 健康状态，并用生成的 LiteLLM master key 调用 `openai-compatible` 模型别名。

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
docker compose up -d --force-recreate backend litellm
```

## 6. 数据库迁移

backend 的 schema 由 Alembic 管理，应用启动不再执行 `Base.metadata.create_all()`。

全新数据库、已有数据库首次接管和后续版本升级均由 backend 容器入口自动完成：

1. 空数据库自动执行 `alembic upgrade head`。
2. 首次接入已有数据库时，管理员需先备份并手动删除已废弃表；应用不负责删除历史表。
3. 清理后启动 backend，入口会校验表集合、自动 `stamp` baseline、应用 reconciliation revision，并校验完整 schema。
4. 表、列、索引、约束、默认值或类型存在差异时迁移失败，backend 不会启动，也不会静默覆盖数据库。
5. 后续 revision 随 backend 镜像发布，容器启动时自动执行 `alembic upgrade head`。

用户无需手动执行 Alembic。迁移日志可通过以下命令查看：

```bash
docker compose logs backend
```

首次接入当前主库时，需由管理员在备份后直接执行 DROP DDL。删除对象清单见 Alembic 设计文档第 3.4 节；该清理不进入代码和 Alembic revision。

停止服务：

```bash
docker compose down
```

备份主系统数据库。脚本会先提示将停止 `backend`，输入 `BACKUP` 确认后继续，完成后自动拉起服务：

```bash
scripts/database-maintenance.sh backup
```

恢复主系统数据库。脚本会先提示将停止 `backend`，输入 `RESTORE` 确认后继续，完成后自动拉起服务：

```bash
scripts/database-maintenance.sh restore backups/bat.YYYYMMDD.HHMMSS
```

## 7. 注意事项

- 生产或公网部署前建议按 `SECURITY.md` 收紧 Nginx、LiteLLM 暴露面、上传大小、超时和访问控制。
- `sandbox`、`webfetch` 和 `scrapling.mcp` 默认只在 Compose 内部网络访问。
- 当前 Compose 使用已发布镜像，不需要在本机源码构建镜像。
