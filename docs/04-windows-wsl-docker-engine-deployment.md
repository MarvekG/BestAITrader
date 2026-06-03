# Windows WSL2 Docker Engine 环境指南

本文说明如何在 Windows 上使用 WSL2 Ubuntu 内的 Docker Engine 和 Docker Compose
Plugin 准备天枢智投（Best-AI-Trader）的运行环境。

这是 Windows 用户的推荐方案。它保留 Docker Compose 一体化部署的稳定性，同时减少桌面后台、
许可证、GUI 和资源管理方式。应用配置和服务启动步骤不在本文维护，统一见 [部署指南](./01-deployment.md)。

## 1. 推荐环境结构

```text
Windows
└── WSL2 Ubuntu
    ├── Docker Engine
    ├── Docker Compose Plugin
    └── Best-AI-Trader
        ├── Nginx             -> 暴露到 Windows 浏览器
        ├── Frontend          -> React/Vite 前端
        ├── Backend           -> FastAPI 后端
        ├── PostgreSQL        -> 主业务库
        ├── Redis             -> 缓存和任务辅助
        ├── MemoFlux          -> 长期记忆服务
        └── Memo PostgreSQL   -> pgvector 记忆库
```

浏览器仍然在 Windows 里打开：

```text
http://localhost
```

如果宿主机 80 端口被占用，则改用：

```text
http://localhost:8080
```

## 2. 为什么不建议原生 Windows 手动部署

天枢智投（Best-AI-Trader）不是一个单进程应用。完整部署至少包含后端、前端、Nginx、PostgreSQL、Redis、pgvector 和
MemoFlux。原生 Windows 手动部署会遇到这些问题：

- Python、Node、PostgreSQL、pgvector、Redis 和浏览器运行时需要分别安装和维护。
- 本项目的 Compose 健康检查、服务名网络、命名卷和容器内路径天然按 Linux 容器设计。
- pgvector 和数据库扩展在 Windows 原生环境下更容易遇到版本和编译问题。
- 长期运行、重启恢复、备份恢复和日志排查会比 Compose 模式复杂。
- 后续升级镜像比维护一组 Windows 原生服务更省心。

因此 Windows 上更稳妥的路线是：只把 Windows 作为桌面和浏览器环境，把运行环境交给 WSL2 Ubuntu。

## 3. 前置要求

### 3.1 Windows 版本

建议使用：

- Windows 11；或
- Windows 10 22H2 及以上，并安装新版 WSL。

检查 WSL：

```powershell
wsl --version
wsl --status
```

如果命令不存在或版本过旧，先更新：

```powershell
wsl --update
```

### 3.2 硬件建议

最低起步配置：

| 项目 | 建议 |
| --- | --- |
| CPU | 8 核 |
| 内存 | 8 GB |
| 磁盘 | 80 GB 及以上 SSD 空间 |

如果要大量同步行情数据、运行长期记忆、保留较多历史数据或同时做开发调试，可以在 8 GB 基础上继续增加内存和 SSD 空间。

### 3.3 网络要求

需要能访问：

- GitHub 和 GitHub Container Registry，用于拉取代码、子模块和镜像。
- Docker 官方 apt 仓库，用于安装 Docker Engine。
- 所选 LLM 服务商，例如 DeepSeek。
- Tushare、Tavily、NewsAPI 等外部数据源。

如果所在网络访问 GHCR 或 Docker 仓库不稳定，首次启动拉取镜像时可能失败。

## 4. 安装 WSL2 Ubuntu

在 Windows PowerShell 中执行。建议使用普通 PowerShell；如果系统提示需要管理员权限，再以管理员身份打开。

查看可安装发行版：

```powershell
wsl --list --online
```

安装 Ubuntu 24.04：

```powershell
wsl --install -d Ubuntu-24.04
```

安装完成后，第一次进入 Ubuntu 会要求创建 Linux 用户名和密码。这个密码是 WSL Ubuntu 内的 `sudo` 密码，
不是 Windows 密码，也不是项目登录密码。

确认 WSL 版本是 2：

```powershell
wsl -l -v
```

如果 Ubuntu 不是 VERSION 2，执行：

```powershell
wsl --set-version Ubuntu-24.04 2
```

进入 Ubuntu：

```powershell
wsl -d Ubuntu-24.04
```

后续命令除特别说明外，都在 Ubuntu 终端中执行。

## 5. 启用 systemd

Docker Engine 推荐由 systemd 管理。新版 WSL 通常已默认支持 systemd，但建议显式检查：

```bash
ps -p 1 -o comm=
```

如果输出是 `systemd`，可以跳过本节剩余步骤。

如果不是，编辑 `/etc/wsl.conf`：

```bash
sudo vi /etc/wsl.conf
```

写入：

```ini
[boot]
systemd=true
```

保存后，在 Windows PowerShell 中关闭所有 WSL 实例：

