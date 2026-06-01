from __future__ import annotations

import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Tuple

from app.ai.agentic.dependency_installer import (
    DependencyInstallError,
    format_dependency_failure_message,
    install_python_requirements,
)
from app.ai.agentic.skills_loader.loader import (
    REQUIRED_MANIFEST_FIELDS,
    SKILL_FILE_NAME,
    SKILL_MANIFEST_FILE_NAME,
    SKILLS_ROOT,
    discover_skills,
)
from app.core.i18n import i18n_service
from app.core.logger import get_logger

SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
logger = get_logger(__name__)


def _t(key: str, **kwargs: Any) -> str:
    return i18n_service.t(f"skills.{key}", **kwargs)


def list_managed_skills() -> Dict[str, Any]:
    """
    List installed skills.

    Returns:
        JSON-serializable skill list used by the settings page.
    """
    items = []
    for skill in discover_skills():
        item = skill.to_catalog_item()
        item["can_delete"] = True
        items.append(item)
    return {
        "status": "success",
        "count": len(items),
        "items": items,
    }


def _normalize_upload_paths(files: Iterable[Tuple[str, bytes]]) -> Tuple[str, List[Tuple[PurePosixPath, bytes]]]:
    normalized_files: List[Tuple[PurePosixPath, bytes]] = []
    for raw_filename, content in files:
        normalized_name = str(raw_filename or "").replace("\\", "/").strip("/")
        path = PurePosixPath(normalized_name)
        if not normalized_name or path.is_absolute() or ".." in path.parts:
            raise ValueError(_t("invalid_upload_path", path=raw_filename))
        if len(path.parts) < 2:
            raise ValueError(_t("folder_required"))
        normalized_files.append((path, content))

    if not normalized_files:
        raise ValueError(_t("folder_required"))

    root_names = {path.parts[0] for path, _ in normalized_files}
    if len(root_names) != 1:
        raise ValueError(_t("single_folder_required"))

    root_name = next(iter(root_names))
    if not SKILL_ID_PATTERN.fullmatch(root_name):
        raise ValueError(_t("invalid_skill_id", skill_id=root_name))

    return root_name, [(PurePosixPath(*path.parts[1:]), content) for path, content in normalized_files]


def _validate_manifest(files: List[Tuple[PurePosixPath, bytes]]) -> Dict[str, Any]:
    manifest = next((content for path, content in files if path.as_posix() == SKILL_MANIFEST_FILE_NAME), None)
    if manifest is None:
        raise ValueError(_t("skill_json_required"))

    try:
        payload = json.loads(manifest.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(_t("utf8_required", file=SKILL_MANIFEST_FILE_NAME)) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(_t("invalid_skill_json", error=str(exc))) from exc

    if not isinstance(payload, dict):
        raise ValueError(_t("skill_json_object_required"))

    missing_fields = [
        field_name
        for field_name in REQUIRED_MANIFEST_FIELDS
        if not str(payload.get(field_name) or "").strip()
    ]
    if missing_fields:
        raise ValueError(_t("skill_json_missing_fields", fields=", ".join(missing_fields)))

    if not any(path.as_posix() == SKILL_FILE_NAME for path, _ in files):
        raise ValueError(_t("skill_md_required"))

    return payload


def _read_skill_requirements(files: List[Tuple[PurePosixPath, bytes]]) -> str:
    requirements = next((content for path, content in files if path.as_posix() == "requirements.txt"), None)
    if requirements is None:
        return ""
    try:
        return requirements.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(_t("utf8_required", file="requirements.txt")) from exc


async def install_skill_dependencies(
    skill_id: str,
    files: List[Tuple[PurePosixPath, bytes]],
) -> Dict[str, Any]:
    """
    Install Python dependencies declared by a skill upload.

    Args:
        skill_id: Skill directory name.
        files: Skill files normalized relative to the skill root.

    Returns:
        Dependency installation result payload.
    """
    requirements_text = _read_skill_requirements(files)
    try:
        result = await install_python_requirements(
            requirements_text,
            component=f"skill:{skill_id}",
        )
    except DependencyInstallError as exc:
        logger.error(
            "Skill dependency installation failed: skill_id=%s requirements=%s exit_code=%s",
            skill_id,
            exc.result.requirements,
            exc.result.exit_code,
        )
        return {
            **exc.result.to_dict(),
            "status": "error",
            "message": _t("dependency_install_failed", error=format_dependency_failure_message(exc.result)),
        }
    except ValueError as exc:
        logger.error("Skill dependency declaration invalid: skill_id=%s error=%s", skill_id, exc)
        return {
            "status": "error",
            "requirements": [],
            "command": [],
            "message": _t("dependency_install_failed", error=str(exc)),
        }
    return result.to_dict()


async def save_uploaded_skill(files: Iterable[Tuple[str, bytes]]) -> Dict[str, Any]:
    """
    Save an uploaded skill directory.

    Args:
        files: Iterable of relative upload path and file content pairs.

    Returns:
        Result payload with installed skill metadata.
    """
    skill_id, normalized_files = _normalize_upload_paths(files)
    _validate_manifest(normalized_files)
    dependency_result = await install_skill_dependencies(skill_id, normalized_files)
    if dependency_result["status"] == "error":
        return {
            "status": "error",
            "message": dependency_result["message"],
            "skill_id": skill_id,
            "dependencies": dependency_result,
        }

    target_dir = (SKILLS_ROOT / skill_id).resolve()
    skills_root = SKILLS_ROOT.resolve()
    try:
        target_dir.relative_to(skills_root)
    except ValueError as exc:
        raise ValueError(_t("invalid_skill_id", skill_id=skill_id)) from exc

    staging_dir = (SKILLS_ROOT / f".{skill_id}.uploading").resolve()
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        for relative_path, content in normalized_files:
            destination = (staging_dir / relative_path.as_posix()).resolve()
            try:
                destination.relative_to(staging_dir)
            except ValueError as exc:
                raise ValueError(_t("invalid_upload_path", path=relative_path.as_posix())) from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(staging_dir), str(target_dir))
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)

    skill = next((item for item in discover_skills() if item.skill_id == skill_id), None)
    return {
        "status": "success",
        "message": _t("saved", skill_id=skill_id),
        "skill_id": skill_id,
        "skill": skill.to_catalog_item() if skill else None,
        "dependencies": dependency_result,
    }


def delete_managed_skill(skill_id: str) -> Dict[str, Any]:
    """
    Delete an installed skill directory.

    Args:
        skill_id: Directory name under the skills root.

    Returns:
        Result payload describing the deletion.
    """
    normalized_id = str(skill_id or "").strip()
    if not SKILL_ID_PATTERN.fullmatch(normalized_id):
        return {
            "status": "error",
            "message": _t("invalid_skill_id", skill_id=normalized_id),
        }

    target_dir = (SKILLS_ROOT / normalized_id).resolve()
    skills_root = SKILLS_ROOT.resolve()
    try:
        target_dir.relative_to(skills_root)
    except ValueError:
        return {
            "status": "error",
            "message": _t("invalid_skill_id", skill_id=normalized_id),
        }

    if not target_dir.is_dir():
        return {
            "status": "error",
            "message": _t("not_found", skill_id=normalized_id),
        }

    shutil.rmtree(target_dir)
    return {
        "status": "success",
        "message": _t("deleted", skill_id=normalized_id),
        "skill_id": normalized_id,
    }
