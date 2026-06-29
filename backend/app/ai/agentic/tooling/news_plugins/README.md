# News Plugins Guide

本文档说明新闻插件如何管理、插件代码需要满足什么规范，以及如何编写和验证一个插件。

新闻插件负责把外部新闻、公告、政策、行业资讯、内部授权情报源接入统一的 `search_news` 工具。Agent 通过 `source + keyword + limit` 调用某一个插件。一个插件应该代表一个明确来源或渠道，不要把多个来源混在同一个插件里。

## 1. 系统组成

| 层级 | 作用 | 关键文件 |
| --- | --- | --- |
| 插件实现 | 每个来源一个 Python 模块 | `tavily.py`、`external/*.py` |
| 管理服务 | 上传、删除、导入前探针、刷新注册表 | `manager.py` |
| 注册中心 | 扫描插件、校验元数据、构造工具说明 | `registry.py` |
| 后端接口 | 暴露插件管理 API | `app/api/endpoints/news_plugins.py` |
| Agent 工具 | LangChain tool，暴露为 `search_news` | `news_tool.py` |

运行链路：

```text
系统设置上传 .py 插件
  -> manager 写入 external/
  -> registry 重新扫描并注册
  -> manager 调用插件 search(keyword="AI", 最近 30 天) 做导入前探针
  -> 探针成功后保留插件，失败则回滚
  -> Agent 在 search_news 的 Sources 中看到新 source
  -> Agent 调用 search_news(keyword, source, limit)
```

## 2. 新闻插件如何管理

### 2.1 管理入口

在前端进入：

```text
系统设置 -> 新闻插件管理
```

支持：

- 查看已注册插件。
- 一次上传一个或多个 `.py` 插件文件。
- 全选可删除插件。
- 批量删除选中的 external 插件。
- 删除单个插件。

内置插件不能在界面删除。只有位于 `/runtime/news_plugins/external/` 的插件会标记为可删除。

### 2.2 后端接口

接口前缀：

