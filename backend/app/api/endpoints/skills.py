from typing import Any, Dict, List

from fastapi import APIRouter, File, UploadFile

from app.ai.agentic.skills_loader.manager import (
    delete_managed_skill,
    list_managed_skills,
    save_uploaded_skill,
)
from app.ai.agentic.skills_loader.runtime import build_skills_catalog_prompt
from app.core.i18n import i18n_service
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def _t(key: str, **kwargs: Any) -> str:
    return i18n_service.t(f"skills.{key}", **kwargs)


@router.get("", response_model=Dict[str, Any])
async def list_installed_skills() -> Dict[str, Any]:
    """
    List installed skills.

    Returns:
        Installed skills discovered by the skill loader.
    """
    return list_managed_skills()


@router.post("", response_model=Dict[str, Any])
async def upload_skill_folder(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
    """
    Upload a skill folder.

    Args:
        files: Files from one uploaded skill folder.

    Returns:
        Result payload with installed skill metadata.
    """
    try:
        uploaded_files = []
        for file in files:
            uploaded_files.append((file.filename, await file.read()))
        return await save_uploaded_skill(uploaded_files)
    except ValueError as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    except Exception as exc:
        logger.exception("Failed to upload skill folder: %s", exc)
        return {
            "status": "error",
            "message": _t("save_failed", error=str(exc)),
        }


@router.get("/prompt", response_model=Dict[str, Any])
async def get_skills_catalog_prompt() -> Dict[str, Any]:
    """
    Get the Skills catalog prompt injected into LLM runs.

    Returns:
        Prompt text generated from the current Skills catalog.
    """
    return {
        "status": "success",
        "prompt": build_skills_catalog_prompt(),
    }


@router.delete("/{skill_id}", response_model=Dict[str, Any])
async def delete_installed_skill(skill_id: str) -> Dict[str, Any]:
    """
    Delete an installed skill.

    Args:
        skill_id: Skill directory name.

    Returns:
        Result payload describing whether the deletion succeeded.
    """
    try:
        return delete_managed_skill(skill_id)
    except Exception as exc:
        logger.exception("Failed to delete skill: %s", exc)
        return {
            "status": "error",
            "message": _t("delete_failed", error=str(exc)),
        }
