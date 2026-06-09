# syntax=docker/dockerfile:1
FROM denoland/deno:bin AS deno

FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    SANDBOX_RUNTIME_DIR=/runtime \
    SANDBOX_PYODIDE_ROOT=/home/sandbox/pyodide

COPY requirements.txt .

ARG PYODIDE_VERSION=0.29.3

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates bzip2 unzip tzdata \
    && pip install uv -i https://mirrors.aliyun.com/pypi/simple/ \
    && useradd -m sandbox \
    && mkdir -p /home/sandbox/pyodide /runtime/logs \
    && chown -R sandbox:sandbox /app /home/sandbox/pyodide /runtime \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/pip \
    uv pip install --system -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt

USER sandbox

COPY --from=deno --chown=sandbox:sandbox /deno /home/sandbox/.deno/bin/deno
ENV PATH="/home/sandbox/.deno/bin:${PATH}"

COPY --chown=sandbox:sandbox build/ /tmp/build/

RUN if [ -f "/tmp/build/pyodide-${PYODIDE_VERSION}.tar.bz2" ]; then \
    cp "/tmp/build/pyodide-${PYODIDE_VERSION}.tar.bz2" /tmp/pyodide.tar.bz2; \
    else \
    curl -fsSL "https://github.com/pyodide/pyodide/releases/download/${PYODIDE_VERSION}/pyodide-${PYODIDE_VERSION}.tar.bz2" -o /tmp/pyodide.tar.bz2; \
    fi \
    && tar -xjf /tmp/pyodide.tar.bz2 -C /home/sandbox/pyodide --strip-components=1 \
    && rm -rf /tmp/pyodide.tar.bz2 /tmp/build

COPY --chown=sandbox:sandbox run.py .
COPY --chown=sandbox:sandbox config ./config
COPY --chown=sandbox:sandbox app ./app

EXPOSE 8030

CMD ["python", "run.py"]