```text
/api/v1/news-plugins
```

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/news-plugins` | 列出已注册新闻插件 |
| `POST` | `/news-plugins` | 上传一个或多个 `.py` 插件文件，表单字段名为 `files` |
| `DELETE` | `/news-plugins/{plugin_key}` | 删除 external 插件，`plugin_key` 可用 `PLUGIN_ID` 或文件模块名 |

上传接口只接受 UTF-8 编码的 `.py` 文件。插件名由文件名推导，例如上传 `internal_news.py`，模块名就是 `internal_news`。
批量上传时后端会逐个处理文件，并返回整体结果：

- 全部成功：`status = "success"`
- 部分成功：`status = "partial_success"`
- 全部失败：`status = "error"`

前端会根据返回结果展示成功提示或失败详情。

### 2.3 文件位置

注册器扫描：

```text
backend/app/ai/agentic/tooling/news_plugins/*.py
/runtime/news_plugins/external/*.py
```

跳过：

- `__init__.py`
- `base.py`
- `manager.py`
- `registry.py`
- `provider_clients.py`

推荐把业务插件放在：

```text
/runtime/news_plugins/external/
```

`/runtime/news_plugins/external/` 是运行环境中的插件目录，不作为仓库内容提交。生产环境需要持久化或挂载 `/runtime`，否则容器重建后上传的插件会丢失。

### 2.4 导入前探针

上传插件后，系统会先注册插件，然后调用：

```python
await search(
    keyword="AI",
    limit=3,
    from_date="<今天 - 30 天>",
    to_date="<今天>",
)
```

探针失败时会回滚文件，不会保留该插件。失败条件包括：

- 插件无法 import。
- 元数据缺失或格式不正确。
- `search` 抛出异常。
- `search` 返回值不是 `list`。
- 返回列表第一项包含 `error` 或 `fatal`。其中 `fatal` 表示插件或上游来源不可用。

## 3. 插件文件命名规范

上传文件名必须是合法 Python 模块名：

```text
^[a-z][a-z0-9_]{1,63}\.py$
```

示例：

```text
internal_news.py
cninfo_announcements.py
official_policy.py
```

不要使用：

```text
NewsPlugin.py
news-plugin.py
test.py
plugin1.py
```

以下模块名为系统保留名，不能上传：

```text
__init__.py
base.py
manager.py
provider_clients.py
registry.py
```

## 4. 插件元数据规范

每个插件模块必须定义：

```python
NAME = "示例新闻源"
PLUGIN_ID = "example_news"
TOOL_NAME = "示例新闻源搜索"
NEWS_TYPES = ["公司新闻", "行业新闻", "政策新闻"]
KEYWORD_EXAMPLES = ["贵州茅台 业绩", "新能源 政策", "半导体 出口限制"]
```

字段要求：

| 字段 | 要求 | 作用 |
| --- | --- | --- |
| `NAME` | 非空字符串 | 给用户和 Agent 看的来源名称 |
| `PLUGIN_ID` | 非空且唯一 | `search_news.source` 使用的稳定 id |
| `TOOL_NAME` | 非空字符串 | 工具说明中的名称 |
| `NEWS_TYPES` | 非空 `list[str]` | 告诉 Agent 这个来源适合什么新闻 |
| `KEYWORD_EXAMPLES` | 非空 `list[str]` | 给 Agent 生成关键词时参考 |

`PLUGIN_ID` 规则：

- 使用短小稳定的 snake_case。
- 不要带环境名，例如不要用 `market_source_prod`。
- 不要带版本号，例如不要用 `cninfo_v2`。
- 不要和已有插件重复。
- 上线后尽量不要改名，否则历史 prompt、日志和使用习惯会失效。

## 5. search 函数规范

插件必须提供异步 `search` 函数：

```python
async def search(
    keyword: str,
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
) -> list[dict[str, object]]:
    ...
```

参数说明：

| 参数 | 说明 |
| --- | --- |
| `keyword` | Agent 生成的关键词，建议支持 `公司/股票/主题 + 事件词` |
| `limit` | 返回结果上限，插件必须尊重 |
| `from_date` | 开始日期，格式 `YYYY-MM-DD`；来源接口不支持时也必须接收并安全忽略 |
| `to_date` | 结束日期，格式 `YYYY-MM-DD`；来源接口不支持时也必须接收并安全忽略 |

输入处理要求：

```python
keyword = str(keyword or "").strip()
limit = max(1, min(int(limit or 10), 20))
if not keyword:
    return []
```

不要把 `keyword` 直接拼接进 SQL、shell 命令或不受控表达式。

## 6. 输出结构规范

`search` 必须返回 `list[dict[str, object]]`。

推荐字段：

```python
{
    "title": "新闻标题",
    "url": "https://example.com/news/1",
    "content": "新闻正文片段或摘要",
    "publish_date": "2026-05-09 09:30:00",
    "source": "example_news",
    "type": "company_news",
    "author": "来源作者或机构",
    "symbols": ["600519.SH"],
    "confidence": 0.9,
}
```

字段说明：

| 字段 | 要求 | 说明 |
| --- | --- | --- |
| `title` | 必填 | 新闻标题，空标题应丢弃 |
| `url` | 建议 | 原文链接，公告/政策源尤其重要 |
| `content` | 建议 | 正文片段、摘要或结构化内容 |
| `publish_date` | 建议 | 来源发布时间，建议 `YYYY-MM-DD HH:MM:SS` |
| `source` | 建议 | 建议填 `PLUGIN_ID`，注册器也会兜底补齐 |
| `type` | 可选 | `company_news`、`policy_news`、`announcement` 等 |
| `symbols` | 可选 | 关联股票代码 |
| `confidence` | 可选 | 插件内部对匹配质量的评分 |

错误结果：

```python
[{"error": "错误说明", "source": PLUGIN_ID}]
```

插件不可用错误：

```python
[{"error": "错误说明", "source": PLUGIN_ID, "fatal": True}]
```

错误语义要求：

- `fatal=True` 只用于插件不可用或上游来源不可用，例如 token 缺失、API key 过期、HTTP 401/403/429/5xx、超时、网络错误、响应结构变化导致无法解析。
- 关键词没有命中新闻、筛选后没有可用文章、空关键词等业务性失败不要标记 `fatal`。
- 测试中心和插件管理会把首项 `fatal=True` 作为明确的来源不可用错误展示，不会再改写成 `No news found`。

插件不应抛出未处理异常。注册器会兜底捕获异常，但插件内部返回可读错误更利于排障。

## 7. 质量要求

正式使用的新闻插件至少应满足：

- `PLUGIN_ID` 稳定且唯一。
- `NEWS_TYPES` 能准确表达来源适用范围。
- `KEYWORD_EXAMPLES` 能指导 Agent 生成好关键词。
- 空关键词不会请求外部服务。
- 每个请求都有 timeout。
- 每个返回结果有 `title`。
- 能处理 HTTP 错误、超时和格式变化。
- 插件不可用时返回带 `fatal=True` 的错误结果，无结果时不标记 `fatal`。
- 输出数量受 `limit` 控制。
- 输出正文长度受控。
- `search` 必须是异步函数。
- 联网请求使用异步 HTTP 客户端，不使用同步 HTTP 接口。
- 抓取多篇新闻详情时使用 `asyncio.gather` 并发抓取，并控制并发数。
- 不在模块 import 阶段发网络请求。
- 不在公开仓库、共享仓库或交付代码中提交真实 token。
- 不绕过来源授权限制。

## 8. 依赖和 Token

新闻插件依赖维护在：

```text
backend/app/ai/agentic/tooling/news_plugins/requirements.txt
```

Docker 构建后端镜像时会同时安装 `backend/requirements.txt` 和本目录的新闻插件依赖文件。新增第三方依赖时，优先修改本目录的 `requirements.txt`。

如果是通过系统设置上传的新插件需要额外依赖，在插件源码中声明 `PYTHON_REQUIREMENTS`：

```python
PYTHON_REQUIREMENTS = [
    "beautifulsoup4",
    "feedparser",
]
```

后端会在插件写入和导入前用当前应用用户执行 `python -m pip install --user -r <临时 requirements 文件>`，
安装失败时不会启用该插件。

由于依赖会安装到后端容器当前应用用户的 Python user site 中，默认不要指定版本号，尽量复用容器内已有版本，避免覆盖
主系统、其他新闻插件或 Skills 正在使用的依赖。只有在插件明确依赖某个 API 且已验证不会影响现有系统时，才考虑写版本约束。

运行时安装只影响当前容器环境。需要在重建镜像后仍然生效时，应同步更新
`backend/app/ai/agentic/tooling/news_plugins/requirements.txt` 并重新构建后端镜像。

推荐 HTTP 客户端：

- `httpx.AsyncClient`
- `aiohttp`

不要在插件中使用同步 HTTP 客户端，例如 `requests`、`httpx.Client`、`urllib.request`。

如果插件需要 token，优先从运行环境变量读取，或从插件目录下未提交的本地文件读取。不要提交真实 token。

示例：

```python
import os
from pathlib import Path


TOKEN_FILE = Path(__file__).resolve().parent / ".secrets" / "internal_news_token.txt"


def read_token() -> str:
    token = os.getenv("INTERNAL_NEWS_TOKEN", "").strip()
    if token:
        return token
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
```

## 9. 完整插件样例

下面是一个 API 型新闻插件样例。它只依赖标准库和 `httpx`，不引用项目内部配置、日志或业务模块。

```python
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

NAME = "内部授权新闻 API"
PLUGIN_ID = "internal_news"
TOOL_NAME = "内部授权新闻搜索"
NEWS_TYPES = ["授权财经新闻", "内部资讯", "公司事件"]
KEYWORD_EXAMPLES = ["贵州茅台 回购", "半导体 订单", "地产 债务"]

API_URL = "https://internal.example.com/news/search"
TOKEN_FILE = Path(__file__).resolve().parent / ".secrets" / "internal_news_token.txt"
TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_LIMIT = 20


def _read_token() -> str:
    token = os.getenv("INTERNAL_NEWS_TOKEN", "").strip()
    if token:
        return token
    try:
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _clean_limit(limit: int) -> int:
    return max(1, min(int(limit or 10), MAX_LIMIT))


def _format_error(message: str, fatal: bool = False) -> list[dict[str, Any]]:
    item: dict[str, Any] = {"error": message, "source": PLUGIN_ID}
    if fatal:
        item["fatal"] = True
    return [item]


async def search(
    keyword: str,
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
) -> list[dict[str, Any]]:
    """Search authorized internal news."""
    keyword = str(keyword or "").strip()
    limit = _clean_limit(limit)
    if not keyword:
        return _format_error("No keyword provided for internal_news")

    token = _read_token()
    if not token:
        return _format_error("Internal news token is not configured", fatal=True)

    payload = {
        "keyword": keyword,
        "limit": limit,
        "from_date": from_date,
        "to_date": to_date,
    }

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                API_URL,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException:
        return _format_error("Internal news API timeout", fatal=True)
    except httpx.HTTPError as exc:
        return _format_error(f"Internal news API failed: {exc}", fatal=True)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in data.get("items", [])[:limit]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue

        url = str(item.get("url") or "").strip()
        dedupe_key = url or title
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        results.append({
            "title": title,
            "url": url,
            "content": str(item.get("summary") or item.get("content") or "").strip()[:2000],
            "publish_date": str(item.get("published_at") or "").strip(),
            "source": PLUGIN_ID,
            "type": str(item.get("type") or "vendor_news"),
            "symbols": item.get("symbols") or [],
        })

    return results
```

## 10. 排障

插件没有出现在列表中：

- 检查上传文件是否是 `.py`。
- 检查文件名是否是合法 Python 模块名。
- 检查元数据字段是否完整。
- 检查 `NEWS_TYPES` 和 `KEYWORD_EXAMPLES` 是否是非空 `list[str]`。
- 检查 import 是否报错。
- 检查 `PLUGIN_ID` 是否重复。

上传后被回滚：

- 检查 `search(keyword="AI", limit=3, from_date=..., to_date=...)` 是否能正常执行。
- 检查 `search` 是否返回 `list`。
- 检查返回首项是否包含 `error` 或 `fatal`。
- 检查 token、网络、API 授权和限流。
- 如果返回 `fatal=True`，按插件不可用处理，优先查 token/API key、HTTP 状态码、网络和上游响应格式。

Agent 不会选择你的插件：

- 检查 `NEWS_TYPES` 是否写得太泛。
- 检查 `KEYWORD_EXAMPLES` 是否贴近真实问题。
- 检查 `NAME` 是否清晰。
- 查看 `build_search_news_docstring()` 输出是否包含该插件。

插件返回为空：

- 检查关键词是否被来源支持。
- 检查目标 API 响应结构是否变化。
- 检查是否触发限流。
- 检查是否把 `limit` 或分页参数传错。
- 如果是 API key、token、授权、超时、限流或响应格式变化导致不可用，应返回 `fatal=True` 错误，而不是空列表或普通无结果错误。

插件导致 Agent 上下文过大：

- 截断 `content`。
- 限制 `limit`。
- 返回摘要而不是全文。
- 对重复 URL 去重。

## 11. 给 AI 的开发指令

当 AI 需要开发新闻插件时，按以下顺序执行：

1. 确认目标新闻源的授权、接口、字段和频率限制。
2. 设计 `PLUGIN_ID`、`NEWS_TYPES` 和 `KEYWORD_EXAMPLES`。
3. 实现插件，保持模块 import 轻量，不引用项目内部配置、日志或业务模块。
4. 如果需要 token，使用环境变量或插件自己的本地文件读取。
5. 联网请求使用 `httpx.AsyncClient` 或 `aiohttp`。
6. 如果需要抓取多篇详情页，使用 `asyncio.gather` 并发抓取，并用 semaphore 控制并发数。
7. 如果需要新增第三方依赖，更新本目录的 `requirements.txt`。
8. 用 mock 响应写测试。
9. 运行 compileall、注册检查和插件调用。
10. 最终说明新增了哪些来源、适合什么新闻、需要哪些密钥、如何验证。