```powershell
wsl --shutdown
```

重新进入 Ubuntu：

```powershell
wsl -d Ubuntu-24.04
```

再次检查：

```bash
ps -p 1 -o comm=
```

## 6. 准备 Ubuntu 基础工具

更新系统包：

```bash
sudo apt-get update
sudo apt-get upgrade -y
```

安装基础工具：

```bash
sudo apt-get install -y ca-certificates curl git gnupg lsb-release openssl vim
```

确认时间和时区。项目容器默认使用 `Asia/Shanghai`，WSL 主机不强制要求同一时区，但建议保持一致：

```bash
timedatectl
```

如果需要调整：

```bash
sudo timedatectl set-timezone Asia/Shanghai
```

## 7. 安装 Docker Engine 和 Compose Plugin

本节分两步：

1. Docker Engine、Docker CLI、containerd 和 buildx 通过 Docker 官方 Ubuntu apt 仓库安装。
2. Docker Compose Plugin 不通过 apt 安装，改为从 Docker Compose GitHub Releases 拉取最新二进制，并校验
   `.sha256` 文件。

不要安装 `docker.io` 或 `docker-compose` 旧包。

### 7.1 清理可能存在的旧包

这是风险操作，但建议执行。它会移除 Ubuntu 仓库、旧版 Compose、Podman 兼容层或旧 containerd/runc 包，避免后续
Docker 官方包和旧包混装。只在这个 WSL 发行版专门用于本项目或 Docker 开发时执行；如果该 WSL 里已经有其他容器
工作负载，先确认这些包不是其他任务依赖。

```bash
for pkg in docker.io docker-doc docker-compose docker-compose-v2 docker-compose-plugin podman-docker containerd runc; do
  sudo apt-get remove -y "$pkg" || true
done
```

### 7.2 添加 Docker 官方 GPG key

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

### 7.3 添加 Docker 官方 apt 源

```bash
architecture="$(dpkg --print-architecture)"
codename="$(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
docker_gpg_path="/etc/apt/keyrings/docker.asc"
docker_repo_url="https://download.docker.com/linux/ubuntu"

printf 'deb [arch=%s signed-by=%s] %s %s stable\n' \
  "$architecture" \
  "$docker_gpg_path" \
  "$docker_repo_url" \
  "$codename" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

更新索引：

```bash
sudo apt-get update
```

### 7.4 安装 Docker Engine

```bash
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
```

启动 Docker：

```bash
sudo systemctl enable --now docker
```

检查版本：

```bash
docker version
```

如果当前用户没有权限访问 Docker socket，先临时用 `sudo docker version` 验证 Docker 是否正常，再看下一节。

### 7.5 从 GitHub Releases 安装最新 Compose Plugin

Docker Compose Plugin 是 Docker CLI 插件，Linux 下可以放在当前用户的 `~/.docker/cli-plugins/docker-compose`。
下面命令会从 GitHub `releases/latest` 下载当前最新版本，并下载对应 `.sha256` 文件做校验。

先确认架构：

```bash
uname -m
```

大多数 Intel/AMD Windows 机器输出 `x86_64`。如果你的机器输出 `x86_64`，执行：

```bash
mkdir -p "$HOME/.docker/cli-plugins"
curl -fSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
  -o "$HOME/.docker/cli-plugins/docker-compose"
curl -fSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64.sha256 \
  -o /tmp/docker-compose-linux-x86_64.sha256
sed 's#docker-compose-linux-x86_64#docker-compose#' /tmp/docker-compose-linux-x86_64.sha256 \
  | (cd "$HOME/.docker/cli-plugins" && sha256sum -c -)
chmod +x "$HOME/.docker/cli-plugins/docker-compose"
docker compose version
```

如果你的机器输出 `aarch64`，执行：

```bash
mkdir -p "$HOME/.docker/cli-plugins"
curl -fSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64 \
  -o "$HOME/.docker/cli-plugins/docker-compose"
curl -fSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-aarch64.sha256 \
  -o /tmp/docker-compose-linux-aarch64.sha256
sed 's#docker-compose-linux-aarch64#docker-compose#' /tmp/docker-compose-linux-aarch64.sha256 \
  | (cd "$HOME/.docker/cli-plugins" && sha256sum -c -)
