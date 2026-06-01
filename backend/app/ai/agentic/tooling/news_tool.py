import re
from typing import Any, Dict, List
from urllib.parse import urlsplit, urlunsplit

from langchain.tools import tool
from pydantic import BaseModel, Field

from app.ai.agentic.tooling.news_plugins import (
    build_search_news_docstring,
    get_available_news_sources,
    invoke_news_plugin,
)

SEARCH_NEWS_DESCRIPTION = build_search_news_docstring()
MAX_SEARCH_NEWS_LIMIT = 20


class SearchNewsInput(BaseModel):
    keyword: str = Field(..., description="搜索关键词，优先使用 `主题/公司/股票 + 事件词` 格式。")
    source: str = Field(
        ...,
        description=(
            "必填，且一次只能选择一个新闻来源。"
            f"当前可用来源: {', '.join(get_available_news_sources())}"
        ),
    )
    limit: int = Field(10, ge=1, le=MAX_SEARCH_NEWS_LIMIT, description="返回结果上限，范围 1-20。")
    from_date: str = Field(
        ...,
        description="必填，搜索起始日期，格式 YYYY-MM-DD（如 2026-05-01）。",
    )
    to_date: str = Field(
        ...,
        description="必填，搜索结束日期，格式 YYYY-MM-DD（如 2026-05-09）。",
    )


async def _search_news_impl(
    keyword: str,
    source: str,
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
) -> List[Dict[str, Any]]:
    if not keyword or not keyword.strip():
        return [{
            "error": "keyword is required for search_news",
            "source": source,
        }]
    if not source or not source.strip():
        return [{
            "error": "source is required for search_news",
            "available_sources": get_available_news_sources(),
            "source": source,
        }]
    results = await invoke_news_plugin(
        source=source.strip(),
        keyword=keyword.strip(),
        limit=min(max(int(limit or 10), 1), MAX_SEARCH_NEWS_LIMIT),
        from_date=from_date.strip(),
        to_date=to_date.strip(),
    )
    return _compact_news_result(results)


def _compact_news_result(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Deduplicate and cap news search results before they enter agent context.

    Args:
        results: Plugin-normalized news rows.

    Returns:
        At most 20 compact rows, preserving plugin error payloads unchanged.
    """
    if not isinstance(results, list):
        return results
    if any(isinstance(item, dict) and item.get("error") for item in results):
        return results

    compacted: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue

        title_key = _normalize_news_title(item.get("title"))
        url_key = _normalize_news_url(item.get("url"))
        if url_key and url_key in seen_urls:
            continue
        if title_key and title_key in seen_titles:
            continue

        row = dict(item)
        if not any(value not in (None, "") for value in row.values()):
            continue

        compacted.append(row)
        if url_key:
            seen_urls.add(url_key)
        if title_key:
            seen_titles.add(title_key)
        if len(compacted) >= MAX_SEARCH_NEWS_LIMIT:
            break
    return compacted


def _normalize_news_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _normalize_news_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))


_search_news_impl.__doc__ = SEARCH_NEWS_DESCRIPTION
search_news = tool("search_news", args_schema=SearchNewsInput)(_search_news_impl)
search_news.description = SEARCH_NEWS_DESCRIPTION
