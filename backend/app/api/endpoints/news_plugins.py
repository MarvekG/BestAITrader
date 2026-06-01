from typing import Any, Dict, List

from fastapi import APIRouter, File, UploadFile

from app.ai.agentic.tooling.news_plugins.manager import (
    NewsPluginCreateRequest,
    create_news_plugin,
    delete_news_plugin,
    list_news_plugins,
)
from app.core.i18n import i18n_service
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _t(key: str, **kwargs: Any) -> str:
    return i18n_service.t(f"news_plugins.{key}", **kwargs)


@router.get("", response_model=Dict[str, Any])
async def list_registered_news_plugins() -> Dict[str, Any]:
    """
    List registered news plugins.

    Returns:
        Registered built-in and external news plugins.
    """
    return list_news_plugins()


@router.post("", response_model=Dict[str, Any])
async def upload_external_news_plugin(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    """
    Upload one or more external news plugin files.

    Args:
        files: Python plugin files. Each filename is used as the plugin name.

    Returns:
        Result payload with plugin metadata when registration succeeds.
    """
    if not files:
        return {
            "status": "error",
            "message": _t("upload_py_only"),
        }

    if len(files) == 1:
        return await _upload_single_news_plugin(files[0])

    results: List[Dict[str, Any]] = []
    success_count = 0
    failed_count = 0
    for file in files:
        result = await _upload_single_news_plugin(file)
        result["filename"] = file.filename
        results.append(result)
        if result.get("status") == "success":
            success_count += 1
        else:
            failed_count += 1

    if failed_count == 0:
        status = "success"
    elif success_count == 0:
        status = "error"
    else:
        status = "partial_success"

    return {
        "status": status,
        "message": f"Uploaded {success_count} plugins, {failed_count} failed.",
        "success_count": success_count,
        "failed_count": failed_count,
        "items": results,
    }


async def _upload_single_news_plugin(file: UploadFile) -> Dict[str, Any]:
    """
    Upload an external news plugin file.

    Args:
        file: Python plugin file. The filename is used as the plugin name.

    Returns:
        Result payload with plugin metadata when registration succeeds.
    """
    try:
        if not file.filename or not file.filename.endswith(".py"):
            return {
                "status": "error",
                "message": _t("upload_py_only"),
            }
        content = (await file.read()).decode("utf-8")
        request = NewsPluginCreateRequest(module_name=file.filename, content=content)
        return await create_news_plugin(request)
    except UnicodeDecodeError as exc:
        logger.exception("Failed to decode news plugin upload: %s", exc)
        return {
            "status": "error",
            "message": _t("utf8_required"),
        }
    except Exception as exc:
        logger.exception("Failed to create news plugin: %s", exc)
        return {
            "status": "error",
            "message": _t("create_failed", error=str(exc)),
        }


@router.delete("/{plugin_key}", response_model=Dict[str, Any])
async def delete_external_news_plugin(plugin_key: str) -> Dict[str, Any]:
    """
    Delete an external news plugin.

    Args:
        plugin_key: Registered plugin id or short module name.

    Returns:
        Result payload describing whether the deletion succeeded.
    """
    try:
        return delete_news_plugin(plugin_key)
    except Exception as exc:
        logger.exception("Failed to delete news plugin: %s", exc)
        return {
            "status": "error",
            "message": _t("delete_failed", error=str(exc)),
        }
