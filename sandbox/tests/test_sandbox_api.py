from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app, lifespan


def test_health_returns_ok() -> None:
    """验证沙箱服务健康检查接口。"""
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_execute_delegates_to_sandbox_runner() -> None:
    """验证 HTTP 执行接口会调用服务内沙箱执行器。"""
    runner = AsyncMock(
        return_value={
            "success": True,
            "stdout": "4\n",
            "stderr": "",
            "error": None,
            "execution_time_ms": 6,
            "timed_out": False,
            "truncated": False,
            "metadata": {"sandbox_runtime": "deno_prewarmed_worker"},
        }
    )
    client = TestClient(app)

    with patch("app.main.execute_python_in_sandbox", runner):
        response = client.post(
            "/execute",
            json={
                "code": "print(2 + 2)",
                "execution_mode": "pooled_worker",
                "timeout_seconds": 30,
                "limits": {"stdout_max_bytes": 1000, "stderr_max_bytes": 500},
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["stdout"] == "4\n"
    runner.assert_awaited_once()
    _, kwargs = runner.call_args
    assert kwargs["code"] == "print(2 + 2)"
    assert kwargs["limits"].stdout_max_bytes == 1000
    assert kwargs["limits"].stderr_max_bytes == 500
    assert kwargs["execution_mode"] == "pooled_worker"
    assert kwargs["timeout_seconds"] == 30


def test_execute_requires_execution_mode() -> None:
    """验证 HTTP 执行接口要求调用方显式传入执行模式。"""
    client = TestClient(app)

    response = client.post(
        "/execute",
        json={
            "code": "print(2 + 2)",
            "timeout_seconds": 30,
            "limits": {"stdout_max_bytes": 1000, "stderr_max_bytes": 500},
        },
    )

    assert response.status_code == 422


def test_execute_accepts_subprocess_execution_mode() -> None:
    """验证 HTTP 执行接口允许显式 subprocess 模式。"""
    runner = AsyncMock(
        return_value={
            "success": True,
            "stdout": "4\n",
            "stderr": "",
            "error": None,
            "execution_time_ms": 6,
            "timed_out": False,
            "truncated": False,
            "metadata": {"sandbox_runtime": "deno"},
        }
    )
    client = TestClient(app)

    with patch("app.main.execute_python_in_sandbox", runner):
        response = client.post(
            "/execute",
            json={
                "code": "print(2 + 2)",
                "execution_mode": "subprocess",
                "timeout_seconds": 30,
                "limits": {"stdout_max_bytes": 1000, "stderr_max_bytes": 500},
            },
        )

    assert response.status_code == 200
    _, kwargs = runner.call_args
    assert kwargs["execution_mode"] == "subprocess"


@pytest.mark.asyncio
async def test_lifespan_prewarms_pooled_and_one_shot_pools() -> None:
    """验证启动阶段会同时预热 pooled 和 one-shot worker 池。"""
    settings = get_settings()
    pooled_pool = AsyncMock()
    one_shot_pool = AsyncMock()

    with patch.object(settings, "SANDBOX_PREWARM_ON_STARTUP", True), \
         patch.object(settings, "SANDBOX_EXECUTION_MODE", "pooled_worker"), \
         patch.object(settings, "SANDBOX_PREWARM_POOL_ENABLED", True), \
         patch("app.main.get_pooled_sandbox_pool", return_value=pooled_pool), \
         patch("app.main.get_prewarmed_sandbox_pool", return_value=one_shot_pool):
        async with lifespan(app):
            pass

    pooled_pool.prewarm.assert_awaited_once_with(settings.SANDBOX_STARTUP_PREWARM_WORKERS)
    one_shot_pool.prewarm.assert_awaited_once_with(settings.SANDBOX_STARTUP_PREWARM_WORKERS)
    pooled_pool.shutdown.assert_awaited_once()
    one_shot_pool.shutdown.assert_awaited_once()
