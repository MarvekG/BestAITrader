import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import SANDBOX_ROOT, get_settings
from app.core.logger import get_logger
from app.services.python_sandbox_pool import (
    PrewarmedSandboxPoolError,
    _is_ignorable_pyodide_startup_output,
    _resolve_executable,
)


logger = get_logger(__name__)


class PooledSandboxAcquireTimeout(PrewarmedSandboxPoolError):
    """获取持久 worker 超时。"""


@dataclass
class PooledSandboxWorker:
    """保存一个持久 Pyodide worker 的进程状态。"""

    process: asyncio.subprocess.Process
    worker_id: str
    startup_ms: int


class PooledSandboxPool:
    """管理可复用的 Pyodide worker 执行池。"""

    def __init__(self) -> None:
        """初始化持久 worker 池。"""
        self._ready: asyncio.Queue[PooledSandboxWorker] = asyncio.Queue()
        self._starting = 0
        self._busy = 0
        self._lock = asyncio.Lock()
        self._closed = False

    async def prewarm(self) -> None:
        """启动后台持久 worker 补池任务。"""
        settings = get_settings()
        if _resolve_executable(settings.SANDBOX_DENO_EXECUTABLE) is None:
            logger.warning(
                "pooled sandbox skipped because Deno executable is unavailable",
                extra={"deno_executable": settings.SANDBOX_DENO_EXECUTABLE},
            )
            return
        await self._replenish()

    async def shutdown(self) -> None:
        """关闭 ready 队列中的持久 worker。"""
        self._closed = True
        while not self._ready.empty():
            worker = await self._ready.get()
            _kill_worker(worker.process)

    async def execute(self, request_json: str, timeout_seconds: int) -> Dict[str, Any]:
        """
        使用一个持久 worker 执行单次沙箱请求，成功后归还池中。

        Args:
            request_json: 一行 execute 请求 JSON。
            timeout_seconds: 用户代码执行超时时间。

        Returns:
            worker 返回的沙箱结果 payload。

        Raises:
            PooledSandboxAcquireTimeout: ready worker 在限定时间内不可用。
            PrewarmedSandboxPoolError: worker 协议或管道异常。
            asyncio.TimeoutError: 用户代码执行超时。
        """
        worker = await self._acquire()
        reusable = False
        try:
            payload = await self._execute_worker(worker, request_json, timeout_seconds)
            reusable = True
            return payload
        finally:
            async with self._lock:
                self._busy = max(0, self._busy - 1)
            if reusable and not self._closed and worker.process.returncode is None:
                await self._ready.put(worker)
            else:
                _kill_worker(worker.process)
                await self._replenish()

    async def _acquire(self) -> PooledSandboxWorker:
        """
        获取一个 ready 持久 worker。

        Returns:
            已完成 Pyodide 预热的持久 worker。

        Raises:
            PooledSandboxAcquireTimeout: ready worker 获取超时。
        """
        settings = get_settings()
        await self._replenish()
        try:
            worker = await asyncio.wait_for(
                self._ready.get(),
                timeout=settings.SANDBOX_WORKER_ACQUIRE_TIMEOUT_SECONDS,
            )
            async with self._lock:
                self._busy += 1
            return worker
        except asyncio.TimeoutError as exc:
            raise PooledSandboxAcquireTimeout("Pooled sandbox worker unavailable") from exc

    async def _replenish(self) -> None:
        """按配置异步补充持久 worker。"""
        if self._closed:
            return
        settings = get_settings()
        async with self._lock:
            target = max(1, int(settings.SANDBOX_WORKER_POOL_SIZE or 1))
            max_starting = max(1, int(settings.SANDBOX_WORKER_POOL_MAX_STARTING or 1))
            deficit = target - self._ready.qsize() - self._starting - self._busy
            starts = min(deficit, max_starting - self._starting)
            for _ in range(max(0, starts)):
                self._starting += 1
                asyncio.create_task(self._start_worker())

    async def _start_worker(self) -> None:
        """启动一个持久 Deno worker 并等待 ready 协议消息。"""
        started_at = time.monotonic()
        process: Optional[asyncio.subprocess.Process] = None
        try:
            command = _build_pooled_worker_command()
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(SANDBOX_ROOT),
            )
            if process.stdout is None:
                raise PrewarmedSandboxPoolError("Worker stdout unavailable")
            ready = await self._read_ready_message(process)
            worker = PooledSandboxWorker(
                process=process,
                worker_id=str(ready.get("worker_id") or uuid.uuid4()),
                startup_ms=int((time.monotonic() - started_at) * 1000),
            )
            await self._ready.put(worker)
        except (OSError, json.JSONDecodeError, asyncio.TimeoutError, PrewarmedSandboxPoolError) as exc:
            logger.warning("pooled sandbox worker failed to start", extra={"error": str(exc)})
            if process is not None:
                _kill_worker(process)
        finally:
            async with self._lock:
                self._starting = max(0, self._starting - 1)

    async def _execute_worker(
        self,
        worker: PooledSandboxWorker,
        request_json: str,
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        """
        通过 JSON Lines 协议驱动一个持久 worker 执行一次请求。

        Args:
            worker: 已预热的持久 worker。
            request_json: 一行 execute 请求 JSON。
            timeout_seconds: 执行超时秒数。

        Returns:
            去掉协议 `type` 字段后的 result payload。

        Raises:
            PrewarmedSandboxPoolError: 管道不可用、协议类型错误或请求 ID 错配。
            asyncio.TimeoutError: 等待 result 超时。
        """
        if worker.process.stdin is None or worker.process.stdout is None:
            raise PrewarmedSandboxPoolError("Worker pipes unavailable")
        if worker.process.returncode is not None:
            raise PrewarmedSandboxPoolError("Worker already exited")

        request_payload = json.loads(request_json)
        worker.process.stdin.write((request_json + "\n").encode("utf-8"))
        await worker.process.stdin.drain()
        try:
            result_line = await asyncio.wait_for(worker.process.stdout.readline(), timeout=timeout_seconds + 1)
        except asyncio.TimeoutError:
            _kill_worker(worker.process)
            raise

        try:
            payload = json.loads(result_line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            _kill_worker(worker.process)
            raise PrewarmedSandboxPoolError("Invalid worker result JSON") from exc

        if payload.get("type") != "result":
            _kill_worker(worker.process)
            raise PrewarmedSandboxPoolError("Invalid worker result message")
        if payload.get("id") != request_payload.get("id"):
            _kill_worker(worker.process)
            raise PrewarmedSandboxPoolError("Worker result id mismatch")
        payload.pop("type", None)
        return payload

    async def _read_ready_message(self, process: asyncio.subprocess.Process) -> Dict[str, Any]:
        """
        读取持久 worker ready 协议消息。

        Args:
            process: 刚启动的 Deno worker 进程。

        Returns:
            ready 协议 JSON。

        Raises:
            PrewarmedSandboxPoolError: worker 在 ready 前退出或输出非法 JSON 协议。
            asyncio.TimeoutError: ready 等待超时。
        """
        settings = get_settings()
        if process.stdout is None:
            raise PrewarmedSandboxPoolError("Worker stdout unavailable")

        deadline = time.monotonic() + settings.SANDBOX_WORKER_STARTUP_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            line = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            if not line:
                raise PrewarmedSandboxPoolError("Worker exited before ready")
            text = line.decode("utf-8", errors="replace").strip()
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                if _is_ignorable_pyodide_startup_output(text):
                    logger.debug("ignored Pyodide package startup output", extra={"worker_output": text})
                    continue
                logger.warning("unexpected pooled worker startup output", extra={"worker_output": text})
                continue
            if payload.get("type") == "ready":
                return payload
            raise PrewarmedSandboxPoolError("Invalid worker ready message")


def _build_pooled_worker_command() -> list[str]:
    """
    构造启动持久 Deno worker 的命令。

    Returns:
        可传给 `asyncio.create_subprocess_exec` 的命令参数。
    """
    settings = get_settings()
    runner_path = Path(settings.SANDBOX_POOLED_WORKER_RUNNER_PATH)
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


def _kill_worker(process: asyncio.subprocess.Process) -> None:
    """
    安全终止 worker 进程。

    Args:
        process: 待终止的 worker 进程。
    """
    try:
        process.kill()
    except ProcessLookupError:
        pass


_POOLED_SANDBOX_POOL: Optional[PooledSandboxPool] = None


def get_pooled_sandbox_pool() -> PooledSandboxPool:
    """
    获取全局持久沙箱池。

    Returns:
        当前进程内共享的持久沙箱池实例。
    """
    global _POOLED_SANDBOX_POOL
    if _POOLED_SANDBOX_POOL is None:
        _POOLED_SANDBOX_POOL = PooledSandboxPool()
    return _POOLED_SANDBOX_POOL
