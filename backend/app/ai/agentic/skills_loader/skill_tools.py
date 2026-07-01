from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain.tools import tool

from app.ai.agentic.skills_loader.loader import (
    discover_skills,
    get_skill,
    read_skill_markdown,
    resolve_skill_relative_path,
)
from app.core.data_source_config_cache import get_data_source_config_value
from app.core.data_source_settings import TUSHARE_API_SETTING_KEY, TUSHARE_TOKEN_SETTING_KEY
from app.core.logger import get_logger

logger = get_logger(__name__)

DEFAULT_SCRIPT_TIMEOUT_SECONDS = 120
SKILL_SCRIPT_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "PYTHONIOENCODING",
)
PYTHON_COMMANDS = {"python", "python3", Path(sys.executable).name, sys.executable}
SHELL_COMMANDS = {"bash", "sh", "/bin/bash", "/bin/sh"}
COMMAND_INJECTION_CHARS = frozenset({";", "|", "&", "`", "$", "<", ">", "\n", "\r", "\x00"})


def _format_error(message: str, **extra: Any) -> Dict[str, Any]:
    payload = {"success": False, "error": message}
    payload.update(extra)
    return payload


@tool
def list_skills() -> Dict[str, Any]:
    """
    列出当前本地安装的外部 skills。

    返回每个 skill 的 id、名称、描述、reference 文件和 script 文件清单。只用于发现能力；
    在使用某个 skill 前，应先调用 `load_skill` 读取完整 SKILL.md。
    """
    skills = [skill.to_catalog_item() for skill in discover_skills()]
    return {"skills": skills, "count": len(skills)}


@tool
def load_skill(skill_id: str) -> Dict[str, Any]:
    """
    加载一个外部 skill 的完整 SKILL.md。

    Args:
        skill_id: skill 目录名，例如 `tushare-data`。

    Returns:
        包含 skill 元数据和 SKILL.md 内容的结构化结果。
    """
    skill = get_skill(skill_id)
    if skill is None:
        return _format_error(f"Skill not found: {skill_id}", skill_id=skill_id)

    content = read_skill_markdown(skill_id)
    if content is None:
        return _format_error(f"Failed to read skill: {skill_id}", skill_id=skill_id)

    return {
        "success": True,
        "skill": skill.to_catalog_item(),
        "content": content,
    }


@tool
def read_skill_file(skill_id: str, relative_path: str) -> Dict[str, Any]:
    """
    读取外部 skill 目录内的文件。

    Args:
        skill_id: skill 目录名，例如 `tushare-data`。
        relative_path: skill 内部相对路径，例如 `SKILL.md` 或 `references/数据接口.md`。

    Returns:
        文件内容。禁止读取 skill 目录外文件。
    """
    try:
        resolved_path = resolve_skill_relative_path(skill_id, relative_path)
    except ValueError as exc:
        return _format_error(str(exc), skill_id=skill_id, relative_path=relative_path)

    if not resolved_path.is_file():
        return _format_error("Skill file not found", skill_id=skill_id, relative_path=relative_path)

    try:
        content = resolved_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _format_error("Skill file is not valid UTF-8 text", skill_id=skill_id, relative_path=relative_path)
    except OSError as exc:
        logger.exception("Failed to read skill file: %s", resolved_path)
        return _format_error(str(exc), skill_id=skill_id, relative_path=relative_path)

    return {
        "success": True,
        "skill_id": skill_id,
        "relative_path": relative_path,
        "content": content,
    }


def _get_scripts_dir(skill_id: str) -> Path:
    skill = get_skill(skill_id)
    if skill is None:
        raise ValueError(f"Skill not found: {skill_id}")
    return (skill.root_path / "scripts").resolve()


def _resolve_script_path(skill_id: str, script_path: str) -> Path:
    raw_script_path = str(script_path or "").strip()
    if not raw_script_path:
        raise ValueError("script_path is required")

    resolved_path = resolve_skill_relative_path(skill_id, raw_script_path)
    scripts_dir = _get_scripts_dir(skill_id)
    try:
        resolved_path.relative_to(scripts_dir)
    except ValueError as exc:
        raise ValueError("Scripts must be located under the skill scripts directory") from exc

    if not resolved_path.is_file():
        raise ValueError("Skill script not found")
    return resolved_path


def _looks_like_script_path(value: str) -> bool:
    return value == "scripts" or value.startswith("scripts/")


