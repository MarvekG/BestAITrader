import ast
import time
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings
from app.core.logger import get_logger


logger = get_logger(__name__)
PY_SANDBOX_CODE_MAX_CHARS = 500_000

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
    """沙箱基础异常。"""


class SandboxValidationError(SandboxError):
    """用户代码违反沙箱静态校验规则。"""


def _module_name(module_name: Optional[str]) -> str:
    """
    提取导入模块根名称。

    Args:
        module_name: AST 中记录的模块名称。

    Returns:
        模块根名称；空值会返回空字符串。
    """
    return (module_name or "").split(".", 1)[0]


class SandboxValidator(ast.NodeVisitor):
    """拒绝可能逃逸计算沙箱的 Python 语法。"""

    def generic_visit(self, node: ast.AST) -> None:
        """
        检查通用 AST 节点是否属于禁用语法。

        Args:
            node: 当前遍历到的 AST 节点。

        Raises:
            SandboxValidationError: 当前节点类型被禁用。
        """
        if isinstance(node, DISALLOWED_NODE_TYPES):
            raise SandboxValidationError(f"Unsupported syntax: {type(node).__name__}")
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        """
        检查普通 import 是否引用禁用模块。

        Args:
            node: import AST 节点。

        Raises:
            SandboxValidationError: 导入了禁用模块。
        """
        for alias in node.names:
            module_name = _module_name(alias.name)
            if module_name in BLOCKED_IMPORTS:
                raise SandboxValidationError(f"Import not allowed: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """
        检查 from import 是否引用禁用模块。

        Args:
            node: from import AST 节点。

        Raises:
            SandboxValidationError: 导入了禁用模块。
        """
        module_name = _module_name(node.module)
        if module_name in BLOCKED_IMPORTS:
            raise SandboxValidationError(f"Import not allowed: {node.module}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """
        检查函数调用是否命中禁用内置函数。

        Args:
            node: 函数调用 AST 节点。

        Raises:
            SandboxValidationError: 调用了禁用函数。
        """
        if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            raise SandboxValidationError(f"Call not allowed: {node.func.id}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """
        检查变量名称是否命中禁用名称。

        Args:
            node: 名称 AST 节点。

        Raises:
            SandboxValidationError: 访问了禁用名称。
        """
        if node.id in BLOCKED_NAMES:
            raise SandboxValidationError(f"Name not allowed: {node.id}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """
        检查属性访问是否命中特殊逃逸属性。

        Args:
            node: 属性访问 AST 节点。

        Raises:
            SandboxValidationError: 访问了禁用属性。
        """
        if node.attr in BLOCKED_ATTRIBUTES or node.attr.startswith("__") or node.attr.endswith("__"):
            raise SandboxValidationError(f"Attribute access not allowed: {node.attr}")
        self.generic_visit(node)


def validate_python_code(code: str) -> None:
    """
    对用户 Python 代码执行静态安全校验。

    Args:
        code: 待执行的 Python 源码。

    Raises:
        SandboxValidationError: 代码包含禁用语法、模块、函数或属性。
    """
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as exc:
        raise SandboxValidationError(f"Invalid Python syntax: {exc.msg}") from exc
    SandboxValidator().visit(tree)


def _truncate_text(text: str, limit: int) -> str:
    """
    按 UTF-8 字节数截断沙箱输出。

    Args:
        text: 原始输出文本。
        limit: 最大保留字节数。

    Returns:
        截断后的文本，发生截断时追加提示。
    """
    if len(text.encode("utf-8")) <= limit:
        return text
    encoded = text.encode("utf-8")[:limit]
    truncated = encoded.decode("utf-8", errors="ignore")
    return truncated + "\n... [truncated]"


def _normalize_response(payload: Dict[str, Any], started_at: float) -> Dict[str, Any]:
    """
    统一独立沙箱服务响应和本地错误响应结构。

    Args:
        payload: 独立沙箱服务返回的响应体或本地错误字段。
        started_at: 外层请求开始时间。

    Returns:
        标准沙箱执行响应。
    """
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
    response["metadata"].setdefault("sandbox_runtime", "sandbox_service")
    return response


def _build_service_payload(code: str, execution_mode: str | None = None) -> Dict[str, Any]:
    """
    构建发往独立 Python 沙箱服务的请求体。

    Args:
        code: 待执行 Python 代码。
        execution_mode: 本次请求的执行模式。

    Returns:
        HTTP JSON 请求体。
    """
    return {
        "code": code,
        "execution_mode": execution_mode or settings.PY_SANDBOX_EXECUTION_MODE,
        "timeout_seconds": settings.PY_SANDBOX_TIMEOUT_SECONDS,
        "limits": {
            "stdout_max_bytes": settings.PY_SANDBOX_STDOUT_MAX_BYTES,
            "stderr_max_bytes": settings.PY_SANDBOX_STDERR_MAX_BYTES,
        },
    }


def _build_error_response(started_at: float, error: str, error_type: str) -> Dict[str, Any]:
    """
    构建本地校验或服务调用失败响应。

    Args:
        started_at: 外层请求开始时间。
        error: 错误描述。
        error_type: 错误类型标识。

    Returns:
        标准沙箱执行响应。
    """
    return _normalize_response(
        {
            "success": False,
            "error": error,
            "metadata": {"error_type": error_type},
        },
        started_at,
    )


async def execute_python_in_sandbox(code: str, execution_mode: str | None = None) -> Dict[str, Any]:
    """
    通过独立沙箱服务执行受限 Python 计算代码。

    Args:
        code: 待执行 Python 代码。
        execution_mode: 本次请求的执行模式。

    Returns:
        标准沙箱执行响应。
    """
    started_at = time.monotonic()
    if len(code) > PY_SANDBOX_CODE_MAX_CHARS:
        return _build_error_response(
            started_at,
            f"Python sandbox code is too large: {len(code)} characters exceeds max {PY_SANDBOX_CODE_MAX_CHARS}.",
            "request_too_large",
        )
    if not settings.PY_SANDBOX_ENABLED:
        return _build_error_response(started_at, "Python sandbox is unavailable", "sandbox_disabled")

    try:
        validate_python_code(code)
    except SandboxValidationError as exc:
        logger.warning("python sandbox validation rejected code", extra={"error": str(exc)})
        return _build_error_response(started_at, str(exc), "validation_error")

    base_url = settings.PY_SANDBOX_BASE_URL.rstrip("/")
    timeout = httpx.Timeout(settings.PY_SANDBOX_HTTP_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
            response = await client.post("/execute", json=_build_service_payload(code, execution_mode))
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("python sandbox service request failed", extra={"error": str(exc), "base_url": base_url})
        return _build_error_response(
            started_at,
            f"Python sandbox service request failed: {type(exc).__name__}: {exc}",
            "sandbox_service_error",
        )

    if not isinstance(payload, dict):
        return _build_error_response(
            started_at,
            f"Python sandbox service response must be an object, got {type(payload).__name__}",
            "protocol_error",
        )
    return _normalize_response(payload, started_at)
