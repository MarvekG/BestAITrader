# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

# 先拷贝 requirements 文件以利用镜像缓存
COPY requirements.txt .
COPY app/ai/agentic/tooling/news_plugins/requirements.txt requirements-news-plugins.txt
COPY app/ai/agentic/skills_loader/requirements.txt requirements-skills.txt

ARG PYODIDE_VERSION=0.29.3
ARG TORCH_VERSION=2.10.0+cpu

# 安装项目基础工具 (uv, tzdata 等)
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates bzip2 unzip tzdata postgresql-client-17 \
    && pip install uv -i https://mirrors.aliyun.com/pypi/simple/ \
    && useradd -m trader \
    && mkdir -p /home/trader/pyodide /app/runtime/logs \
    && chown -R trader:trader /app /home/trader/pyodide \
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

# 切换到非 root 用户
USER trader

# 安装 Deno (在 trader 用户目录下)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV PATH="/home/trader/.deno/bin:${PATH}"

# 将本地 build 目录带入构建上下文，优先使用其中的 Pyodide 归档
COPY --chown=trader:trader build/ /tmp/build/

# 优先使用本地归档；缺失时回退到官方发布地址下载
RUN if [ -f "/tmp/build/pyodide-${PYODIDE_VERSION}.tar.bz2" ]; then \
    cp "/tmp/build/pyodide-${PYODIDE_VERSION}.tar.bz2" /tmp/pyodide.tar.bz2; \
    else \
    curl -fsSL "https://github.com/pyodide/pyodide/releases/download/${PYODIDE_VERSION}/pyodide-${PYODIDE_VERSION}.tar.bz2" -o /tmp/pyodide.tar.bz2; \
    fi \
    && tar -xjf /tmp/pyodide.tar.bz2 -C /home/trader/pyodide --strip-components=1 \
    && rm -rf /tmp/pyodide.tar.bz2 /tmp/build

# 复制代码和配置文件
COPY --chown=trader:trader . .

EXPOSE 8000

# 启动命令 (使用 run.py 以支持热重载)
CMD ["python", "run.py"]
