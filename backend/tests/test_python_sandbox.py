from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.ai.agentic.tooling.python_sandbox import (
    PY_SANDBOX_CODE_MAX_CHARS,
    execute_python_in_sandbox,
    validate_python_code,
)
from app.ai.agentic.tools import execute_python_sandboxed, get_all_tools
from app.core.config import settings


def test_validate_python_code_allows_numpy_and_pandas() -> None:
    """验证沙箱静态校验允许常用数据分析代码。"""
    code = """
import numpy as np
import pandas as pd
result = pd.DataFrame({"x": np.array([1, 2, 3])}).sum().to_dict()
"""
    validate_python_code(code)


def test_validate_python_code_allows_common_stdlib_imports() -> None:
    """验证沙箱静态校验允许常见无 I/O 标准库导入。"""
    code = """
import json
import datetime
import asyncio
import signal
import resource
result = json.loads('{"day": 1}')["day"] + datetime.timedelta(days=1).days
"""
    validate_python_code(code)


def test_validate_python_code_allows_stateful_and_generator_syntax() -> None:
    """验证沙箱静态校验允许普通函数、闭包和生成器语法。"""
    code = """
counter = 0

def outer():
    total = 1

    def inner():
        nonlocal total
        global counter
        total += 2
        counter = total
        return total

    return inner()

tmp = "remove"
del tmp

def gen():
    yield 1
    yield from [2, 3]

result = {
    "value": outer(),
    "counter": counter,
    "items": list(gen()),
}
"""
    validate_python_code(code)


@pytest.mark.parametrize(
    ("code", "error_fragment"),
    [
        ("import os\nresult = 1", "Import not allowed"),
        ("result = open('x')", "Call not allowed"),
        ("result = (1).__class__", "Attribute access not allowed"),
    ],
)
def test_validate_python_code_rejects_dangerous_patterns(code: str, error_fragment: str) -> None:
    """验证沙箱静态校验拒绝危险模块、函数和反射属性。"""
    with pytest.raises(Exception) as exc_info:
        validate_python_code(code)
    assert error_fragment in str(exc_info.value)


class FakeResponse:
    """测试用 HTTP 响应。"""

    def __init__(self, payload: object) -> None:
        """
        初始化固定 JSON 响应。

        Args:
            payload: `json()` 返回的响应体。
        """
        self._payload = payload

    def raise_for_status(self) -> None:
        """模拟成功响应状态检查。"""

    def json(self) -> object:
        """
        返回固定响应体。

        Returns:
            构造时传入的响应体。
        """
        return self._payload


