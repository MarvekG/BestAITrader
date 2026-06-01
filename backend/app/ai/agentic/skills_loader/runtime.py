from __future__ import annotations

from typing import Any, List

from app.ai.agentic.skills_loader.loader import discover_skills
from app.ai.agentic.skills_loader.skill_tools import get_skills_loader_tools


def build_skills_catalog_prompt() -> str:
    """
    Build a compact prompt section listing available external skills.

    Returns:
        Prompt section to append to a system prompt. Empty string when no skills exist.
    """
    skills = discover_skills()
    if not skills:
        return ""

    lines = [
        "# Available External Skills",
        "",
        "You may use these external skills when relevant. Do not assume their full instructions; "
        "when the current Context, built-in tools, or prior messages are insufficient for a reliable answer, "
        "use a relevant skill to fill the evidence gap before saying the information is insufficient. "
        "Call `load_skill` first, then follow that skill's SKILL.md. Before calling `run_skill_script`, "
        "read the relevant reference files listed by the loaded skill with `read_skill_file` and use the "
        "script only after the required interface, parameters, and output fields are clear.",
        "",
    ]
    for skill in skills:
        lines.append(f"- {skill.skill_id} ({skill.name}): {skill.description}")
        if skill.references:
            lines.append(f"  References: {', '.join(skill.references[:8])}")
        if skill.scripts:
            lines.append(f"  Scripts: {', '.join(skill.scripts[:8])}")
    return "\n".join(lines).strip()


def prepare_skills_loader() -> tuple[str, List[Any]]:
    """
    Prepare the external skills prompt and tools for an agent run.

    Returns:
        Tuple of prompt section and LangChain tools.
    """
    return build_skills_catalog_prompt(), get_skills_loader_tools()
