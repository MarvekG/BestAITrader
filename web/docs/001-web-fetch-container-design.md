# Web 抓取容器设计

## 1. 目标

新增独立 `web` 容器，对内提供网页渲染、内容选择、HTML/Markdown 转换和 Markdown 正则清理能力。主后端只通过 HTTP 调用该容器，不直接管理浏览器生命周期。

该容器用于替代主后端中逐步膨胀的浏览器工具链，但不改变现有 Agent 对网页内容的使用方式。

## 2. 非目标

- 不接入 Firecrawl。
- 不实现搜索、站点爬取、URL 队列或批量任务调度。
- 不在本阶段接入商业 Web Unlocker 或云浏览器。
- 不把该服务做成可被主后端 import 的 Python 包。
- 不在 fetch 接口中支持写入文件、下载文件或执行用户自定义 JavaScript。

## 3. 容器边界

目录固定为：

```text
web/
  Dockerfile
  requirements.txt
  run.py
  app/
    main.py
    config.py
    schemas.py
    engines/
      base.py
      cloakbrowser_engine.py
      patchright_engine.py
      camoufox_engine.py
    services/
      cleaner.py
      renderer.py
      limiter.py
```

Compose 服务名固定为 `web`，容器名建议为 `best_ai_trader_web`。主后端通过 `http://web:8010` 访问。

默认不通过公网 Nginx 暴露该服务；如需本地调试，可在 `docker-compose.dev.yml` 中映射 `8010:8010`。

## 4. API 设计

### 4.1 健康检查

```http
GET /health
```

返回：

```json
{
  "status": "ok"
}
```

### 4.2 Fetch 网页抓取

```http
POST /fetch
```

请求体 JSON：

| 参数 | 类型 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `url` | string | 是 | 无 | 目标 URL，缺少协议时按 `https://` 处理 |
| `selectors` | list[string] | 否 | 空 | CSS selector 列表；为空返回完整 DOM |
| `markdown_clean_regexes` | list[string] | 否 | 空 | Markdown 清理正则列表，按顺序删除匹配片段 |
| `engine` | enum | 否 | `cloakbrowser` | `patchright`、`cloakbrowser`、`camoufox` |
| `return_type` | enum | 否 | `markdown` | `html` 或 `markdown` |
| `timeout_ms` | int | 否 | `60000` | 页面导航超时时间 |
| `wait_after_ms` | int | 否 | `5000` | 导航完成后等待 JS 渲染时间 |

请求示例：

```json
{
  "url": "https://example.com/article",
  "selectors": ["main", ".content"],
  "markdown_clean_regexes": ["广告.*?免责声明"],
  "engine": "cloakbrowser",
  "return_type": "markdown",
  "timeout_ms": 60000,
  "wait_after_ms": 5000
}
```

返回示例：

```json
{
  "success": true,
  "url": "https://example.com/article",
  "final_url": "https://example.com/article",
  "status": 200,
  "title": "Example Article",
  "engine": "cloakbrowser",
  "return_type": "markdown",
  "selectors": ["main", ".content"],
  "selected_element_count": 2,
  "content": "# Example Article...",
  "content_length": 37392,
  "source_html_length": 166024,
  "content_source": "rendered_dom_markdown"
}
```

错误返回示例：

```json
{
  "success": false,
  "url": "file:///etc/passwd",
  "engine": "cloakbrowser",
  "return_type": "markdown",
  "error": "only http and https URLs are supported"
}
```

## 5. 参数规则

### 5.1 URL

- 只允许 `http` 和 `https`。
- 必须包含 hostname。
- 缺少协议时自动补 `https://`。
- 本阶段不禁止内网地址，因为主后端当前浏览器工具允许访问本地地址；后续若暴露公网，必须增加 SSRF 防护。

### 5.2 Selectors

- 使用 JSON 数组传入：`"selectors": ["main", ".article"]`。
- 支持空数组或缺省，表示返回完整 DOM。
- 不支持逗号分隔字符串，避免 CSS selector 内逗号造成歧义。
- 选择逻辑与现有 `browser_tool.py` 保持一致：按 selector 顺序取 `document.querySelectorAll`，同一 DOM 元素只输出一次，拼接 `outerHTML`。
- 如果 selector 无匹配，返回空内容和 `selected_element_count=0`，不自动 fallback 到整页。

### 5.3 Markdown 正则清理

- 仅作用于 `return_type=markdown` 的结果。
- 使用 JSON 数组传入：`"markdown_clean_regexes": ["pattern1", "pattern2"]`。
- 按传入顺序执行 `re.sub(pattern, "", markdown, flags=re.MULTILINE | re.DOTALL)`。
- 正则编译失败时返回 `400`，不静默跳过。
- 不支持用户传入 replacement，避免把接口变成通用文本重写器。

## 6. 引擎设计

### 6.1 公共接口

每个引擎实现同一组能力：

