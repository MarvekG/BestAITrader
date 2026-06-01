import asyncio
import ast
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.core.config import PROJECT_ROOT, settings
from app.core.logger import get_logger

logger = get_logger(__name__)


BLOCKED_IMPORTS = {
    "os",
    "sys",
    "subprocess",
    "socket",
    "pathlib",
    "shutil",
    "tempfile",
    "ctypes",
    "importlib",
    "builtins",
    "multiprocessing",
}
BLOCKED_CALLS = {
    "open",
    "exec",
    "eval",
    "compile",
    "__import__",
    "input",
    "help",
    "dir",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "breakpoint",
}
BLOCKED_NAMES = BLOCKED_IMPORTS | {"__builtins__"}
BLOCKED_ATTRIBUTES = {
    "__class__",
    "__bases__",
    "__mro__",
    "__subclasses__",
    "__globals__",
    "__code__",
    "__closure__",
    "__func__",
    "__self__",
}
_DISALLOWED_NODE_TYPES = [
    ast.AsyncFunctionDef,
    ast.AsyncFor,
    ast.AsyncWith,
    ast.Await,
    ast.ClassDef,
    ast.With,
]
if hasattr(ast, "Match"):
    _DISALLOWED_NODE_TYPES.append(ast.Match)
DISALLOWED_NODE_TYPES = tuple(_DISALLOWED_NODE_TYPES)


class SandboxError(Exception):
    """Base sandbox exception."""


class SandboxValidationError(SandboxError):
    """Raised when code violates sandbox validation rules."""


def _module_name(module_name: Optional[str]) -> str:
    return (module_name or "").split(".", 1)[0]


