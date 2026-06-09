# syntax=docker/dockerfile:1
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    WEB_RUNTIME_DIR=/runtime \
    PLAYWRIGHT_BROWSERS_PATH=/runtime/browsers/ms-playwright \
    XDG_CACHE_HOME=/runtime/cache \
    CLOAKBROWSER_CACHE_DIR=/runtime/cloakbrowser/cache

COPY requirements.txt .

RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates tzdata libgtk-3-0t64 libx11-xcb1 libasound2t64 \
    && pip install uv -i https://mirrors.aliyun.com/pypi/simple/ \
    && useradd -m webfetch \
    && mkdir -p /runtime/browsers/ms-playwright /runtime/cache /runtime/cloakbrowser/cache \
    && uv pip install --system -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt \
    && python -m playwright install-deps chromium \
    && chown -R webfetch:webfetch /usr/local/lib/python3.12/site-packages/camoufox \
    && chown -R webfetch:webfetch /app /runtime /home/webfetch \
    && rm -rf /var/lib/apt/lists/*

USER webfetch

RUN python -m cloakbrowser install \
    && python -m patchright install chromium \
    && python -m camoufox fetch

COPY --chown=webfetch:webfetch run.py .
COPY --chown=webfetch:webfetch app ./app

EXPOSE 8010

CMD ["python", "run.py"]
