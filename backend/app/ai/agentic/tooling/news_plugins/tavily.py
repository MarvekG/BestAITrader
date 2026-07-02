"""
Search Tavily web results，适合补充跨来源、开放网页和不确定站点的新闻检索。

Best for:
- 跨来源新闻和网页结果
- 不确定具体财经站点时的兜底检索
- 较长自然语言查询、海外或综合市场语境

Keyword guidance:
- 推荐格式: `主题/公司/股票 + latest/today/事件词`
- Examples: `AI`

Coverage note:
- 基于 Tavily 搜索 API，不限定单一财经站点；结果广但来源质量需要交叉验证

Source traits:
- 覆盖面广，适合快速发现线索
- 适合作为垂直财经源之外的补充新闻插件
"""

import asyncio
import re
from typing import Any

import httpx

from app.core.config import settings
from app.core.data_source_config_cache import get_data_source_config_list
from app.core.data_source_settings import TAVILY_API_KEY_SETTING_KEY
from app.core.logger import get_logger

from app.ai.agentic.tooling.news_plugins.base import format_error
from app.ai.agentic.tooling.news_plugins.provider_clients import ProviderRequestError, request_with_key_failover

logger = get_logger(__name__)

NAME = "Tavily 通用新闻搜索"
PLUGIN_ID = "tavily"
TOOL_NAME = "Tavily 通用新闻搜索"
NEWS_TYPES = ["跨来源新闻检索", "开放网页线索", "通用市场和公司事件搜索"]
KEYWORD_EXAMPLES = [
    "AI",
]
PYTHON_REQUIREMENTS = ["httpx"]
TIMEOUT = httpx.Timeout(settings.DEFAULT_HTTP_TIMEOUT, connect=10.0)
WEAK_QUERY_TERMS = {
    "news", "latest", "today", "market", "finance", "policy", "related", "update",
    "新闻", "财经", "市场", "行业", "行业政策", "政策", "政策解读", "相关", "最新",
    "情况", "影响", "分析", "公司", "个股", "公告", "披露", "报道", "消息",
}


def _normalize_inline_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def _extract_search_terms(keyword: str) -> list[str]:
    raw_terms = re.split(r"[\s,/，、；;|]+", _normalize_inline_text(keyword))
    selected: list[str] = []
    seen = set()
    for term in raw_terms:
        cleaned = term.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        selected.append(cleaned)
        if len(selected) >= 6:
            break
    return selected


def _is_weak_query_term(term: str) -> bool:
    cleaned = _normalize_inline_text(term).lower()
    if not cleaned:
        return True
    if cleaned in WEAK_QUERY_TERMS:
        return True
    if re.fullmatch(r"(?:19|20)\d{2}年?", cleaned):
        return True
    if re.fullmatch(r"(?:19|20)\d{2}[qQ][1-4]|[qQ][1-4]", cleaned):
        return True
    if len(cleaned) == 1 and not re.search(r"[a-z0-9]", cleaned):
        return True
    return False


def _term_matches_text(term: str, text: str) -> bool:
    if not term or not text:
        return False
    cleaned = _normalize_inline_text(term)
    haystack = text.lower()
    if re.fullmatch(r"[A-Za-z0-9]{1,6}", cleaned):
        return re.search(rf"(?<![A-Za-z0-9]){re.escape(cleaned)}(?![A-Za-z0-9])", text, re.I) is not None
    return cleaned.lower() in haystack


def _is_specific_core_term(term: str) -> bool:
    cleaned = _normalize_inline_text(term)
    if re.fullmatch(r"\d{6}", cleaned):
        return True
    if re.fullmatch(r"[A-Za-z0-9]+", cleaned):
        return len(cleaned) >= 3
    return len(re.findall(r"[\u4e00-\u9fff]", cleaned)) >= 3 or len(cleaned) >= 4


