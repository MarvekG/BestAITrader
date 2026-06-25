import ast
import importlib
import re
from datetime import date, timedelta
from inspect import isawaitable
from pathlib import Path
from typing import Any, Dict

from pydantic import BaseModel, Field

from app.ai.agentic.dependency_installer import (
    DependencyInstallError,
    format_dependency_failure_message,
    install_python_requirements,
)
from app.ai.agentic.tooling.news_plugins import get_news_plugins
from app.ai.agentic.tooling.news_plugins.paths import (
    NEWS_PLUGIN_EXTERNAL_DIR,
)
from app.ai.agentic.tooling.news_plugins.registry import NewsPlugin, RESERVED_NEWS_PLUGIN_MODULE_NAMES
from app.core.i18n import i18n_service
from app.core.logger import get_logger

NEWS_PLUGIN_MODULE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
logger = get_logger(__name__)


def _t(key: str, **kwargs: Any) -> str:
    return i18n_service.t(f"news_plugins.{key}", **kwargs)


class NewsPluginCreateRequest(BaseModel):
    """Request body for creating or replacing an external news plugin."""

    module_name: str = Field(..., min_length=2, max_length=64)
    content: str = Field(..., min_length=1, max_length=200_000)


def normalize_news_plugin_module_name(value: str) -> str:
    """
    Normalize and validate an external news plugin module name.

    Args:
        value: Raw module name from the request path or body.

    Returns:
        A normalized Python module name without the `.py` suffix.

    Raises:
        ValueError: If the module name is invalid or reserved.
    """
    module_name = value.removesuffix(".py").strip().lower()
    if not NEWS_PLUGIN_MODULE_NAME_PATTERN.fullmatch(module_name):
        raise ValueError(_t("invalid_module_name"))
    if module_name == "__init__" or module_name in RESERVED_NEWS_PLUGIN_MODULE_NAMES:
        raise ValueError(_t("reserved_module_name", module_name=module_name))
    return module_name


def get_external_news_plugin_path(module_name: str) -> Path:
    """
    Build the external plugin file path for a validated module name.

    Args:
        module_name: Raw or normalized module name.

    Returns:
        The target Python file path under the external plugin directory.
    """
    normalized_module_name = normalize_news_plugin_module_name(module_name)
    return NEWS_PLUGIN_EXTERNAL_DIR / f"{normalized_module_name}.py"


def _display_external_news_plugin_path(plugin_path: Path) -> str:
    """
    生成外部新闻插件路径的响应展示值。

    Args:
        plugin_path: 外部新闻插件文件路径。

    Returns:
        可读的路径字符串；位于运行时插件目录下时优先返回相对路径。
    """
    try:
        return str(plugin_path.relative_to(NEWS_PLUGIN_EXTERNAL_DIR.parent))
    except ValueError:
        return str(plugin_path)


def serialize_news_plugin(plugin: NewsPlugin) -> Dict[str, Any]:
    """
    Convert a news plugin registration to an API response payload.

    Args:
        plugin: Registered news plugin metadata.

    Returns:
        JSON-serializable plugin metadata.
    """
    return {
        "name": plugin.name,
        "plugin_id": plugin.plugin_id,
        "tool_name": plugin.tool_name,
        "news_types": list(plugin.news_types),
        "keyword_examples": list(plugin.keyword_examples),
        "module_name": plugin.module_name,
        "qualified_module_name": plugin.module_name,
        "source_type": plugin.source_type,
        "can_delete": plugin.source_type == "external" and bool(plugin.file_path and plugin.file_path.exists()),
    }


def list_news_plugins() -> Dict[str, Any]:
    """
    List all registered news plugins.

    Returns:
        A response dictionary containing registered plugin metadata.
    """
    items = [serialize_news_plugin(plugin) for _, plugin in sorted(get_news_plugins().items())]
    return {
        "status": "success",
        "count": len(items),
        "items": items,
    }


