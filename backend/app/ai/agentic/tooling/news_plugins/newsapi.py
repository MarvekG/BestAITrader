"""
Search NewsAPI web results，适合全球英文新闻检索和特定事件追踪。

Best for:
- 全球主流英文媒体新闻
- 按发布时间排序的实时新闻
- 特定公司/事件/主题的跨媒体报道

Keyword guidance:
- 推荐格式: `主题/公司/事件 + 英文关键词`
- Examples: `Apple earnings Q1 2025`, `Federal Reserve interest rate decision`, `NVIDIA AI chip demand`

Coverage note:
- 基于 NewsAPI，覆盖全球 30,000+ 新闻源
- 免费版返回摘要和链接，每日 100 次请求额度
- 结果以英文为主，中文覆盖较少

Source traits:
- 数据来源正规，适合需要权威媒体引用的场景
- 发布时间精确，适合时效性追踪
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.core.config import settings
from app.core.data_source_config_cache import get_data_source_config_list
from app.core.data_source_settings import NEWS_API_KEY_SETTING_KEY
from app.core.logger import get_logger

from app.ai.agentic.tooling.news_plugins.base import format_error
from app.ai.agentic.tooling.news_plugins.provider_clients import ProviderRequestError, request_with_key_failover

logger = get_logger(__name__)

NAME = "NewsAPI 全球新闻搜索"
PLUGIN_ID = "newsapi"
TOOL_NAME = "NewsAPI 全球新闻搜索"
NEWS_TYPES = ["全球新闻检索", "英文媒体报道", "实时事件追踪"]
KEYWORD_EXAMPLES = [
    "AI",
]
PYTHON_REQUIREMENTS = ["httpx"]
TIMEOUT = httpx.Timeout(settings.DEFAULT_HTTP_TIMEOUT, connect=10.0)


async def search(
    keyword: str,
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
    **kwargs,
) -> list[dict[str, Any]]:
    """
    Search NewsAPI as a news plugin.

    Args:
        keyword: Search keyword.
        limit: Maximum number of results to return.
        from_date: Start date in YYYY-MM-DD format (e.g., 2026-05-01).
        to_date: End date in YYYY-MM-DD format (e.g., 2026-05-09).

    Returns:
        Normalized NewsAPI search results.
    """
    api_keys = ",".join(get_data_source_config_list(NEWS_API_KEY_SETTING_KEY))
    if not api_keys:
        logger.warning("NEWS_API_KEY is not configured.")
        return format_error("NEWS_API_KEY is not configured", PLUGIN_ID, fatal=True)

    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    params = {
        "q": keyword,
        "sortBy": "publishedAt",
        "language": "zh",
        "pageSize": limit,
        "from": from_date or week_ago,
        "to": to_date or today,
    }
    max_retries = 3

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async def request_once(api_key: str) -> httpx.Response:
                    request_params = {**params, "apiKey": api_key}
                    return await client.get("https://newsapi.org/v2/everything", params=request_params)

                response = await request_with_key_failover("NewsAPI", api_keys, request_once)
                if response is None:
                    return format_error("NEWS_API_KEY is not configured", PLUGIN_ID, fatal=True)
                data = response.json()

                if data.get("status") != "ok":
                    logger.warning("NewsAPI returned non-ok status: %s", data.get("message"))
                    return format_error(
                        f"NewsAPI returned non-ok status: {data.get('message') or data.get('status')}",
                        PLUGIN_ID,
                        fatal=True,
                    )

                results = []
                for article in data.get("articles", []):
                    title = article.get("title", "")
                    description = article.get("description", "")
                    url = article.get("url", "")
                    published_at = article.get("publishedAt", "")
                    source_name = article.get("source", {}).get("name", "")

                    # Skip removed/invalid articles
                    if title in ("[Removed]", None) or not url:
                        continue

                    content = description or ""

                    results.append({
                        "title": title,
                        "content": content,
                        "url": url,
                        "published_at": published_at,
                        "publisher": source_name,
                        "source": "newsapi",
                    })
                return results
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt < max_retries - 1:
                logger.warning("NewsAPI search attempt %s failed: %s. Retrying...", attempt + 1, exc)
                await asyncio.sleep(attempt + 1)
                continue
            logger.error("NewsAPI search failed after %s attempts: %s", max_retries, exc)
            return format_error(f"NewsAPI request failed: {exc}", PLUGIN_ID, fatal=True)
        except ProviderRequestError as exc:
            logger.warning("NewsAPI provider request failed: %s", exc)
            return format_error(str(exc), PLUGIN_ID, fatal=True)
        except Exception as exc:
            logger.exception("Unexpected error in NewsAPI search: %s", exc)
            return format_error(f"Unexpected error in NewsAPI search: {exc}", PLUGIN_ID, fatal=True)
    return []
