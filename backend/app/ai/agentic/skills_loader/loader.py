from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logger import get_logger

logger = get_logger(__name__)

SKILLS_ROOT = Path(__file__).resolve().parent / "skills"
SKILL_FILE_NAME = "SKILL.md"
SKILL_MANIFEST_FILE_NAME = "skill.json"
REQUIRED_MANIFEST_FIELDS = ("name", "description")


@dataclass(frozen=True)
class LoadedSkill:
    """Metadata for one locally installed external skill."""

    skill_id: str
    name: str
    description: str
    root_path: Path
    skill_path: Path
    metadata: Dict[str, Any] = field(default_factory=dict)
    references: List[str] = field(default_factory=list)
    scripts: List[str] = field(default_factory=list)

    def to_catalog_item(self) -> Dict[str, Any]:
        """
        Convert the skill to a compact catalog item.

        Returns:
            Compact JSON-serializable skill metadata.
        """
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "references": self.references,
            "scripts": self.scripts,
        }


def _load_manifest(root_path: Path) -> Dict[str, Any]:
    manifest_path = root_path / SKILL_MANIFEST_FILE_NAME
    if not manifest_path.is_file():
        raise ValueError("skill.json is required")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"skill.json is invalid JSON: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"skill.json cannot be read: {exc}") from exc

    if not isinstance(payload, dict):
        raise ValueError("skill.json must contain a JSON object")

    missing_fields = [
        field_name
        for field_name in REQUIRED_MANIFEST_FIELDS
        if not str(payload.get(field_name) or "").strip()
    ]
    if missing_fields:
        raise ValueError(f"skill.json missing required fields: {', '.join(missing_fields)}")
    return payload


def _relative_files(root_path: Path, directory_name: str) -> List[str]:
    directory = root_path / directory_name
    if not directory.is_dir():
        return []
    files = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files.append(path.relative_to(root_path).as_posix())
    return files


def _load_skill_from_path(root_path: Path) -> Optional[LoadedSkill]:
    skill_path = root_path / SKILL_FILE_NAME
    if not skill_path.is_file():
        return None

    try:
        metadata = _load_manifest(root_path)
    except ValueError as exc:
        logger.warning("Skipping invalid skill: %s (%s)", root_path, exc)
        return None

    skill_id = root_path.name
    name = str(metadata["name"]).strip()
    description = str(metadata["description"]).strip()

    return LoadedSkill(
        skill_id=skill_id,
        name=name,
        description=description,
        root_path=root_path.resolve(),
        skill_path=skill_path.resolve(),
        metadata=metadata,
        references=_relative_files(root_path, "references"),
        scripts=_relative_files(root_path, "scripts"),
    )


def discover_skills() -> List[LoadedSkill]:
    """
    Discover locally installed external skills.

    Returns:
        List of skills that have a readable SKILL.md.
    """
    if not SKILLS_ROOT.is_dir():
        return []

    skills: List[LoadedSkill] = []
    for root_path in sorted(SKILLS_ROOT.iterdir()):
        if not root_path.is_dir():
            continue
        skill = _load_skill_from_path(root_path)
        if skill is None:
            continue
        skills.append(skill)
    return skills


def get_skill(skill_id: str) -> Optional[LoadedSkill]:
    """
    Get one locally installed skill by id.

    Args:
        skill_id: Directory name under the skills root.

    Returns:
        Loaded skill metadata, or None when not found.
    """
    normalized_id = str(skill_id or "").strip()
    if not normalized_id:
        return None
    return next((skill for skill in discover_skills() if skill.skill_id == normalized_id), None)


def read_skill_markdown(skill_id: str) -> Optional[str]:
    """
    Read a skill's full SKILL.md content.

    Args:
        skill_id: Skill id.

    Returns:
        Markdown content, or None when unavailable.
    """
    skill = get_skill(skill_id)
    if skill is None:
        return None
    try:
        content = skill.skill_path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Failed to read skill markdown: %s", skill.skill_path)
        return None
    return content


def resolve_skill_relative_path(skill_id: str, relative_path: str) -> Path:
    """
    Resolve a skill-local relative path safely.

    Args:
        skill_id: Skill id.
        relative_path: Relative path inside the skill directory.

    Returns:
        Resolved absolute path.

    Raises:
        ValueError: If the skill does not exist or the path escapes the skill directory.
    """
    skill = get_skill(skill_id)
    if skill is None:
        raise ValueError(f"Skill not found: {skill_id}")

    raw_path = Path(str(relative_path or "").strip())
    if not raw_path.as_posix() or raw_path.is_absolute():
        raise ValueError("relative_path must be a non-empty relative path")

    resolved = (skill.root_path / raw_path).resolve()
    try:
        resolved.relative_to(skill.root_path)
    except ValueError as exc:
        raise ValueError("Path escapes skill directory") from exc
    return resolved