class FakeAsyncClient:
    """测试用 httpx 异步客户端。"""

    instances: list["FakeAsyncClient"] = []
    response_payload: object = {
        "success": True,
        "stdout": "4\n",
        "stderr": "",
        "error": None,
        "execution_time_ms": 8,
        "timed_out": False,
        "truncated": False,
        "metadata": {"sandbox_runtime": "deno_prewarmed_worker"},
    }

    def __init__(self, base_url: str, timeout: httpx.Timeout) -> None:
        """
        记录客户端初始化参数。

        Args:
            base_url: 请求基础地址。
            timeout: 请求超时配置。
        """
        self.base_url = base_url
        self.timeout = timeout
        self.requests: list[tuple[str, object]] = []
        self.instances.append(self)

    async def __aenter__(self) -> "FakeAsyncClient":
        """
        进入异步上下文。

        Returns:
            当前客户端实例。
        """
        return self

    async def __aexit__(self, *_args: object) -> None:
        """退出异步上下文。"""

    async def post(self, path: str, json: object) -> FakeResponse:
        """
        记录 POST 请求并返回固定响应。

        Args:
            path: 请求路径。
            json: JSON 请求体。

        Returns:
            固定测试响应。
        """
        self.requests.append((path, json))
        return FakeResponse(self.response_payload)


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_calls_sandbox_service() -> None:
    """验证后端沙箱入口会转发到独立 sandbox HTTP 服务。"""
    FakeAsyncClient.instances.clear()
    with patch("app.ai.agentic.tooling.python_sandbox.httpx.AsyncClient", FakeAsyncClient), \
         patch.object(settings, "PY_SANDBOX_BASE_URL", "http://sandbox:8030"), \
         patch.object(settings, "PY_SANDBOX_EXECUTION_MODE", "pooled_worker"), \
         patch.object(settings, "PY_SANDBOX_TIMEOUT_SECONDS", 30):
        response = await execute_python_in_sandbox("print(2 + 2)")

    assert response["success"] is True
    assert response["stdout"] == "4\n"
    assert response["metadata"]["sandbox_runtime"] == "deno_prewarmed_worker"
    assert "result" not in response
    client = FakeAsyncClient.instances[0]
    assert client.base_url == "http://sandbox:8030"
    assert client.requests == [
        (
            "/execute",
            {
                "code": "print(2 + 2)",
                "execution_mode": "pooled_worker",
                "timeout_seconds": 30,
                "limits": {
                    "stdout_max_bytes": settings.PY_SANDBOX_STDOUT_MAX_BYTES,
                    "stderr_max_bytes": settings.PY_SANDBOX_STDERR_MAX_BYTES,
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_forwards_execution_mode() -> None:
    """验证后端沙箱入口会透传请求级执行模式。"""
    FakeAsyncClient.instances.clear()
    with patch("app.ai.agentic.tooling.python_sandbox.httpx.AsyncClient", FakeAsyncClient), \
         patch.object(settings, "PY_SANDBOX_BASE_URL", "http://sandbox:8030"), \
         patch.object(settings, "PY_SANDBOX_TIMEOUT_SECONDS", 30):
        await execute_python_in_sandbox("print(2 + 2)", execution_mode="subprocess")

    client = FakeAsyncClient.instances[0]
    assert client.requests[0][1]["execution_mode"] == "subprocess"


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_uses_configured_execution_mode() -> None:
    """验证后端沙箱入口默认使用配置中的执行模式。"""
    FakeAsyncClient.instances.clear()
    with patch("app.ai.agentic.tooling.python_sandbox.httpx.AsyncClient", FakeAsyncClient), \
         patch.object(settings, "PY_SANDBOX_BASE_URL", "http://sandbox:8030"), \
         patch.object(settings, "PY_SANDBOX_EXECUTION_MODE", "one_shot_worker"), \
         patch.object(settings, "PY_SANDBOX_TIMEOUT_SECONDS", 30):
        await execute_python_in_sandbox("print(2 + 2)")

    client = FakeAsyncClient.instances[0]
    assert client.requests[0][1]["execution_mode"] == "one_shot_worker"


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_returns_validation_error_without_http_call() -> None:
    """验证静态校验失败时不会调用独立 sandbox 服务。"""
    with patch("app.ai.agentic.tooling.python_sandbox.httpx.AsyncClient", side_effect=AssertionError("unexpected")):
        response = await execute_python_in_sandbox("import os\nresult = 1")

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "validation_error"
    assert "Import not allowed" in response["error"]


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_rejects_oversized_code_without_http_call() -> None:
    """超过 sandbox API code 上限时应本地拒绝，避免产生 HTTP 422。"""
    with patch("app.ai.agentic.tooling.python_sandbox.httpx.AsyncClient", side_effect=AssertionError("unexpected")):
        response = await execute_python_in_sandbox("x" * (PY_SANDBOX_CODE_MAX_CHARS + 1))

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "request_too_large"
    assert str(PY_SANDBOX_CODE_MAX_CHARS) in response["error"]


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_reports_disabled_sandbox() -> None:
    """验证配置关闭沙箱时返回稳定错误结构。"""
    with patch.object(settings, "PY_SANDBOX_ENABLED", False):
        response = await execute_python_in_sandbox("print(1)")

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "sandbox_disabled"


class FailingAsyncClient:
    """测试用失败 HTTP 客户端。"""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        """初始化失败客户端。"""

    async def __aenter__(self) -> "FailingAsyncClient":
        """
        进入异步上下文。

        Returns:
            当前客户端实例。
        """
        return self

    async def __aexit__(self, *_args: object) -> None:
        """退出异步上下文。"""

    async def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
        """
        模拟网络连接失败。

        Raises:
            httpx.ConnectError: 始终抛出连接失败。
        """
        request = httpx.Request("POST", "http://sandbox:8030/execute")
        raise httpx.ConnectError("connection failed", request=request)


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_reports_service_error() -> None:
    """验证独立 sandbox 服务不可用时返回可诊断错误。"""
    with patch("app.ai.agentic.tooling.python_sandbox.httpx.AsyncClient", FailingAsyncClient):
        response = await execute_python_in_sandbox("print(1)")

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "sandbox_service_error"
    assert "ConnectError" in response["error"]


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_reports_protocol_error() -> None:
    """验证独立 sandbox 服务返回非对象 JSON 时识别为协议错误。"""
    FakeAsyncClient.instances.clear()
    with patch.object(FakeAsyncClient, "response_payload", ["bad"]), \
         patch("app.ai.agentic.tooling.python_sandbox.httpx.AsyncClient", FakeAsyncClient):
        response = await execute_python_in_sandbox("print(1)")

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "protocol_error"


@pytest.mark.asyncio
async def test_execute_python_sandboxed_tool_registered() -> None:
    """验证 Agent 工具仍通过原名称暴露沙箱执行能力。"""
    tool_names = {tool.name for tool in get_all_tools()}

    assert "execute_python_sandboxed" in tool_names
    with patch("app.ai.agentic.tools.execute_python_in_sandbox", new=AsyncMock(return_value={"success": True, "stdout": "4"})):
        result = await execute_python_sandboxed.ainvoke({"code": "print(2 + 2)"})
    assert "success" in result
    assert "result" not in result