```python
async def render(
    url: str,
    selectors: list[str],
    timeout_ms: int,
    wait_after_ms: int,
) -> RenderedPage
```

`RenderedPage` 至少包含：

- `final_url`
- `status`
- `title`
- `html`
- `selected_element_count`

HTML 到 Markdown 的转换不放在引擎中，由公共 `renderer` 统一处理。

### 6.2 CloakBrowser

CloakBrowser 引擎必须原封不动迁移现有已验证逻辑：

- `launch_context_async(headless=True, viewport={"width": 1365, "height": 900}, locale="zh-CN", timezone="Asia/Shanghai")`
- 进程级共享 browser context。
- 使用 `asyncio.Lock` 保护 context 启动。
- 使用 `asyncio.BoundedSemaphore` 限制并发 page 数。
- 记录 `_active_pages`，容器 shutdown 时逐个关闭。
- `page.goto(..., wait_until="domcontentloaded", timeout=timeout_ms)`。
- `wait_after_ms` 非零时调用 `page.wait_for_timeout(wait_after_ms)`。
- selector 提取逻辑使用现有 `document.querySelectorAll` 与去重逻辑。

迁移目标不是重写或优化 CloakBrowser，而是把现有稳定能力从主后端隔离到 `web` 容器。

### 6.3 Patchright

Patchright 作为 Chromium Playwright 兼容引擎实现：

- 使用独立 context 生命周期，不复用 CloakBrowser context。
- 默认 viewport、locale、timezone 与 CloakBrowser 对齐。
- 默认使用 headless 模式；如目标站检测严重，可通过环境变量切换 headful/Xvfb。
- 初始实现只需要达到与 CloakBrowser 相同的 `render` 输出契约。

### 6.4 Camoufox

Camoufox 作为 Firefox 反检测备选引擎实现：

- 使用 Camoufox Python async API。
- 默认 viewport、locale、timezone 与其他引擎尽量对齐。
- 初始实现只处理 URL 渲染和 DOM 提取，不做额外指纹策略配置。
- 如果运行环境缺少 Camoufox 浏览器二进制，启动时不失败；第一次请求该引擎时返回明确错误。

## 7. 引擎限流

每个引擎独立限流，不共享同一个全局 semaphore。

环境变量：

```env
WEB_CLOAKBROWSER_MAX_PAGES=10
WEB_PATCHRIGHT_MAX_PAGES=4
WEB_CAMOUFOX_MAX_PAGES=2
WEB_DEFAULT_TIMEOUT_MS=60000
WEB_DEFAULT_WAIT_AFTER_MS=5000
```

限流策略：

- 请求进入 `/fetch` 后，根据 `engine` 获取对应 `BoundedSemaphore`。
- 获取 semaphore 后才创建 page/context。
- 超过并发上限时请求等待，而不是直接拒绝。
- 后续如需要防止堆积，可增加 `WEB_ENGINE_ACQUIRE_TIMEOUT_MS`，超时返回 `429`。

## 8. 内容转换与清理

HTML 返回：

- `return_type=html` 时直接返回 selector 后 HTML 或整页 HTML。
- 不做 HTML sanitization，避免破坏页面结构。

Markdown 返回：

- 使用 `markdownify` 将 HTML 转换为 Markdown。
- 保留现有格式：顶部加入标题和 Source URL。
- 转换完成后再执行 `markdown_clean_regexes`。
- 返回 `source_html_length`，便于判断清洗前后的信息量。

内容质量判断不在 `web` 容器中做，只返回基础元信息。上层可根据 `title`、`content_length`、关键字段和反爬提示自行判断是否升级引擎。

## 9. Docker 与 Compose

`web/Dockerfile` 基于 `python:3.12-slim`。

基础依赖：

- `fastapi`
- `uvicorn[standard]`
- `pydantic-settings`
- `markdownify`
- `cloakbrowser[geoip]`
- `patchright`
- `camoufox`

系统依赖：

- 复用主后端 Dockerfile 中 Playwright 浏览器依赖安装方式。
- 如 Camoufox 需要额外系统库，在 `web/Dockerfile` 中单独补齐，不污染主后端镜像。

生产 Compose 增加：

```yaml
web:
  build:
    context: ./web
    dockerfile: Dockerfile
  container_name: best_ai_trader_web
  restart: unless-stopped
  environment:
    TZ: Asia/Shanghai
    WEB_CLOAKBROWSER_MAX_PAGES: 10
    WEB_PATCHRIGHT_MAX_PAGES: 4
    WEB_CAMOUFOX_MAX_PAGES: 2
    WEB_RELOAD: "false"
    WEB_RUNTIME_DIR: /runtime
    CLOAKBROWSER_CACHE_DIR: /runtime/cloakbrowser/cache
    PLAYWRIGHT_BROWSERS_PATH: /runtime/browsers/ms-playwright
    XDG_CACHE_HOME: /runtime/cache
  volumes:
    - web_runtime_data:/runtime
  healthcheck:
    test: ["CMD", "curl", "-f", "http://127.0.0.1:8010/health"]
  networks:
    - best_ai_trader_network
```