def _is_relevant_match(keyword: str, terms: list[str], *parts: str) -> bool:
    haystack = "\n".join(_normalize_inline_text(part) for part in parts if part)
    if not haystack:
        return False
    normalized_keyword = _normalize_inline_text(keyword)
    if normalized_keyword and _term_matches_text(normalized_keyword, haystack):
        return True
    core_terms = [term for term in terms if not _is_weak_query_term(term)]
    if not core_terms:
        return any(_term_matches_text(term, haystack) for term in terms)
    matched_terms = [term for term in core_terms if _term_matches_text(term, haystack)]
    if len(matched_terms) < min(2, len(core_terms)):
        return False
    specific_terms = [term for term in core_terms if _is_specific_core_term(term)]
    return len(core_terms) < 3 or not specific_terms or any(term in matched_terms for term in specific_terms)


async def search(
    keyword: str,
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
    **kwargs,
) -> list[dict[str, Any]]:
    """
    Search Tavily as a news plugin.

    Args:
        keyword: Search keyword.
        limit: Maximum number of results to return.
        from_date: Start date in YYYY-MM-DD format.
        to_date: End date in YYYY-MM-DD format.

    Returns:
        Normalized Tavily search results.
    """
    api_keys = await get_data_source_config_list(TAVILY_API_KEY_SETTING_KEY)
    return await search_with_api_keys(api_keys, keyword, limit, from_date, to_date)


async def search_with_api_keys(
    api_keys: list[str],
    keyword: str,
    limit: int = 10,
    from_date: str = "",
    to_date: str = "",
) -> list[dict[str, Any]]:
    """
    使用指定 API Key 列表执行 Tavily 搜索。

    Args:
        api_keys: API Key 列表。
        keyword: 搜索关键词。
        limit: 最大返回结果数。
        from_date: 开始日期。
        to_date: 结束日期。

    Returns:
        标准化后的 Tavily 搜索结果。
    """
    if not api_keys:
        logger.warning("TAVILY_API_KEY is not configured.")
        return format_error("TAVILY_API_KEY is not configured", PLUGIN_ID, fatal=True)

    normalized_keyword = _normalize_inline_text(keyword)
    query_terms = _extract_search_terms(normalized_keyword) or [normalized_keyword]
    max_retries = 3
    payload = {
        "query": keyword,
        "search_depth": "advanced",
        "max_results": limit,
        "include_raw_content": "markdown",
    }
    if from_date:
        payload["start_date"] = from_date
    if to_date:
        payload["end_date"] = to_date

    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                async def request_once(api_key: str) -> httpx.Response:
                    request_payload = {**payload, "api_key": api_key}
                    return await client.post("https://api.tavily.com/search", json=request_payload)

                response = await request_with_key_failover("Tavily", api_keys, request_once)
                if response is None:
                    return format_error("TAVILY_API_KEY is not configured", PLUGIN_ID, fatal=True)
                data = response.json()
                results = []
                for result in data.get("results", []):
                    title = _normalize_inline_text(result.get("title", ""))
                    content = _normalize_inline_text(
                        result.get("raw_content", "") or result.get("content", "")
                    )
                    url = _normalize_inline_text(result.get("url", ""))
                    if not _is_relevant_match(normalized_keyword, query_terms, title, content, url):
                        continue
                    results.append({
                        "title": title,
                        "content": content,
                        "url": url,
                        "score": result.get("score"),
                        "source": "tavily",
                    })
                return results
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt < max_retries - 1:
                logger.warning("Tavily search attempt %s failed: %s. Retrying...", attempt + 1, exc)
                await asyncio.sleep(attempt + 1)
                continue
            logger.error("Tavily search failed after %s attempts: %s", max_retries, exc)
            return format_error(f"Tavily request failed: {exc}", PLUGIN_ID, fatal=True)
        except ProviderRequestError as exc:
            logger.warning("Tavily provider request failed: %s", exc)
            return format_error(str(exc), PLUGIN_ID, fatal=True)
        except Exception as exc:
            logger.exception("Unexpected error in Tavily search: %s", exc)
            return format_error(f"Unexpected error in Tavily search: {exc}", PLUGIN_ID, fatal=True)
    return []