chmod +x "$HOME/.docker/cli-plugins/docker-compose"
docker compose version
```

如果以后要更新 Compose Plugin，重复执行本小节对应架构命令即可。`releases/latest` 的好处是能拿到最新版本；
代价是版本会随 GitHub 最新 release 变化。需要可复现部署时，把命令 URL 中的：

```text
https://github.com/docker/compose/releases/latest/download
```

替换成固定版本地址，例如：

```text
https://github.com/docker/compose/releases/download/v2.40.3
```

固定版本号应以 Docker Compose GitHub Releases 页面实际发布版本为准。

## 8. 配置非 root Docker 使用权限

把当前 Linux 用户加入 `docker` 组：

```bash
sudo usermod -aG docker "$USER"
```

让当前终端立即加载新组：

```bash
newgrp docker
```

验证：

```bash
docker run --rm hello-world
```

如果仍然提示权限不足，退出 Ubuntu 终端，在 Windows PowerShell 中执行：

```powershell
wsl --shutdown
```

然后重新进入 Ubuntu 再执行：

```bash
docker ps
```

注意：加入 `docker` 组意味着该 Linux 用户可以控制 Docker daemon，等价于拥有较高系统权限。只应在个人可信
WSL 环境中这样配置。

## 9. 配置 WSL 资源限制

可以通过 Windows 用户目录下的 `.wslconfig` 控制 WSL2 资源。

在 WSL Ubuntu 中用 `vi` 编辑 Windows 用户目录下的 `.wslconfig`。先确认 Windows 用户目录：

```bash
ls /mnt/c/Users
```

然后打开配置文件，把 `<你的Windows用户名>` 替换成实际目录名：

```bash
vi "/mnt/c/Users/<你的Windows用户名>/.wslconfig"
```

建议写入：

```ini
[wsl2]
memory=8GB
processors=8
```

保存后重启 WSL：

```powershell
wsl --shutdown
```

重新进入 Ubuntu 后检查：

```bash
free -h
nproc
```

## 10. 获取项目代码

项目目录必须放在 WSL Linux 文件系统内，不要放在 Windows 盘、`/mnt/c/...` 或 `/home/...`。本文统一使用
`/opt/codes/Best-AI-Trader`。这样可以避开 Windows 文件系统的 I/O、权限、换行符和 bind mount 性能问题。

默认部署不在本机源码构建镜像，而是直接拉取已经构建好的镜像。这里拉取仓库主要是为了获得 `docker-compose.yml`、
`nginx.conf`、`.env.example` 和项目文档。

创建代码目录并放开权限：

```bash
sudo mkdir -p /opt/codes
sudo chmod 777 /opt/codes
cd /opt/codes
```

克隆仓库：

```bash
git clone https://github.com/MarvekG/BestAITrader.git Best-AI-Trader
cd Best-AI-Trader
```

给项目目录开放读写执行权限，避免开发模式挂载源码时容器内的非 root 用户无法读取代码：

```bash
sudo chmod -R 777 /opt/codes/Best-AI-Trader
```

如果已经从 Windows 下载了 zip 或放在 C 盘，仍然需要重新 clone 到 `/opt/codes`。不要直接在
`/mnt/c/Users/...` 下运行 Compose。

初始化子模块：

```bash
git submodule update --init --recursive
```

确认关键文件存在：

```bash
ls -la
ls -la backend/.env.example memo/.env.example docker-compose.yml nginx.conf
```

## 11. 下一步

到这里，Windows WSL2、Docker Engine、Docker Compose Plugin 和项目代码已经准备完成。应用配置、LiteLLM 配置、启动服务、停止服务和修改配置后的重建命令统一维护在：

```text
docs/01-deployment.md
```

继续执行部署时，在 WSL Ubuntu 中保持当前项目目录：

```bash
cd /opt/codes/Best-AI-Trader
```

然后按 [部署指南](./01-deployment.md) 完成 `.env`、`litellm/config.yaml` 和 Compose 启动。

## 12. WSL 常见问题

### 12.1 `docker compose` 命令不存在

确认 Docker CLI 能识别当前用户目录下的 Compose 插件：

```bash
ls -l "$HOME/.docker/cli-plugins/docker-compose"
docker compose version
```

如果文件不存在，回到第 7.5 节，重新从 Docker Compose GitHub Releases 下载并安装。注意命令是 `docker compose`，不是旧版 `docker-compose`。

### 12.2 Docker 权限不足

如果出现 permission denied，确认当前用户已经加入 `docker` 组：

```bash
groups
```

如果没有 `docker`，回到第 8 节重新配置。配置后退出 Ubuntu，并在 Windows PowerShell 中执行：

```powershell
wsl --shutdown
```

重新进入 Ubuntu 后再验证：

```bash
docker run --rm hello-world
```

### 12.3 不要在 Windows 盘运行 Compose

项目应位于 WSL Linux 文件系统，例如：

```text
/opt/codes/Best-AI-Trader
```

不要在 `/mnt/c/Users/...` 下运行 Compose。Windows 盘会带来 I/O 慢、权限差异、换行符和 bind mount 问题。

## 13. 官方参考

- WSL systemd: https://learn.microsoft.com/windows/wsl/systemd
- Docker Engine on Ubuntu: https://docs.docker.com/engine/install/ubuntu/
- Docker Compose plugin: https://docs.docker.com/compose/install/linux/
- Docker Linux post-install: https://docs.docker.com/engine/install/linux-postinstall/
