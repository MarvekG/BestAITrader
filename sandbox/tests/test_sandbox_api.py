from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


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
    assert kwargs["timeout_seconds"] == 30
