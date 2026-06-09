from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse


def normalize_fetch_url(raw_url: str) -> str:
    """
    规范化网页抓取 URL。

    Args:
        raw_url: 用户传入的原始 URL。

    Returns:
        补全协议并规范化后的 URL。

    Raises:
        ValueError: URL 为空、协议不支持或缺少 hostname。
    """
    stripped_url = raw_url.strip()
    if not stripped_url:
        raise ValueError("url is required")

    normalized_input = stripped_url if "://" in stripped_url else f"https://{stripped_url}"
    parsed = urlparse(normalized_input)

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs are supported")
    if not parsed.netloc or not parsed.hostname:
        raise ValueError("url must include a hostname")

    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def compile_markdown_patterns(patterns: list[str]) -> list[re.Pattern[str]]:
    """
    编译 Markdown 清理正则。

    Args:
        patterns: 正则表达式列表。

    Returns:
        已编译的正则对象列表。

    Raises:
        re.error: 正则表达式非法。
    """
    return [re.compile(pattern, flags=re.MULTILINE | re.DOTALL) for pattern in patterns]


def clean_markdown(markdown: str, patterns: list[re.Pattern[str]]) -> str:
    """
    按顺序清理 Markdown 文本。

    Args:
        markdown: 原始 Markdown 文本。
        patterns: 已编译的清理正则列表。

    Returns:
        清理后的 Markdown 文本。
    """
    cleaned = markdown
    for pattern in patterns:
        cleaned = pattern.sub("", cleaned)
    return cleaned.strip()