async def create_news_plugin(request: NewsPluginCreateRequest) -> Dict[str, Any]:
    """
    Create or replace an external news plugin file, probe search, and refresh the registry.

    Args:
        request: Validated plugin module name and Python source content.

    Returns:
        A response dictionary describing the created plugin.
    """
    module_name = normalize_news_plugin_module_name(request.module_name)
    plugin_path = get_external_news_plugin_path(module_name)
    content = validate_news_plugin_content(request.content)
    dependency_result = await install_news_plugin_dependencies(
        content,
        module_name=module_name,
    )
    if dependency_result["status"] == "error":
        return {
            "status": "error",
            "message": dependency_result["message"],
            "module_name": module_name,
            "path": _display_external_news_plugin_path(plugin_path),
            "dependencies": dependency_result,
        }
    previous_content = plugin_path.read_text(encoding="utf-8") if plugin_path.exists() else None

    NEWS_PLUGIN_EXTERNAL_DIR.mkdir(parents=True, exist_ok=True)
    plugin_path.write_text(content, encoding="utf-8")
    refresh_news_plugin_registry()

    matched_plugin = find_registered_plugin_by_module_name(module_name)
    if matched_plugin is None:
        _restore_news_plugin_file(plugin_path, previous_content)
        refresh_news_plugin_registry()
        return {
            "status": "error",
            "message": _t("not_registered", module_name=module_name),
            "module_name": module_name,
            "path": _display_external_news_plugin_path(plugin_path),
        }

    probe_result = await probe_news_plugin_search(matched_plugin)
    if probe_result["status"] != "success":
        _restore_news_plugin_file(plugin_path, previous_content)
        refresh_news_plugin_registry()
        return {
            "status": "error",
            "message": probe_result["message"],
            "module_name": module_name,
            "path": _display_external_news_plugin_path(plugin_path),
            "probe": probe_result,
        }

    return {
        "status": "success",
        "message": _t("saved", plugin_id=matched_plugin.plugin_id),
        "module_name": module_name,
        "source": matched_plugin.plugin_id,
        "path": _display_external_news_plugin_path(plugin_path),
        "plugin": serialize_news_plugin(matched_plugin),
        "probe": probe_result,
        "dependencies": dependency_result,
    }


def delete_news_plugin(plugin_key: str) -> Dict[str, Any]:
    """
    Delete an external news plugin by plugin id or module name.

    Args:
        plugin_key: Registered plugin id or external module name.

    Returns:
        A response dictionary describing the deletion result.
    """
    plugin = get_news_plugins().get(plugin_key)
    if plugin is not None:
        module_name = plugin.module_name
        plugin_path = plugin.file_path if plugin.source_type == "external" else None
    else:
        module_name = normalize_news_plugin_module_name(plugin_key)
        plugin_path = get_external_news_plugin_path(module_name)

    if plugin_path is None or not plugin_path.exists():
        return {
            "status": "error",
            "message": _t("not_found", plugin_key=plugin_key),
        }

    plugin_path.unlink()
    refresh_news_plugin_registry()
    return {
        "status": "success",
        "message": _t("deleted", module_name=module_name),
        "module_name": module_name,
    }


def validate_news_plugin_content(content: str) -> str:
    """
    Validate Python syntax for a plugin source file.

    Args:
        content: Raw Python source submitted by the user.

    Returns:
        Normalized source content ending with a newline.

    Raises:
        ValueError: If content is empty or syntactically invalid.
    """
    normalized_content = content.replace("\r\n", "\n").strip()
    if not normalized_content:
        raise ValueError(_t("content_empty"))
    try:
        ast.parse(normalized_content, filename="<news_plugin>")
    except SyntaxError as exc:
        raise ValueError(_t("syntax_invalid", error=str(exc))) from exc
    return f"{normalized_content}\n"


