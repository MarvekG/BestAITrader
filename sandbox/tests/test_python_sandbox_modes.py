from unittest.mock import patch

import pytest

from app.config import get_settings
from app.schemas import SandboxLimits
from app.services.python_sandbox import execute_python_in_sandbox


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


@pytest.mark.asyncio
async def test_execute_python_uses_pooled_worker_mode_by_default() -> None:
    """验证默认执行模式为持久 worker 池。"""
    settings = get_settings()
    fake_pool = FakePool("deno_pooled_worker")

    with patch.object(settings, "SANDBOX_EXECUTION_MODE", "pooled_worker"), \
         patch.object(settings, "SANDBOX_TIMEOUT_SECONDS", 5), \
         patch("app.services.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.services.python_sandbox.get_pooled_sandbox_pool", return_value=fake_pool), \
         patch("app.services.python_sandbox.get_prewarmed_sandbox_pool") as one_shot_mock, \
         patch("app.services.python_sandbox.asyncio.create_subprocess_exec") as subprocess_mock:
        response = await execute_python_in_sandbox(
            "print(2 + 2)",
            SandboxLimits(stdout_max_bytes=1000, stderr_max_bytes=1000),
            timeout_seconds=None,
        )

    assert response["success"] is True
    assert response["metadata"]["sandbox_runtime"] == "deno_pooled_worker"
    assert fake_pool.called is True
    one_shot_mock.assert_not_called()
    subprocess_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_python_uses_one_shot_worker_mode_when_configured() -> None:
    """验证配置为 one_shot_worker 时保留原 one-shot 预热池路径。"""
    settings = get_settings()
    fake_pool = FakePool("deno_prewarmed_worker")

    with patch.object(settings, "SANDBOX_EXECUTION_MODE", "one_shot_worker"), \
         patch.object(settings, "SANDBOX_TIMEOUT_SECONDS", 5), \
         patch.object(settings, "SANDBOX_PREWARM_POOL_ENABLED", True), \
         patch("app.services.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.services.python_sandbox.get_pooled_sandbox_pool") as pooled_mock, \
         patch("app.services.python_sandbox.get_prewarmed_sandbox_pool", return_value=fake_pool), \
         patch("app.services.python_sandbox.asyncio.create_subprocess_exec") as subprocess_mock:
        response = await execute_python_in_sandbox(
            "print(2 + 2)",
            SandboxLimits(stdout_max_bytes=1000, stderr_max_bytes=1000),
            timeout_seconds=None,
        )

    assert response["success"] is True
    assert response["metadata"]["sandbox_runtime"] == "deno_prewarmed_worker"
    assert fake_pool.called is True
    pooled_mock.assert_not_called()
    subprocess_mock.assert_not_called()