开发 Compose 可额外增加：

```yaml
ports:
  - "8010:8010"
volumes:
  - ./web:/app
  - web_runtime_data:/runtime
```

`run.py` 统一封装 Uvicorn 启动参数：固定监听 `0.0.0.0:8010`，固定使用 `asyncio` loop；开发 Compose 通过 `WEB_RELOAD=true` 开启 `/app` 热重载，生产 Compose 默认关闭热重载。

`web_runtime_data` 是唯一持久化卷，浏览器安装文件和运行 cache 都归到该卷：

- Patchright Chromium：`/runtime/browsers/ms-playwright`
- Camoufox 浏览器、GeoIP 和 fontconfig cache：`/runtime/cache/camoufox`
- CloakBrowser cache：`/runtime/cloakbrowser/cache`

Patchright Chromium、Camoufox 浏览器和 CloakBrowser Chromium 在镜像构建阶段下载到 `/runtime`。Docker 首次把空的 `web_runtime_data` 命名卷挂载到 `/runtime` 时，会自动把镜像目录内已有内容复制进卷；后续重建容器会复用同一个卷，避免反复下载浏览器安装文件。

`run.py` 显式使用 `loop="asyncio"`，避免 `uvicorn[standard]` 默认启用 `uvloop` 时影响 CloakBrowser 子进程管道。

如果 `web_runtime_data` 已经在旧版本中创建且内容为空，Docker 不会再次从新镜像回填；需要删除该卷或手动重新创建后再启动 `web` 服务。

## 10. 后端集成

主后端后续新增配置：

```env
WEB_FETCH_SERVICE_BASE_URL=http://web:8010
WEB_FETCH_DEFAULT_ENGINE=cloakbrowser
```

集成方式：

- 保留现有 Agent 工具函数签名。
- 后端 `browser_tool.py` 可逐步改为 HTTP client，调用 `web` 容器 `/fetch`。
- 在迁移完成前，主后端现有 CloakBrowser 工具仍可作为 fallback。

## 11. 安全边界

该容器具备访问网页和内网地址能力，默认只能在 Docker 内网访问。

必须遵守：

- 不通过根 `nginx.conf` 暴露 `/fetch` 到公网。
- 如未来公开，必须增加鉴权、SSRF 防护、host allowlist/denylist、请求体大小限制和速率限制。
- 不记录完整 HTML/Markdown 到日志，避免泄露页面内容、Cookie 或供应商 payload。
- 不允许用户传入任意 JavaScript。
- 不允许下载文件写入宿主目录。

## 12. 测试计划

单元测试：

- URL normalize：协议补全、非法协议、缺 hostname。
- selector normalize：空值、重复参数、空白过滤。
- markdown regex：多规则顺序清理、非法正则返回错误。
- engine limiter：不同 engine 使用不同 semaphore。
- CloakBrowser 迁移逻辑：context 复用、page 自动关闭、shutdown 关闭 active pages。

集成测试：

- mock engine 返回 HTML，验证 `/fetch?return_type=html`。
- mock engine 返回 HTML，验证 `/fetch?return_type=markdown` 和正则清理。
- 指定不存在 engine 返回 `400`。

手工验证：

```bash
docker compose -f docker-compose.dev.yml up -d --build web
curl 'http://localhost:8010/health'
curl -X POST 'http://localhost:8010/fetch' \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://example.com/article",
    "engine": "cloakbrowser",
    "return_type": "markdown",
    "selectors": ["body"]
  }'
```

## 13. 分阶段实施

### 阶段一：容器骨架和 CloakBrowser

- 新增 `web` 目录、Dockerfile、FastAPI 服务。
- 原封不动迁移 CloakBrowser context/page 管理。
- 实现 `/health` 和 `/fetch`。
- 接入 Compose，但不修改主后端调用链。

### 阶段二：Patchright 和 Camoufox

- 新增 Patchright 引擎。
- 新增 Camoufox 引擎。
- 每个引擎独立限流。
- 对静态页面、动态页面和 selector 提取场景做手工验证。

### 阶段三：主后端切换

- 后端新增 `WEB_FETCH_SERVICE_BASE_URL`。
- `browser_tool.py` 优先调用 `web` 容器。
- 旧 CloakBrowser 本地实现保留为短期 fallback。
- 验证市场观察、Agent 工具、PDF/浏览器相关测试。

## 14. 待确认问题

- `web` 容器是否需要 API key 鉴权，即使只在内网访问。
- fetch 请求体是否需要增加 `wait_until`，目前为保持稳定固定 `domcontentloaded`。
- 是否需要对返回内容设置最大长度，避免大页面撑爆调用方上下文。
- Patchright 和 Camoufox 是否允许 headful/Xvfb，还是统一 headless。
- 是否需要为不同站点配置默认 engine 和 selector。
