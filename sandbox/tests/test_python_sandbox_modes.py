import json
from unittest.mock import patch

import pytest

from app.config import get_settings
from app.schemas import SandboxLimits
from app.services.python_sandbox import execute_python_in_sandbox
from app.services.pooled_sandbox_pool import PooledSandboxAcquireTimeout


class FakePool:
    """测试用沙箱 worker 池。"""

    def __init__(self, sandbox_runtime: str) -> None:
        """
        初始化固定运行时响应。

        Args:
            sandbox_runtime: 返回 metadata 中的运行时标识。
        """
        self.sandbox_runtime = sandbox_runtime
        self.called = False

    async def execute(self, request_json: str, timeout_seconds: int) -> dict[str, object]:
        """
        记录调用并返回成功响应。

        Args:
            request_json: 序列化后的沙箱请求。
            timeout_seconds: 执行超时时间。

        Returns:
            标准沙箱响应 payload。
        """
        self.called = True
        assert "print(2 + 2)" in request_json
        assert timeout_seconds == 5
        return {
            "success": True,
            "stdout": "4\n",
            "stderr": "",
            "error": None,
            "execution_time_ms": 2,
            "timed_out": False,
            "truncated": False,
            "metadata": {"sandbox_runtime": self.sandbox_runtime},
        }


class UnavailablePool:
    """测试用不可用 worker 池。"""

    async def execute(self, request_json: str, timeout_seconds: int) -> dict[str, object]:
        """
        模拟获取 worker 超时。

        Args:
            request_json: 序列化后的沙箱请求。
            timeout_seconds: 执行超时时间。

        Raises:
            PooledSandboxAcquireTimeout: 始终表示 worker 不可用。
        """
        raise PooledSandboxAcquireTimeout("busy")


class FakeSubprocess:
    """测试用一次性子进程。"""

    def __init__(self) -> None:
        """初始化固定成功响应。"""
        self.returncode = 0

    async def communicate(self, data: bytes = b"") -> tuple[bytes, bytes]:
        """
        返回模拟 runner 输出。

        Args:
            data: 写入子进程 stdin 的请求字节。

        Returns:
            stdout/stderr 字节元组。
        """
        assert b"print(2 + 2)" in data
        payload = {
            "success": True,
            "stdout": "4\n",
            "stderr": "",
            "error": None,
            "execution_time_ms": 3,
            "timed_out": False,
            "truncated": False,
            "metadata": {"sandbox_runtime": "deno"},
        }
        return json.dumps(payload).encode("utf-8"), b""

    def kill(self) -> None:
        """兼容超时分支的进程接口。"""


@pytest.mark.asyncio
async def test_execute_python_uses_pooled_worker_mode_when_requested() -> None:
    """验证请求 pooled_worker 时使用持久 worker 池。"""
    settings = get_settings()
    fake_pool = FakePool("deno_pooled_worker")

    with patch.object(settings, "SANDBOX_TIMEOUT_SECONDS", 5), \
         patch("app.services.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.services.python_sandbox.get_pooled_sandbox_pool", return_value=fake_pool), \
         patch("app.services.python_sandbox.get_prewarmed_sandbox_pool") as one_shot_mock, \
         patch("app.services.python_sandbox.asyncio.create_subprocess_exec") as subprocess_mock:
        response = await execute_python_in_sandbox(
            "print(2 + 2)",
            SandboxLimits(stdout_max_bytes=1000, stderr_max_bytes=1000),
            execution_mode="pooled_worker",
            timeout_seconds=None,
        )

    assert response["success"] is True
    assert response["metadata"]["sandbox_runtime"] == "deno_pooled_worker"
    assert fake_pool.called is True
    one_shot_mock.assert_not_called()
    subprocess_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_python_uses_one_shot_worker_mode_when_requested() -> None:
    """验证请求 one_shot_worker 时使用 one-shot 预热池路径。"""
    settings = get_settings()
    fake_pool = FakePool("deno_prewarmed_worker")

    with patch.object(settings, "SANDBOX_TIMEOUT_SECONDS", 5), \
         patch.object(settings, "SANDBOX_PREWARM_POOL_ENABLED", True), \
         patch("app.services.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.services.python_sandbox.get_pooled_sandbox_pool") as pooled_mock, \
         patch("app.services.python_sandbox.get_prewarmed_sandbox_pool", return_value=fake_pool), \
         patch("app.services.python_sandbox.asyncio.create_subprocess_exec") as subprocess_mock:
        response = await execute_python_in_sandbox(
            "print(2 + 2)",
            SandboxLimits(stdout_max_bytes=1000, stderr_max_bytes=1000),
            execution_mode="one_shot_worker",
            timeout_seconds=None,
        )

    assert response["success"] is True
    assert response["metadata"]["sandbox_runtime"] == "deno_prewarmed_worker"
    assert fake_pool.called is True
    pooled_mock.assert_not_called()
    subprocess_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_python_pooled_worker_unavailable_does_not_fallback_to_subprocess() -> None:
    """验证 pooled_worker 不可用时不会自动启动一次性子进程。"""
    settings = get_settings()

    with patch.object(settings, "SANDBOX_TIMEOUT_SECONDS", 5), \
         patch("app.services.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.services.python_sandbox.get_pooled_sandbox_pool", return_value=UnavailablePool()), \
         patch("app.services.python_sandbox.asyncio.create_subprocess_exec") as subprocess_mock:
        response = await execute_python_in_sandbox(
            "print(2 + 2)",
            SandboxLimits(stdout_max_bytes=1000, stderr_max_bytes=1000),
            execution_mode="pooled_worker",
            timeout_seconds=None,
        )

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "worker_unavailable"
    assert response["metadata"]["sandbox_runtime"] == "deno_pooled_worker"
    subprocess_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_python_uses_subprocess_mode_when_requested() -> None:
    """验证只有显式 subprocess 模式才启动一次性子进程。"""
    settings = get_settings()
    fake_process = FakeSubprocess()

    with patch.object(settings, "SANDBOX_TIMEOUT_SECONDS", 5), \
         patch("app.services.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.services.python_sandbox.asyncio.create_subprocess_exec", return_value=fake_process) as subprocess_mock, \
         patch("app.services.python_sandbox.get_pooled_sandbox_pool") as pooled_mock, \
         patch("app.services.python_sandbox.get_prewarmed_sandbox_pool") as one_shot_mock:
        response = await execute_python_in_sandbox(
            "print(2 + 2)",
            SandboxLimits(stdout_max_bytes=1000, stderr_max_bytes=1000),
            execution_mode="subprocess",
            timeout_seconds=None,
        )

    assert response["success"] is True
    assert response["stdout"] == "4\n"
    subprocess_mock.assert_called_once()
    pooled_mock.assert_not_called()
    one_shot_mock.assert_not_called()
