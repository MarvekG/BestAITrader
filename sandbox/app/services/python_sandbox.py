import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import SANDBOX_ROOT, get_settings
from app.core.logger import get_logger
from app.schemas import SandboxLimits
from app.services.pooled_sandbox_pool import PooledSandboxAcquireTimeout, get_pooled_sandbox_pool
from app.services.validator import SandboxValidationError, validate_python_code
from app.services.python_sandbox_pool import (
    PrewarmedSandboxAcquireTimeout,
    _resolve_executable,
    get_prewarmed_sandbox_pool,
)


logger = get_logger(__name__)
_ASYNC_SANDBOX_LIMITER: Optional[asyncio.Semaphore] = None


def _truncate_text(text: str, limit: int) -> str:
    """
    按 UTF-8 字节数截断输出文本。

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


def _get_async_sandbox_limiter() -> asyncio.Semaphore:
    """
    获取异步沙箱执行限流器，超过上限的请求会等待。

    Returns:
        用于限制异步沙箱子进程数量的信号量。
    """
    global _ASYNC_SANDBOX_LIMITER

    if _ASYNC_SANDBOX_LIMITER is None:
        settings = get_settings()
        limit = max(1, int(settings.SANDBOX_MAX_CONCURRENT_EXECUTIONS or 1))
        _ASYNC_SANDBOX_LIMITER = asyncio.Semaphore(limit)
    return _ASYNC_SANDBOX_LIMITER


def _build_request(code: str, limits: SandboxLimits) -> Dict[str, Any]:
    """
    构建传给 Deno worker 的沙箱执行请求。

    Args:
        code: 待执行 Python 代码。
        limits: stdout/stderr 输出字节限制。

    Returns:
        Deno worker JSON 协议请求。
    """
    return {
        "type": "execute",
        "id": str(time.time_ns()),
        "code": code,
        "limits": {
            "stdout_max_bytes": limits.stdout_max_bytes,
            "stderr_max_bytes": limits.stderr_max_bytes,
        },
    }


def _build_command() -> list[str]:
    """
    构造一次性 Deno runner 启动命令。

    Returns:
        可传给 `asyncio.create_subprocess_exec` 的命令参数。
    """
    settings = get_settings()
    runner_path = Path(settings.SANDBOX_RUNNER_PATH)
    pyodide_root = Path(settings.SANDBOX_PYODIDE_ROOT)
    read_allowlist = ",".join([str(runner_path.parent), str(pyodide_root)])
    return [
        settings.SANDBOX_DENO_EXECUTABLE,
        "run",
        "--quiet",
        f"--allow-read={read_allowlist}",
        str(runner_path),
        str(pyodide_root),
    ]


def _normalize_response(payload: Dict[str, Any], started_at: float, limits: SandboxLimits) -> Dict[str, Any]:
    """
    统一 Deno worker 和服务内部错误响应结构。

    Args:
        payload: worker 或服务内部生成的响应字段。
        started_at: 外层请求开始时间。
        limits: stdout/stderr 输出字节限制。

    Returns:
        标准沙箱执行响应。
    """
    stdout = _truncate_text(str(payload.get("stdout", "")), limits.stdout_max_bytes)
    stderr = _truncate_text(str(payload.get("stderr", "")), limits.stderr_max_bytes)
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
    response["metadata"].setdefault(
        "runner_path",
        _runner_path_for_runtime(response["metadata"].get("sandbox_runtime")),
    )
    return response


def _runner_path_for_runtime(sandbox_runtime: object) -> str:
    """
    根据沙箱运行时类型选择 runner 路径。

    Args:
        sandbox_runtime: worker 返回的运行时标识。

    Returns:
        对应 runner 脚本路径。
    """
    settings = get_settings()
    if sandbox_runtime == "deno_pooled_worker":
        return settings.SANDBOX_POOLED_WORKER_RUNNER_PATH
    if sandbox_runtime == "deno_prewarmed_worker":
        return settings.SANDBOX_WORKER_RUNNER_PATH
    return settings.SANDBOX_RUNNER_PATH


def _prepare_execution(
    code: str,
    limits: SandboxLimits,
    started_at: float,
) -> tuple[Optional[Dict[str, Any]], Optional[str], list[str]]:
    """
    执行沙箱请求前的配置、静态校验和启动命令准备。

    Args:
        code: 待执行 Python 代码。
        limits: stdout/stderr 输出字节限制。
        started_at: 外层请求开始时间。

    Returns:
        早期响应、序列化请求和 Deno 命令三元组；早期响应不为空时无需继续执行。
    """
    settings = get_settings()
    if not settings.SANDBOX_ENABLED:
        return _normalize_response(
            {"success": False, "error": "Python sandbox is unavailable"},
            started_at,
            limits,
        ), None, []

    try:
        validate_python_code(code)
    except SandboxValidationError as exc:
        logger.warning("python sandbox validation rejected code", extra={"error": str(exc)})
        return _normalize_response(
            {
                "success": False,
                "error": str(exc),
                "metadata": {"error_type": "validation_error"},
            },
            started_at,
            limits,
        ), None, []

    executable = _resolve_executable(settings.SANDBOX_DENO_EXECUTABLE)
    if not executable:
        return _normalize_response(
            {
                "success": False,
                "error": f"Deno executable not found: {settings.SANDBOX_DENO_EXECUTABLE}",
                "metadata": {"error_type": "sandbox_boot_error"},
            },
            started_at,
            limits,
        ), None, []

    request = _build_request(code, limits)
    command = _build_command()
    command[0] = executable
    return None, json.dumps(request), command


def _parse_process_result(
    stdout: str,
    stderr: str,
    returncode: int,
    started_at: float,
    limits: SandboxLimits,
) -> Dict[str, Any]:
    """
    解析一次性 Deno runner 进程输出。

    Args:
        stdout: runner 标准输出。
        stderr: runner 标准错误。
        returncode: runner 退出码。
        started_at: 外层请求开始时间。
        limits: stdout/stderr 输出字节限制。

    Returns:
        标准沙箱执行响应。
    """
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
    return _normalize_response(payload, started_at, limits)


def _bound_timeout_seconds(timeout_seconds: int | None) -> int:
    """
    将请求超时限制在服务允许范围内。

    Args:
        timeout_seconds: 调用方请求的超时时间。

    Returns:
        最终用于执行的超时时间。
    """
    settings = get_settings()
    requested_timeout = int(timeout_seconds or settings.SANDBOX_TIMEOUT_SECONDS)
    return max(1, min(requested_timeout, settings.SANDBOX_MAX_TIMEOUT_SECONDS))


async def execute_python_in_sandbox(
    code: str,
    limits: SandboxLimits,
    timeout_seconds: int | None,
) -> Dict[str, Any]:
    """
    在独立 Deno/Pyodide 沙箱服务内执行 Python 代码。

    Args:
        code: 待执行 Python 代码。
        limits: stdout/stderr 输出字节限制。
        timeout_seconds: 调用方请求的执行超时时间。

    Returns:
        标准沙箱执行响应。
    """
    started_at = time.monotonic()
    execution_timeout = _bound_timeout_seconds(timeout_seconds)
    early_response, request_json, command = _prepare_execution(code, limits, started_at)
    if early_response is not None:
        return early_response

    settings = get_settings()
    if settings.SANDBOX_EXECUTION_MODE == "pooled_worker":
        try:
            payload = await get_pooled_sandbox_pool().execute(request_json, execution_timeout)
            return _normalize_response(payload, started_at, limits)
        except PooledSandboxAcquireTimeout:
            logger.info("pooled sandbox worker unavailable; falling back to subprocess")
        except asyncio.TimeoutError:
            logger.warning("pooled sandbox worker timed out")
            return _normalize_response(
                {
                    "success": False,
                    "error": "Python sandbox execution timed out",
                    "timed_out": True,
                    "metadata": {"error_type": "timeout_error", "sandbox_runtime": "deno_pooled_worker"},
                },
                started_at,
                limits,
            )
        except Exception as exc:
            logger.warning("pooled sandbox worker failed; falling back to subprocess", extra={"error": str(exc)})

    if settings.SANDBOX_EXECUTION_MODE == "one_shot_worker" and settings.SANDBOX_PREWARM_POOL_ENABLED:
        try:
            payload = await get_prewarmed_sandbox_pool().execute(request_json, execution_timeout)
            return _normalize_response(payload, started_at, limits)
        except PrewarmedSandboxAcquireTimeout:
            logger.info("prewarmed sandbox pool unavailable; falling back to subprocess")
        except asyncio.TimeoutError:
            logger.warning("prewarmed sandbox worker timed out")
            return _normalize_response(
                {
                    "success": False,
                    "error": "Python sandbox execution timed out",
                    "timed_out": True,
                    "metadata": {"error_type": "timeout_error", "sandbox_runtime": "deno_prewarmed_worker"},
                },
                started_at,
                limits,
            )
        except Exception as exc:
            logger.warning("prewarmed sandbox worker failed; falling back to subprocess", extra={"error": str(exc)})

    return await _execute_python_subprocess_once(request_json, command, started_at, limits, execution_timeout)


async def _execute_python_subprocess_once(
    request_json: str,
    command: list[str],
    started_at: float,
    limits: SandboxLimits,
    timeout_seconds: int,
) -> Dict[str, Any]:
    """
    使用一次性 Deno 子进程执行沙箱请求，作为预热池 fallback。

    Args:
        request_json: 已序列化的沙箱请求。
        command: Deno runner 启动命令。
        started_at: 外层请求开始时间。
        limits: stdout/stderr 输出字节限制。
        timeout_seconds: 用户代码执行超时时间。

    Returns:
        标准沙箱执行响应。
    """
    async with _get_async_sandbox_limiter():
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(SANDBOX_ROOT),
            )
        except OSError as exc:
            logger.error("python sandbox failed to start", extra={"error": str(exc)})
            return _normalize_response(
                {
                    "success": False,
                    "error": f"Python sandbox failed to start: {exc}",
                    "metadata": {"error_type": "sandbox_boot_error"},
                },
                started_at,
                limits,
            )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(request_json.encode("utf-8")),
                timeout=timeout_seconds + 1,
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
                limits,
            )

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    return _parse_process_result(stdout, stderr, process.returncode, started_at, limits)
