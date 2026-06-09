from __future__ import annotations

from typing import Any

from markdownify import markdownify as html_to_markdown


DEFAULT_VIEWPORT = {"width": 1365, "height": 900}
DEFAULT_LOCALE = "zh-CN"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_WAIT_UNTIL = "domcontentloaded"


def convert_html_to_markdown(html: str, title: str, source_url: str) -> str:
    """
    将 HTML 转换为带来源信息的 Markdown。

    Args:
        html: 待转换的 HTML。
        title: 页面标题。
        source_url: 最终来源 URL。

    Returns:
        Markdown 文本。
    """
    markdown_body = html_to_markdown(html, heading_style="ATX").strip()
    heading = title.strip() or source_url
    return f"# {heading}\n\nSource URL: {source_url}\n\n{markdown_body}".strip()


async def select_rendered_html(page: Any, selectors: list[str]) -> tuple[str, int | None]:
    """
    从浏览器页面中提取指定 selector 的 HTML。

    Args:
        page: Playwright 兼容页面对象。
        selectors: CSS selector 列表。

    Returns:
        HTML 内容和匹配元素数量。未指定 selector 时数量为 None。
    """
    if not selectors:
        html = await page.content()
        return html, None

    result = await page.evaluate(
        """
        (selectors) => {
          const selected = [];
          const seen = new Set();
          for (const selector of selectors) {
            for (const element of document.querySelectorAll(selector)) {
              if (seen.has(element)) {
                continue;
              }
              seen.add(element);
              selected.push(element.outerHTML);
            }
          }
          return {
            html: selected.join("\\n"),
            selected_element_count: selected.length,
          };
        }
        """,
        selectors,
    )
    if not isinstance(result, dict):
        return "", 0
    return str(result.get("html") or ""), int(result.get("selected_element_count") or 0)