async def install_news_plugin_dependencies(
    content: str,
    *,
    module_name: str,
) -> Dict[str, Any]:
    """
    Install Python dependencies declared by an external news plugin.

    Args:
        content: Validated Python plugin source code.
        module_name: Normalized short plugin module name.

    Returns:
        Dependency installation result payload.
    """
    try:
        result = await install_python_requirements(
            extract_news_plugin_requirements(content),
            component=f"news_plugin:{module_name}",
        )
    except DependencyInstallError as exc:
        logger.error(
            "News plugin dependency installation failed: module=%s requirements=%s exit_code=%s",
            module_name,
            exc.result.requirements,
            exc.result.exit_code,
        )
        return {
            **exc.result.to_dict(),
            "status": "error",
            "message": _t("dependency_install_failed", error=format_dependency_failure_message(exc.result)),
        }
    except ValueError as exc:
        logger.error("News plugin dependency declaration invalid: module=%s error=%s", module_name, exc)
        return {
            "status": "error",
            "requirements": [],
            "command": [],
            "message": _t("dependency_install_failed", error=str(exc)),
        }
    return result.to_dict()


def extract_news_plugin_requirements(content: str) -> str:
    """
    Extract `PYTHON_REQUIREMENTS` from plugin source without importing it.

    Args:
        content: Validated Python plugin source code.

    Returns:
        requirements.txt-style content.

    Raises:
        ValueError: If `PYTHON_REQUIREMENTS` is present but is not a string/list/tuple of strings.
    """
    module_ast = ast.parse(content, filename="<news_plugin>")
    requirement_values: list[str] = []

    for node in module_ast.body:
        value_node = _get_python_requirements_value_node(node)
        if value_node is None:
            continue
        value = ast.literal_eval(value_node)
        if isinstance(value, str):
            requirement_values.extend(value.splitlines())
        elif isinstance(value, (list, tuple)):
            if not all(isinstance(item, str) for item in value):
                raise ValueError(_t("dependency_requirements_invalid"))
            requirement_values.extend(value)
        else:
            raise ValueError(_t("dependency_requirements_invalid"))

    return "\n".join(requirement_values)


def _get_python_requirements_value_node(node: ast.stmt) -> ast.expr | None:
    if isinstance(node, ast.Assign):
        if any(isinstance(target, ast.Name) and target.id == "PYTHON_REQUIREMENTS" for target in node.targets):
            return node.value
        return None
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        if node.target.id == "PYTHON_REQUIREMENTS" and node.value is not None:
            return node.value
    return None


def refresh_news_plugin_registry() -> None:
    """
    Clear importlib caches and cached registry state.
    """
    importlib.invalidate_caches()
    get_news_plugins.cache_clear()


def find_registered_plugin_by_module_name(module_name: str) -> NewsPlugin | None:
    """
    Find a registered plugin by its short module name.

    Args:
        module_name: Short external module name.

    Returns:
        The registered plugin, or None when the file did not register.
    """
    return next(
        (plugin for plugin in get_news_plugins().values() if plugin.module_name == module_name),
        None,
    )


async def probe_news_plugin_search(plugin: NewsPlugin) -> Dict[str, Any]:
    """
    Probe a news plugin by calling its search interface before accepting import.

    Args:
        plugin: Newly registered news plugin.

    Returns:
        A response dictionary describing probe success or failure.
    """
    to_date = date.today()
    from_date = to_date - timedelta(days=30)
    try:
        result = plugin.search(
            keyword="AI",
            limit=3,
            from_date=from_date.isoformat(),
            to_date=to_date.isoformat(),
        )
        if isawaitable(result):
            result = await result
    except Exception as exc:
        return {
            "status": "error",
            "message": _t("probe_failed", error=str(exc)),
            "keyword": "AI",
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        }

    if not isinstance(result, list):
        return {
            "status": "error",
            "message": _t("probe_wrong_type", result_type=type(result).__name__),
            "keyword": "AI",
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        }
    if result and isinstance(result[0], dict) and (result[0].get("error") or result[0].get("fatal")):
        return {
            "status": "error",
            "message": _t("probe_error", error=result[0].get("error", "fatal")),
            "keyword": "AI",
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
        }
    return {
        "status": "success",
        "message": _t("probe_success"),
        "keyword": "AI",
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "count": len(result),
    }


def _restore_news_plugin_file(plugin_path: Path, previous_content: str | None) -> None:
    if previous_content is None:
        plugin_path.unlink(missing_ok=True)
        return
    plugin_path.write_text(previous_content, encoding="utf-8")
