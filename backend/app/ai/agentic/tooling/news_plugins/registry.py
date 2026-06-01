import importlib
from dataclasses import dataclass
from functools import lru_cache
from inspect import isawaitable
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.core.logger import get_logger

from .base import ensure_source, format_error

logger = get_logger(__name__)


@dataclass(frozen=True)
class NewsPlugin:
    name: str
    plugin_id: str
    tool_name: str
    news_types: tuple[str, ...]
    keyword_examples: tuple[str, ...]
    search: Callable[..., Any]
    module_name: str


def _validate_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("Expected list[str]")
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    if not cleaned:
        raise ValueError("Expected non-empty list[str]")
    return tuple(cleaned)


def _iter_plugin_modules(plugin_dir: Path, package_name: str) -> list[tuple[Path, str]]:
    reserved_modules = {"base", "manager", "registry"}
    module_specs: list[tuple[Path, str]] = []

    for path in sorted(plugin_dir.glob("*.py")):
        if path.name == "__init__.py" or path.stem in reserved_modules:
            continue
        module_specs.append((path, f"{package_name}.{path.stem}"))

    external_dir = plugin_dir / "external"
    if external_dir.is_dir():
        for path in sorted(external_dir.glob("*.py")):
            if path.name == "__init__.py" or path.stem in reserved_modules:
                continue
            module_specs.append((path, f"{package_name}.external.{path.stem}"))

    return module_specs


@lru_cache(maxsize=1)
def get_news_plugins() -> Dict[str, NewsPlugin]:
    plugin_dir = Path(__file__).resolve().parent
    package_name = __package__
    plugins: Dict[str, NewsPlugin] = {}

    for path, module_name in _iter_plugin_modules(plugin_dir, package_name):
        try:
            module = importlib.import_module(module_name)
            name = str(getattr(module, "NAME")).strip()
            plugin_id = str(getattr(module, "PLUGIN_ID")).strip()
            tool_name = str(getattr(module, "TOOL_NAME")).strip()
            news_types = _validate_list(getattr(module, "NEWS_TYPES"))
            keyword_examples = _validate_list(getattr(module, "KEYWORD_EXAMPLES"))
            search = getattr(module, "search")
        except Exception as exc:
            logger.exception("Failed to load news plugin from %s: %s", path.name, exc)
            continue

        if not name or not plugin_id or not callable(search):
            logger.warning("Skipping news plugin %s due to invalid metadata", module_name)
            continue

        if plugin_id in plugins:
            logger.warning(
                "Skipping duplicated news plugin id '%s' from %s",
                plugin_id,
                module_name,
            )
            continue

        plugins[plugin_id] = NewsPlugin(
            name=name,
            plugin_id=plugin_id,
            tool_name=tool_name,
            news_types=news_types,
            keyword_examples=keyword_examples,
            search=search,
            module_name=module_name,
        )

    return plugins


def get_available_news_sources() -> List[str]:
    return sorted(get_news_plugins().keys())


def build_search_news_docstring() -> str:
    sources = get_available_news_sources()
    sections = [
        "统一新闻搜索入口。调用前必须先选择一个 `source`，每次只能调用一个新闻插件。",
        "参数 `limit` 只能取 1-20；默认 10，除非用户明确要求更多，否则不要超过 20。",
        "工具会自动去除重复 URL 和重复标题，并最多返回 20 条去重后的结果。",
        "",
        "Sources:",
    ]

    for source in sources:
        plugin = get_news_plugins()[source]
        suitable_for = "、".join(plugin.news_types[:3])
        sections.append(f"- `{source}`: {plugin.name}；{suitable_for}")

    return "\n".join(sections)


async def invoke_news_plugin(source: str, keyword: str, limit: int = 10, **kwargs) -> List[Dict[str, Any]]:
    plugin = get_news_plugins().get(source)
    if plugin is None:
        return [{
            "error": f"Unknown news source: {source}",
            "available_sources": get_available_news_sources(),
            "source": source,
        }]

    try:
        result = plugin.search(keyword=keyword, limit=limit, **kwargs)
        if isawaitable(result):
            result = await result
        return ensure_source(result, plugin.plugin_id)
    except Exception as exc:
        logger.exception("News plugin '%s' failed: %s", source, exc)
        return format_error(f"News plugin '{source}' failed: {exc}", source)
