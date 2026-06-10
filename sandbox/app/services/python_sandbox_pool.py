import asyncio
import json
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import SANDBOX_ROOT, get_settings
from app.core.logger import get_logger


logger = get_logger(__name__)


class PrewarmedSandboxPoolError(Exception):
    """预热沙箱池基础错误。"""


class PrewarmedSandboxAcquireTimeout(PrewarmedSandboxPoolError):
    """获取预热 worker 超时。"""


@dataclass
class PrewarmedSandboxWorker:
    """保存一个已预热 one-shot worker 的进程状态。"""

    process: asyncio.subprocess.Process
    worker_id: str
    startup_ms: int


class PrewarmedSandboxPool:
    """管理 one-shot Pyodide worker 的预热池。"""

    def __init__(self) -> None:
        """初始化预热池内部队列和启动计数。"""
        self._ready: asyncio.Queue[PrewarmedSandboxWorker] = asyncio.Queue()
        self._starting = 0
        self._lock = asyncio.Lock()

    async def prewarm(self, target_size: int | None = None) -> None:
        """
        启动后台 one-shot worker 补池任务。

        Args:
            target_size: 本次预热希望维持的 ready worker 数量；未传入时使用池默认容量。
        """
        settings = get_settings()
        if _resolve_executable(settings.SANDBOX_DENO_EXECUTABLE) is None:
            logger.warning(
                "prewarmed sandbox pool skipped because Deno executable is unavailable",
                extra={"deno_executable": settings.SANDBOX_DENO_EXECUTABLE},
            )
            return
        await self._replenish(target_size)

    async def shutdown(self) -> None:
        """关闭 ready 队列中的预热 worker。"""
        while not self._ready.empty():
            worker = await self._ready.get()
            try:
                worker.process.kill()
            except ProcessLookupError:
                pass

    async def execute(self, request_json: str, timeout_seconds: int) -> Dict[str, Any]:
        """
        使用一个已预热 worker 执行单次沙箱请求。

        Args:
            request_json: 后端构造的 JSON Lines 请求内容。
            timeout_seconds: 用户代码执行超时时间。

        Returns:
            worker 返回的沙箱结果 payload。

        Raises:
            PrewarmedSandboxAcquireTimeout: ready worker 在限定时间内不可用。
            PrewarmedSandboxPoolError: worker 协议或管道异常。
            asyncio.TimeoutError: 用户代码执行超时。
        """
        worker = await self._acquire()
        try:
            return await self._execute_worker(worker, request_json, timeout_seconds)
        finally:
            await self._replenish()

    async def _acquire(self) -> PrewarmedSandboxWorker:
        """
        获取一个 ready worker，必要时触发后台补池。

        Returns:
            已完成 Pyodide 预热的 one-shot worker。

        Raises:
            PrewarmedSandboxAcquireTimeout: ready worker 获取超时。
        """
        settings = get_settings()
        await self._replenish()
        try:
            return await asyncio.wait_for(
                self._ready.get(),
                timeout=settings.SANDBOX_WORKER_ACQUIRE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise PrewarmedSandboxAcquireTimeout("Prewarmed sandbox worker unavailable") from exc

    async def _replenish(self, target_size: int | None = None) -> None:
        """
        按配置异步补充预热 worker。

        Args:
            target_size: 本次补池目标数量；未传入时使用池默认容量。
        """
        settings = get_settings()
        async with self._lock:
            target = max(1, int(target_size or settings.SANDBOX_PREWARM_POOL_SIZE or 1))
            max_starting = max(1, int(settings.SANDBOX_PREWARM_MAX_STARTING or 1))
            deficit = target - self._ready.qsize() - self._starting
            starts = min(deficit, max_starting - self._starting)
            for _ in range(max(0, starts)):
                self._starting += 1
                asyncio.create_task(self._start_worker())

    async def _start_worker(self) -> None:
        """启动一个 Deno one-shot worker 并等待 ready 协议消息。"""
        started_at = time.monotonic()
        process: Optional[asyncio.subprocess.Process] = None
        try:
            command = _build_worker_command()
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
            worker = PrewarmedSandboxWorker(
                process=process,
                worker_id=str(ready.get("worker_id") or uuid.uuid4()),
                startup_ms=int((time.monotonic() - started_at) * 1000),
            )
            await self._ready.put(worker)
        except (OSError, json.JSONDecodeError, asyncio.TimeoutError, PrewarmedSandboxPoolError) as exc:
            logger.warning("prewarmed sandbox worker failed to start", extra={"error": str(exc)})
            if process is not None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        finally:
            async with self._lock:
                self._starting = max(0, self._starting - 1)

    async def _execute_worker(
        self,
        worker: PrewarmedSandboxWorker,
        request_json: str,
        timeout_seconds: int,
    ) -> Dict[str, Any]:
        """
        通过 JSON Lines 协议驱动一个 worker 执行一次请求。

        Args:
            worker: 已预热且尚未执行用户代码的 worker。
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

        request_payload = json.loads(request_json)
        worker.process.stdin.write((request_json + "\n").encode("utf-8"))
        await worker.process.stdin.drain()
        worker.process.stdin.close()
        try:
            result_line = await asyncio.wait_for(worker.process.stdout.readline(), timeout=timeout_seconds + 1)
        except asyncio.TimeoutError:
            worker.process.kill()
            raise

        try:
            payload = json.loads(result_line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            worker.process.kill()
            raise PrewarmedSandboxPoolError("Invalid worker result JSON") from exc

        if payload.get("type") != "result":
            worker.process.kill()
            raise PrewarmedSandboxPoolError("Invalid worker result message")
        if payload.get("id") != request_payload.get("id"):
            worker.process.kill()
            raise PrewarmedSandboxPoolError("Worker result id mismatch")
        payload.pop("type", None)
        return payload

    async def _read_ready_message(self, process: asyncio.subprocess.Process) -> Dict[str, Any]:
        """
        读取 worker ready 协议消息，并跳过 Pyodide 启动噪声。

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
                logger.warning("unexpected prewarmed worker startup output", extra={"worker_output": text})
                continue
            if payload.get("type") == "ready":
                return payload
            raise PrewarmedSandboxPoolError("Invalid worker ready message")


def _resolve_executable(command: str) -> Optional[str]:
    """
    解析 Deno 可执行文件路径。

    Args:
        command: 可执行文件名或绝对路径。

    Returns:
        可执行文件绝对路径；不存在时返回 None。
    """
    if Path(command).is_absolute():
        return command if Path(command).exists() else None
    return shutil.which(command)


def _build_worker_command() -> list[str]:
    """
    构造启动 one-shot Deno worker 的命令。

    Returns:
        可传给 `asyncio.create_subprocess_exec` 的命令参数。
    """
    settings = get_settings()
    runner_path = Path(settings.SANDBOX_WORKER_RUNNER_PATH)
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


def _is_ignorable_pyodide_startup_output(text: str) -> bool:
    """
    判断 ready 前 stdout 是否为 Pyodide 包加载噪声。

    Args:
        text: worker stdout 的单行文本。

    Returns:
        如果是 Pyodide `loadPackage` 固定加载日志则返回 True。
    """
    return text.startswith("Loading ") or text.startswith("Loaded ")


_PREWARMED_SANDBOX_POOL: Optional[PrewarmedSandboxPool] = None


def get_prewarmed_sandbox_pool() -> PrewarmedSandboxPool:
    """
    获取全局预热沙箱池。

    Returns:
        当前进程内共享的预热沙箱池实例。
    """
    global _PREWARMED_SANDBOX_POOL
    if _PREWARMED_SANDBOX_POOL is None:
        _PREWARMED_SANDBOX_POOL = PrewarmedSandboxPool()
    return _PREWARMED_SANDBOX_POOL
