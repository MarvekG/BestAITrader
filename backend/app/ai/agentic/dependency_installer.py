from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

MAX_OUTPUT_CHARS = 8_000
_INSTALL_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class DependencyInstallResult:
    """Result payload for one dependency installation attempt."""

    status: str
    requirements: list[str]
    command: list[str]
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Convert the result into a JSON-serializable payload.

        Returns:
            JSON-serializable dependency installation metadata.
        """
        return {
            "status": self.status,
            "requirements": self.requirements,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "message": self.message,
        }


class DependencyInstallError(RuntimeError):
    """Raised when declared Python dependencies cannot be installed."""

    def __init__(self, message: str, result: DependencyInstallResult):
        super().__init__(message)
        self.result = result


def format_dependency_failure_message(result: DependencyInstallResult) -> str:
    """
    Build a concise human-readable dependency installation error message.

    Args:
        result: Dependency installation result.

    Returns:
        One-line summary including the most relevant failure detail.
    """
    details: list[str] = []
    if result.exit_code is not None:
        details.append(f"exit_code={result.exit_code}")

    stderr_line = _first_non_empty_line(result.stderr)
    stdout_line = _first_non_empty_line(result.stdout)
    if stderr_line:
        details.append(f"stderr={stderr_line}")
    elif stdout_line:
        details.append(f"stdout={stdout_line}")

    if not details:
        return result.message
    return f"{result.message} ({', '.join(details)})"


def _trim_output(value: str) -> str:
    if len(value) <= MAX_OUTPUT_CHARS:
        return value
    return f"{value[:MAX_OUTPUT_CHARS]}\n...<truncated>"


def _first_non_empty_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _strip_inline_comment(line: str) -> str:
    if " #" not in line:
        return line
    return line.split(" #", 1)[0].rstrip()


def normalize_requirements_text(requirements_text: str | None) -> list[str]:
    """
    Normalize runtime Python dependency declarations.

    Args:
        requirements_text: Raw requirements.txt-style content.

    Returns:
        Non-empty requirement lines to pass to pip through a temporary requirements file.

    Raises:
        ValueError: If the declaration is too large.
    """
    if not requirements_text:
        return []

    requirements: list[str] = []
    for raw_line in requirements_text.replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = _strip_inline_comment(line)
        if not line:
            continue
        if len(line) > 300:
            raise ValueError("Dependency requirement line is too long")
        if any(char in line for char in ("\x00", "\n", "\r")):
            raise ValueError("Dependency requirement line contains unsupported control characters")
        requirements.append(line)

    max_requirements = settings.AGENTIC_DEPENDENCY_INSTALL_MAX_REQUIREMENTS
    if len(requirements) > max_requirements:
        raise ValueError(f"Too many dependency requirements: {len(requirements)} > {max_requirements}")
    return requirements


async def install_python_requirements(
    requirements_text: str | None,
    *,
    component: str,
) -> DependencyInstallResult:
    """
    Install declared Python dependencies into the current backend Python environment.

    Args:
        requirements_text: Raw requirements.txt-style content.
        component: Human-readable caller name for logging.

    Returns:
        Dependency installation result.

    Raises:
        DependencyInstallError: If pip exits unsuccessfully or times out.
        ValueError: If the requirements declaration is invalid.
    """
    requirements = normalize_requirements_text(requirements_text)
    if not requirements:
        return DependencyInstallResult(
            status="skipped",
            requirements=[],
            command=[],
            message="No Python dependencies declared.",
        )

    if not settings.ENABLE_RUNTIME_EXTENSIONS:
        result = DependencyInstallResult(
            status="error",
            requirements=requirements,
            command=[],
            message="Runtime extension installation is disabled.",
        )
        logger.error(
            "Runtime extension installation disabled for %s: requirements=%s",
            component,
            requirements,
        )
        raise DependencyInstallError(result.message, result)

    async with _INSTALL_LOCK:
        return await _run_pip_install(requirements, component=component)


async def _run_pip_install(requirements: list[str], *, component: str) -> DependencyInstallResult:
    requirements_file_path = _write_temporary_requirements(requirements)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--user",
        "--disable-pip-version-check",
        "--no-input",
        "-r",
        str(requirements_file_path),
    ]
    logger.info("Installing runtime dependencies for %s: %s", component, requirements)

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.AGENTIC_DEPENDENCY_INSTALL_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            result = _build_result(
                requirements=requirements,
                command=command,
                exit_code=process.returncode,
                stdout_bytes=stdout_bytes,
                stderr_bytes=stderr_bytes,
                status="error",
                message="Dependency installation timed out.",
            )
            logger.error(
                "Dependency installation timed out for %s: command=%s requirements=%s stdout=%s stderr=%s",
                component,
                command,
                requirements,
                result.stdout,
                result.stderr,
            )
            raise DependencyInstallError(result.message, result) from exc

        status = "success" if process.returncode == 0 else "error"
        message = "Dependency installation succeeded." if status == "success" else "Dependency installation failed."
        result = _build_result(
            requirements=requirements,
            command=command,
            exit_code=process.returncode,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
            status=status,
            message=message,
        )
        if process.returncode != 0:
            logger.error(
                "Dependency installation failed for %s: command=%s requirements=%s exit_code=%s stdout=%s stderr=%s",
                component,
                command,
                requirements,
                process.returncode,
                result.stdout,
                result.stderr,
            )
            raise DependencyInstallError(message, result)
        return result
    finally:
        try:
            requirements_file_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove temporary requirements file %s: %s", requirements_file_path, exc)


def _write_temporary_requirements(requirements: list[str]) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix="agentic-requirements-", suffix=".txt")
    path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            file_obj.write("\n".join(requirements))
            file_obj.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def _build_result(
    *,
    requirements: list[str],
    command: list[str],
    exit_code: int | None,
    stdout_bytes: bytes,
    stderr_bytes: bytes,
    status: str,
    message: str,
) -> DependencyInstallResult:
    stdout = _trim_output(stdout_bytes.decode("utf-8", errors="replace"))
    stderr = _trim_output(stderr_bytes.decode("utf-8", errors="replace"))
    return DependencyInstallResult(
        status=status,
        requirements=requirements,
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        message=message,
    )
