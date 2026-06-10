import json
from unittest.mock import patch

import pytest

from app.services.pooled_sandbox_pool import PooledSandboxPool, PooledSandboxWorker
from app.services.python_sandbox_pool import PrewarmedSandboxPool
from app.config import get_settings


class FakeStdout:
    """测试用 stdout 行读取器。"""

    def __init__(self, lines: list[str]) -> None:
        """
        初始化待读取行。

        Args:
            lines: 预置 stdout 行。
        """
        self._lines = [line.encode("utf-8") for line in lines]

    async def readline(self) -> bytes:
        """
        读取下一行测试输出。

        Returns:
            下一行字节；耗尽时返回空字节。
        """
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeProcess:
    """测试用子进程对象。"""

    def __init__(self, lines: list[str]) -> None:
        """
        初始化 stdout。

        Args:
            lines: 预置 stdout 行。
        """
        self.stdout = FakeStdout(lines)


class FakeStdin:
    """测试用 stdin 写入器。"""

    def __init__(self) -> None:
        """初始化写入记录。"""
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        """
        记录写入数据。

        Args:
            data: 写入 worker stdin 的字节。
        """
        self.writes.append(data)

    async def drain(self) -> None:
        """模拟异步写缓冲刷新。"""


class FakePooledProcess:
    """测试用持久 worker 进程。"""

    def __init__(self, lines: list[str]) -> None:
        """
        初始化 stdin/stdout 和进程状态。

        Args:
            lines: 预置 stdout 行。
        """
        self.stdin = FakeStdin()
        self.stdout = FakeStdout(lines)
        self.returncode = None
        self.killed = False

    def kill(self) -> None:
        """记录进程被终止。"""
        self.killed = True
        self.returncode = -9


def test_sandbox_pool_defaults_limit_startup_prewarm_to_one_worker() -> None:
    """验证启动阶段默认只预热 1 个 worker，避免占用过多内存。"""
    settings = get_settings()

    assert settings.SANDBOX_STARTUP_PREWARM_WORKERS == 1
    assert settings.SANDBOX_WORKER_POOL_MAX_STARTING == 2
    assert settings.SANDBOX_WORKER_POOL_SIZE == 4
    assert settings.SANDBOX_PREWARM_MAX_STARTING == 2
    assert settings.SANDBOX_PREWARM_POOL_SIZE == 4


@pytest.mark.asyncio
async def test_prewarmed_pool_ready_reader_allows_only_pyodide_package_noise() -> None:
    """验证 ready 读取器会跳过 Pyodide 包加载噪声。"""
    pool = PrewarmedSandboxPool()
    process = FakeProcess([
        "Loading numpy, pandas, python-dateutil, pytz, six\n",
        "Loaded numpy, pandas, python-dateutil, pytz, six\n",
        '{"type":"ready","worker_id":"worker-1"}\n',
    ])

    ready = await pool._read_ready_message(process)

    assert ready["type"] == "ready"
    assert ready["worker_id"] == "worker-1"


@pytest.mark.asyncio
async def test_prewarmed_pool_ready_reader_logs_unknown_non_json_stdout() -> None:
    """验证 ready 前未知 stdout 会记录警告并继续等待协议消息。"""
    pool = PrewarmedSandboxPool()
    process = FakeProcess([
        "unexpected debug output\n",
        '{"type":"ready","worker_id":"worker-1"}\n',
    ])

    with patch("app.services.python_sandbox_pool.logger") as logger_mock:
        ready = await pool._read_ready_message(process)

    assert ready["type"] == "ready"
    assert ready["worker_id"] == "worker-1"
    logger_mock.warning.assert_called_once()
    assert "unexpected debug output" == logger_mock.warning.call_args.kwargs["extra"]["worker_output"]


@pytest.mark.asyncio
async def test_pooled_pool_ready_reader_allows_pyodide_package_noise() -> None:
    """验证持久池 ready 读取器会跳过 Pyodide 包加载噪声。"""
    pool = PooledSandboxPool()
    process = FakeProcess([
        "Loading numpy, pandas, python-dateutil, pytz, six\n",
        "Loaded numpy, pandas, python-dateutil, pytz, six\n",
        '{"type":"ready","worker_id":"worker-1"}\n',
    ])

    ready = await pool._read_ready_message(process)

    assert ready["type"] == "ready"
    assert ready["worker_id"] == "worker-1"


@pytest.mark.asyncio
async def test_pooled_pool_reuses_worker_after_success() -> None:
    """验证持久池执行成功后会复用同一个 worker。"""
    first_request = {
        "type": "execute",
        "id": "req-1",
        "code": "print(1)",
        "limits": {"stdout_max_bytes": 1000, "stderr_max_bytes": 1000},
    }
    second_request = {
        "type": "execute",
        "id": "req-2",
        "code": "print(2)",
        "limits": {"stdout_max_bytes": 1000, "stderr_max_bytes": 1000},
    }
    first_result = {
        "type": "result",
        "id": "req-1",
        "success": True,
        "stdout": "1\n",
        "stderr": "",
        "error": None,
        "metadata": {"sandbox_runtime": "deno_pooled_worker"},
    }
    second_result = {
        "type": "result",
        "id": "req-2",
        "success": True,
        "stdout": "2\n",
        "stderr": "",
        "error": None,
        "metadata": {"sandbox_runtime": "deno_pooled_worker"},
    }
    process = FakePooledProcess([json.dumps(first_result) + "\n", json.dumps(second_result) + "\n"])
    worker = PooledSandboxWorker(process=process, worker_id="worker-1", startup_ms=1)
    pool = PooledSandboxPool()
    await pool._ready.put(worker)

    with patch.object(get_settings(), "SANDBOX_WORKER_POOL_SIZE", 1):
        first_payload = await pool.execute(json.dumps(first_request), timeout_seconds=1)
        second_payload = await pool.execute(json.dumps(second_request), timeout_seconds=1)

    assert first_payload["stdout"] == "1\n"
    assert second_payload["stdout"] == "2\n"
    assert process.killed is False
    assert process.stdin.writes == [
        (json.dumps(first_request) + "\n").encode("utf-8"),
        (json.dumps(second_request) + "\n").encode("utf-8"),
    ]
    assert await pool._ready.get() is worker