def _build_explicit_command(skill_id: str, command: List[str]) -> List[str]:
    normalized_command = [str(item) for item in command if str(item)]
    if not normalized_command:
        raise ValueError("command must contain at least one item")

    entrypoint = normalized_command[0]
    if _looks_like_script_path(entrypoint):
        resolved_entrypoint = _resolve_script_path(skill_id, entrypoint)
        return [str(resolved_entrypoint), *normalized_command[1:]]

    if entrypoint in PYTHON_COMMANDS:
        if len(normalized_command) < 2 or not _looks_like_script_path(normalized_command[1]):
            raise ValueError("python commands must execute a file under the skill scripts directory")
        resolved_script = _resolve_script_path(skill_id, normalized_command[1])
        return [entrypoint, str(resolved_script), *normalized_command[2:]]

    if entrypoint in SHELL_COMMANDS:
        if len(normalized_command) < 2 or not _looks_like_script_path(normalized_command[1]):
            raise ValueError("shell commands must execute a file under the skill scripts directory")
        resolved_script = _resolve_script_path(skill_id, normalized_command[1])
        return [entrypoint, str(resolved_script), *normalized_command[2:]]

    raise ValueError("command entrypoint must be a skill script or python executing a skill script")


def _validate_command_argv(command_argv: List[str]) -> None:
    """Reject shell metacharacters in command argv items."""
    for item in command_argv:
        matched_chars = sorted(char for char in COMMAND_INJECTION_CHARS if char in item)
        if matched_chars:
            raise ValueError(
                "command arguments contain unsupported shell metacharacters: "
                + ", ".join(repr(char) for char in matched_chars)
            )


def _build_skill_script_env() -> Dict[str, str]:
    """
    构建 skill 脚本子进程环境变量。

    Returns:
        仅包含安全白名单变量和数据库中 Tushare 配置的子进程环境。
    """
    env = {
        key: value
        for key in SKILL_SCRIPT_ENV_ALLOWLIST
        if (value := os.environ.get(key)) is not None
    }
    tushare_api_url = get_data_source_config_value(TUSHARE_API_SETTING_KEY)
    tushare_token = get_data_source_config_value(TUSHARE_TOKEN_SETTING_KEY)
    if tushare_api_url:
        env["TUSHARE_API"] = tushare_api_url
    if tushare_token:
        env["TUSHARE_TOKEN"] = tushare_token
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


@tool
async def run_skill_script(
    skill_id: str,
    command: List[str],
    stdin: Optional[str] = None,
    timeout_seconds: int = DEFAULT_SCRIPT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    执行外部 skill 的脚本或二进制命令。

    推荐用法:
    - `command=["python", "scripts/tool.py", "--flag", "value"]`
    - `command=["bash", "scripts/tool.sh", "--flag", "value"]`
    - `command=["sh", "scripts/tool.sh", "--flag", "value"]`
    - `command=["scripts/tool_binary", "--flag", "value"]`
    - 复杂 JSON、URL query、自然语言文本或大块参数放入 `stdin`，由脚本自行解析。

    禁止用法:
    - 不接受 shell 字符串，只接受 argv 列表。
    - 不允许 `python -c`、`python -m`、`sh -c`、`bash -c`。
    - 不允许实际执行入口位于 skill 的 `scripts/` 目录之外。
    - 不允许只在后续参数中附带 `scripts/...` 来绕过入口校验。
    - 不允许在 `command` 任一项中使用 shell 注入元字符: `; | & ` $ < >`、换行、回车、NUL。

    Args:
        skill_id: skill 目录名，例如 `tushare-data`。
        command: 完整命令 argv 列表，例如 `["python", "scripts/query_daily.py"]`。
        stdin: 可选原始标准输入文本。skill 文档要求 stdin 文本时使用。
        timeout_seconds: 超时时间，默认 120 秒。

    Returns:
        exit_code、stdout、stderr 和是否超时。禁止执行 skill scripts 目录外文件。
    """
    skill = get_skill(skill_id)
    if skill is None:
        return _format_error(f"Skill not found: {skill_id}", skill_id=skill_id)

    try:
        command_argv = _build_explicit_command(skill_id, command)
        _validate_command_argv(command_argv)
    except ValueError as exc:
        return _format_error(str(exc), skill_id=skill_id, command=command)

    timeout = max(1, int(timeout_seconds or DEFAULT_SCRIPT_TIMEOUT_SECONDS))
    stdin_payload = None
    if stdin is not None:
        stdin_payload = str(stdin).encode("utf-8")

    try:
        process = await asyncio.create_subprocess_exec(
            *command_argv,
            cwd=str(skill.root_path),
            env=_build_skill_script_env(),
            stdin=asyncio.subprocess.PIPE if stdin_payload is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return _format_error(str(exc), skill_id=skill_id, command=command_argv)

    timed_out = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(stdin_payload), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return {
        "success": process.returncode == 0 and not timed_out,
        "skill_id": skill_id,
        "command": command_argv,
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
    }


def get_skills_loader_tools() -> List[Any]:
    """
    Return LangChain tools for external skills.

    Returns:
        List of tools available to agentic workflows.
    """
    return [list_skills, load_skill, read_skill_file, run_skill_script]
