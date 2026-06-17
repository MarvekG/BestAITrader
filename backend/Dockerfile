# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# 先拷贝 requirements 文件以利用镜像缓存
COPY requirements.txt .
COPY app/ai/agentic/tooling/news_plugins/requirements.txt requirements-news-plugins.txt
COPY app/ai/agentic/skills_loader/requirements.txt requirements-skills.txt

ARG TORCH_VERSION=2.10.0+cpu

# 安装项目基础工具 (uv, tzdata 等)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata postgresql-client-17 \
    && pip install uv -i https://mirrors.aliyun.com/pypi/simple/ \
    && useradd -m trader \
    && mkdir -p /runtime/logs /runtime/skills /runtime/news_plugins/external \
    && chown -R trader:trader /app /runtime \
    && rm -rf /var/lib/apt/lists/*

# 使用 uv 进行项目依赖安装，并挂载 pip 缓存
# 对 torch 固定使用国内镜像中的 CPU 轮子，避免在 Linux 上解析到 CUDA 依赖
RUN --mount=type=cache,target=/root/.cache/pip \
    uv pip install --system -i https://mirrors.aliyun.com/pypi/simple/ \
    --find-links https://mirrors.aliyun.com/pytorch-wheels/cpu/ \
    "torch==${TORCH_VERSION}" && \
    uv pip install --system -i https://mirrors.aliyun.com/pypi/simple/ \
    -r requirements.txt \
    -r requirements-news-plugins.txt \
    -r requirements-skills.txt

RUN python -c "import akshare, pathlib; print(pathlib.Path(akshare.__file__).parent / 'utils' / 'func.py')" | xargs sed -i 's/random.uniform(0.5, 1.5)/random.uniform(2.5, 5.0)/'

# 切换到非 root 用户
USER trader

# 复制代码和配置文件
COPY --chown=trader:trader . .

EXPOSE 8000

# 启动命令 (使用 run.py 以支持热重载)
CMD ["python", "run.py"]
