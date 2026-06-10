import importlib
import importlib.util
import sys
from dataclasses import dataclass
from functools import lru_cache
from inspect import isawaitable
from pathlib import Path
from typing import Any, Callable, Dict, List

from app.core.logger import get_logger

from .base import ensure_source, format_error
from .paths import NEWS_PLUGIN_DIR, NEWS_PLUGIN_EXTERNAL_DIR

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
    source_type: str
    file_path: Path | None = None


def _validate_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("Expected list[str]")
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    if not cleaned:
        raise ValueError("Expected non-empty list[str]")
    return tuple(cleaned)


def _iter_plugin_modules(plugin_dir: Path, package_name: str) -> list[tuple[Path, str, str, str]]:
    reserved_modules = {"base", "manager", "paths", "registry"}
    module_specs: list[tuple[Path, str, str, str]] = []

    for path in sorted(plugin_dir.glob("*.py")):
        if path.name == "__init__.py" or path.stem in reserved_modules:
            continue
        module_specs.append((path, path.stem, "builtin", f"{package_name}.{path.stem}"))

    if NEWS_PLUGIN_EXTERNAL_DIR.is_dir():
        for path in sorted(NEWS_PLUGIN_EXTERNAL_DIR.glob("*.py")):
            if path.name == "__init__.py" or path.stem in reserved_modules:
                continue
            module_specs.append((path, path.stem, "external", f"runtime_news_plugin_{path.stem}"))

    return module_specs


def _load_plugin_module(path: Path, import_name: str, source_type: str) -> Any:
    """
    加载新闻插件模块。

    Args:
        path: 插件源文件路径。
        import_name: Python 导入或文件加载使用的模块名。
        source_type: 插件来源，`builtin` 或 `external`。

    Returns:
        已导入的 Python 模块。

    Raises:
        ImportError: 外部插件无法按文件路径加载时抛出。
    """
    if source_type == "builtin":
        return importlib.import_module(import_name)

    spec = importlib.util.spec_from_file_location(import_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load news plugin from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[import_name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def get_news_plugins() -> Dict[str, NewsPlugin]:
    plugins: Dict[str, NewsPlugin] = {}
    package_name = __package__ or "app.ai.agentic.tooling.news_plugins"

    for path, module_name, source_type, import_name in _iter_plugin_modules(NEWS_PLUGIN_DIR, package_name):
        try:
            module = _load_plugin_module(path, import_name, source_type)
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
            source_type=source_type,
            file_path=path if source_type == "external" else None,
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