class SandboxValidator(ast.NodeVisitor):
    """Reject code patterns that could escape a compute-only sandbox."""

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, DISALLOWED_NODE_TYPES):
            raise SandboxValidationError(f"Unsupported syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module_name = _module_name(alias.name)
            if module_name in BLOCKED_IMPORTS:
                raise SandboxValidationError(f"Import not allowed: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name = _module_name(node.module)
        if module_name in BLOCKED_IMPORTS:
            raise SandboxValidationError(f"Import not allowed: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            raise SandboxValidationError(f"Call not allowed: {node.func.id}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in BLOCKED_NAMES:
            raise SandboxValidationError(f"Name not allowed: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_ATTRIBUTES or node.attr.startswith("__") or node.attr.endswith("__"):
            raise SandboxValidationError(f"Attribute access not allowed: {node.attr}")
        self.generic_visit(node)


def validate_python_code(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxValidationError(f"Invalid Python syntax: {exc.msg}") from exc
    SandboxValidator().visit(tree)


def _truncate_text(text: str, limit: int) -> str:
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    truncated = encoded.decode("utf-8", errors="ignore")
    return truncated + "\n... [truncated]"


def _resolve_executable(command: str) -> Optional[str]:
    if Path(command).is_absolute():
        return command if Path(command).exists() else None
    return shutil.which(command)


def _build_request(code: str) -> Dict[str, Any]:
    return {
        "code": code,
        "limits": {
            "stdout_max_bytes": settings.PY_SANDBOX_STDOUT_MAX_BYTES,
            "stderr_max_bytes": settings.PY_SANDBOX_STDERR_MAX_BYTES,
        },
    }


def _build_command() -> Iterable[str]:
    runner_path = Path(settings.PY_SANDBOX_RUNNER_PATH)
    pyodide_root = Path(settings.PY_SANDBOX_PYODIDE_ROOT)
    read_allowlist = ",".join([str(runner_path.parent), str(pyodide_root)])
    return [
        settings.PY_SANDBOX_DENO_EXECUTABLE,
        "run",
        "--quiet",
        f"--allow-read={read_allowlist}",
        str(runner_path),
        str(pyodide_root),
    ]


def _normalize_response(payload: Dict[str, Any], started_at: float) -> Dict[str, Any]:
    stdout = _truncate_text(str(payload.get("stdout", "")), settings.PY_SANDBOX_STDOUT_MAX_BYTES)
    stderr = _truncate_text(str(payload.get("stderr", "")), settings.PY_SANDBOX_STDERR_MAX_BYTES)
    execution_time_ms = int(payload.get("execution_time_ms") or ((time.monotonic() - started_at) * 1000))
    response = {
        "success": bool(payload.get("success", False)),
        "stdout": stdout,
        "stderr": stderr,
        "error": payload.get("error"),
        "execution_time_ms": execution_time_ms,
        "timed_out": bool(payload.get("timed_out", False)),
        "truncated": bool(payload.get("truncated", False) or stdout.endswith("[truncated]") or stderr.endswith("[truncated]")),
        "metadata": payload.get("metadata", {}),
    }
    response["metadata"].setdefault("python_runtime", "pyodide")
    response["metadata"].setdefault("sandbox_runtime", "deno")
    response["metadata"].setdefault("runner_path", settings.PY_SANDBOX_RUNNER_PATH)
    return response


def _prepare_execution(code: str, started_at: float) -> tuple[Optional[Dict[str, Any]], Optional[str], list[str]]:
    if not settings.PY_SANDBOX_ENABLED:
        return _normalize_response(
            {"success": False, "error": "Python sandbox is unavailable"},
            started_at,
        ), None, []

    try:
        validate_python_code(code)
    except SandboxValidationError as exc:
        logger.warning("python sandbox validation rejected code: %s", exc)
        return _normalize_response(
            {
                "success": False,
                "error": str(exc),
                "metadata": {"error_type": "validation_error"},
            },
            started_at,
        ), None, []

    executable = _resolve_executable(settings.PY_SANDBOX_DENO_EXECUTABLE)
    if not executable:
        return _normalize_response(
            {
                "success": False,
                "error": f"Deno executable not found: {settings.PY_SANDBOX_DENO_EXECUTABLE}",
                "metadata": {"error_type": "sandbox_boot_error"},
            },
            started_at,
        ), None, []

    request = _build_request(code)
    command = list(_build_command())
    command[0] = executable
    return None, json.dumps(request), command


def _parse_process_result(stdout: str, stderr: str, returncode: int, started_at: float) -> Dict[str, Any]:
    stdout_lines = [line for line in stdout.splitlines() if line.strip()]
    if stdout_lines:
        try:
            payload = json.loads(stdout_lines[-1])
        except json.JSONDecodeError:
            payload = {
                "success": False,
                "error": "Sandbox returned invalid JSON",
                "stdout": stdout,
                "stderr": stderr,
                "metadata": {"error_type": "protocol_error", "returncode": returncode},
            }
    else:
        payload = {
            "success": False,
            "error": "Sandbox returned no output",
            "stdout": stdout,
            "stderr": stderr,
            "metadata": {"error_type": "protocol_error", "returncode": returncode},
        }

    if returncode != 0 and payload.get("success", False):
        payload["success"] = False
        payload["error"] = payload.get("error") or f"Sandbox exited with code {returncode}"
        payload.setdefault("metadata", {})["error_type"] = "runtime_error"

    payload.setdefault("stdout", stdout)
    payload.setdefault("stderr", stderr)
    payload.setdefault("metadata", {})
    payload["metadata"].setdefault("returncode", returncode)
    return _normalize_response(payload, started_at)


async def execute_python_in_sandbox(code: str) -> Dict[str, Any]:
    started_at = time.monotonic()
    early_response, request_json, command = _prepare_execution(code, started_at)
    if early_response is not None:
        return early_response

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
    except OSError as exc:
        logger.error("python sandbox failed to start: %s", exc)
        return _normalize_response(
            {
                "success": False,
                "error": f"Python sandbox failed to start: {exc}",
                "metadata": {"error_type": "sandbox_boot_error"},
            },
            started_at,
        )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(request_json.encode("utf-8")),
            timeout=settings.PY_SANDBOX_TIMEOUT_SECONDS + 1,
        )
    except asyncio.TimeoutError:
        logger.warning("python sandbox timed out")
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.communicate()
        return _normalize_response(
            {
                "success": False,
                "error": "Python sandbox execution timed out",
                "timed_out": True,
                "metadata": {"error_type": "timeout_error"},
            },
            started_at,
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return _parse_process_result(stdout, stderr, process.returncode, started_at)


def execute_python_in_sandbox_sync(code: str) -> Dict[str, Any]:
    started_at = time.monotonic()
    early_response, request_json, command = _prepare_execution(code, started_at)
    if early_response is not None:
        return early_response

    try:
        completed = subprocess.run(
            command,
            input=request_json,
            text=True,
            capture_output=True,
            timeout=settings.PY_SANDBOX_TIMEOUT_SECONDS + 1,
            cwd=str(PROJECT_ROOT),
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("python sandbox timed out")
        return _normalize_response(
            {
                "success": False,
                "error": "Python sandbox execution timed out",
                "timed_out": True,
                "metadata": {"error_type": "timeout_error"},
            },
            started_at,
        )
    except OSError as exc:
        logger.error("python sandbox failed to start: %s", exc)
        return _normalize_response(
            {
                "success": False,
                "error": f"Python sandbox failed to start: {exc}",
                "metadata": {"error_type": "sandbox_boot_error"},
            },
            started_at,
        )

    return _parse_process_result(completed.stdout, completed.stderr, completed.returncode, started_at)
