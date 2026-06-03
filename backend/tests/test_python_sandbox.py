import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.agentic.tooling.python_sandbox import (
    _get_async_sandbox_limiter,
    execute_python_in_sandbox,
    validate_python_code,
)
from app.ai.agentic.tooling.python_sandbox_pool import PrewarmedSandboxPool
from app.ai.agentic.tools import execute_python_sandboxed, get_all_tools
from app.core.config import settings


def _skip_if_deno_unavailable(response):
    if response.get("metadata", {}).get("error_type") == "sandbox_boot_error" and "Deno executable not found" in str(
        response.get("error", "")
    ):
        pytest.skip(response["error"])


def test_validate_python_code_allows_numpy_and_pandas():
    code = """
import numpy as np
import pandas as pd
result = pd.DataFrame({"x": np.array([1, 2, 3])}).sum().to_dict()
"""
    validate_python_code(code)


def test_validate_python_code_allows_common_stdlib_imports():
    code = """
import json
import datetime
import asyncio
import signal
import resource
result = json.loads('{"day": 1}')["day"] + datetime.timedelta(days=1).days
"""
    validate_python_code(code)


def test_validate_python_code_allows_lambda_key_functions():
    code = """
result = sorted(
    [{"x": 2, "label": "b"}, {"x": 1, "label": "a"}],
    key=lambda row: row["x"],
)
"""
    validate_python_code(code)


def test_validate_python_code_allows_stateful_and_generator_syntax():
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
def test_validate_python_code_rejects_dangerous_patterns(code, error_fragment):
    with pytest.raises(Exception) as exc_info:
        validate_python_code(code)
    assert error_fragment in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_async_sandbox_limiter_initializes_lazily():
    with patch.object(settings, "PY_SANDBOX_MAX_CONCURRENT_EXECUTIONS", 1), \
         patch("app.ai.agentic.tooling.python_sandbox._ASYNC_SANDBOX_LIMITER", None):
        limiter = _get_async_sandbox_limiter()

    assert isinstance(limiter, asyncio.Semaphore)


def test_python_sandbox_prewarm_pool_defaults_follow_cpu_count():
    assert settings.PY_SANDBOX_PREWARM_POOL_ENABLED is True
    assert settings.PY_SANDBOX_PREWARM_ON_STARTUP is True
    assert settings.PY_SANDBOX_PREWARM_POOL_SIZE == max(1, (os.cpu_count() or 1) // 2)
    assert settings.PY_SANDBOX_PREWARM_MAX_STARTING == settings.PY_SANDBOX_PREWARM_POOL_SIZE
    assert settings.PY_SANDBOX_PREWARM_MIN_READY >= 1


def test_python_sandbox_worker_runner_path_exists():
    assert Path(settings.PY_SANDBOX_WORKER_RUNNER_PATH).exists()


class FakeStdout:
    def __init__(self, lines):
        self._lines = [line.encode("utf-8") for line in lines]

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class FakeProcess:
    def __init__(self, lines):
        self.stdout = FakeStdout(lines)


@pytest.mark.asyncio
async def test_prewarmed_pool_ready_reader_allows_only_pyodide_package_noise():
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
async def test_prewarmed_pool_ready_reader_logs_unknown_non_json_stdout():
    pool = PrewarmedSandboxPool()
    process = FakeProcess([
        "unexpected debug output\n",
        '{"type":"ready","worker_id":"worker-1"}\n',
    ])

    with patch("app.ai.agentic.tooling.python_sandbox_pool.logger") as logger_mock:
        ready = await pool._read_ready_message(process)

    assert ready["type"] == "ready"
    assert ready["worker_id"] == "worker-1"
    logger_mock.warning.assert_called_once()
    assert "unexpected debug output" == logger_mock.warning.call_args.kwargs["extra"]["worker_output"]


class FakePool:
    def __init__(self, response):
        self.response = response
        self.called = False

    async def execute(self, request_json, timeout_seconds):
        self.called = True
        assert "result = 40 + 2" in request_json
        return self.response


class TimeoutPool:
    async def execute(self, request_json, timeout_seconds):
        from app.ai.agentic.tooling.python_sandbox_pool import PrewarmedSandboxAcquireTimeout

        raise PrewarmedSandboxAcquireTimeout("empty")


class ExecutionTimeoutPool:
    async def execute(self, request_json, timeout_seconds):
        raise asyncio.TimeoutError


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_uses_prewarm_pool_when_enabled():
    fake_pool = FakePool({
        "success": True,
        "stdout": "",
        "stderr": "",
        "error": None,
        "execution_time_ms": 5,
        "timed_out": False,
        "truncated": False,
        "metadata": {"sandbox_runtime": "deno_prewarmed_worker"},
    })

    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", True), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.get_prewarmed_sandbox_pool", return_value=fake_pool), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec") as create_mock:
        response = await execute_python_in_sandbox("result = 40 + 2")

    assert response["success"] is True
    assert response["metadata"]["sandbox_runtime"] == "deno_prewarmed_worker"
    assert fake_pool.called is True
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_falls_back_when_prewarm_pool_empty():
    mocked_payload = {
        "success": True,
        "result": 42,
        "stdout": "ok",
        "stderr": "",
        "error": None,
        "execution_time_ms": 12,
        "timed_out": False,
        "truncated": False,
        "metadata": {"result_type": "int"},
    }
    process = AsyncMock()
    process.communicate.return_value = (json.dumps(mocked_payload).encode("utf-8"), b"")
    process.returncode = 0

    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", True), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.get_prewarmed_sandbox_pool", return_value=TimeoutPool()), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec", return_value=process) as create_mock:
        response = await execute_python_in_sandbox("result = 40 + 2")

    assert response["success"] is True
    assert response["metadata"]["result_type"] == "int"
    create_mock.assert_called_once()


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_does_not_fallback_after_worker_timeout():
    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", True), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.get_prewarmed_sandbox_pool", return_value=ExecutionTimeoutPool()), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec") as create_mock:
        response = await execute_python_in_sandbox("while True:\n    pass")

    assert response["success"] is False
    assert response["timed_out"] is True
    assert response["metadata"]["error_type"] == "timeout_error"
    assert response["metadata"]["sandbox_runtime"] == "deno_prewarmed_worker"
    create_mock.assert_not_called()


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_returns_validation_error():
    response = await execute_python_in_sandbox("import os\nresult = 1")

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "validation_error"
    assert "Import not allowed" in response["error"]


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_allows_common_stdlib_imports():
    mocked_payload = {
        "success": True,
        "result": 2,
        "stdout": "",
        "stderr": "",
        "error": None,
        "execution_time_ms": 12,
        "timed_out": False,
        "truncated": False,
        "metadata": {"result_type": "int"},
    }
    process = AsyncMock()
    process.communicate.return_value = (json.dumps(mocked_payload).encode("utf-8"), b"")
    process.returncode = 0

    code = """
import json
import datetime
result = json.loads('{"v": 1}')["v"] + datetime.timedelta(days=1).days
"""
    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", False), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec", return_value=process):
        response = await execute_python_in_sandbox(code)

    assert response["success"] is True
    assert "result" not in response
    assert response["metadata"]["result_type"] == "int"


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_allows_common_safe_builtins():
    mocked_payload = {
        "success": True,
        "result": True,
        "stdout": "",
        "stderr": "",
        "error": None,
        "execution_time_ms": 12,
        "timed_out": False,
        "truncated": False,
        "metadata": {"result_type": "bool"},
    }
    process = AsyncMock()
    process.communicate.return_value = (json.dumps(mocked_payload).encode("utf-8"), b"")
    process.returncode = 0

    code = """
import signal
result = hasattr(signal, "SIGINT") and isinstance(signal.SIGINT, int)
"""
    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", False), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec", return_value=process):
        response = await execute_python_in_sandbox(code)

    assert response["success"] is True
    assert "result" not in response
    assert response["metadata"]["result_type"] == "bool"


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_allows_lambda_expressions():
    mocked_payload = {
        "success": True,
        "result": [{"x": 1, "label": "a"}, {"x": 2, "label": "b"}],
        "stdout": "",
        "stderr": "",
        "error": None,
        "execution_time_ms": 12,
        "timed_out": False,
        "truncated": False,
        "metadata": {"result_type": "list", "item_count": 2},
    }
    process = AsyncMock()
    process.communicate.return_value = (json.dumps(mocked_payload).encode("utf-8"), b"")
    process.returncode = 0

    code = """
result = sorted(
    [{"x": 2, "label": "b"}, {"x": 1, "label": "a"}],
    key=lambda row: row["x"],
)
"""
    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", False), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec", return_value=process):
        response = await execute_python_in_sandbox(code)

    assert response["success"] is True
    assert "result" not in response
    assert response["metadata"]["item_count"] == 2


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_reports_missing_deno():
    with patch.object(settings, "PY_SANDBOX_DENO_EXECUTABLE", "deno-does-not-exist"):
        response = await execute_python_in_sandbox("result = 1 + 1")

    assert response["success"] is False
    assert response["metadata"]["error_type"] == "sandbox_boot_error"
    assert "deno-does-not-exist" in response["error"]


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_async_uses_subprocess_exec():
    mocked_payload = {
        "success": True,
        "result": 42,
        "stdout": "ok",
        "stderr": "",
        "error": None,
        "execution_time_ms": 12,
        "timed_out": False,
        "truncated": False,
        "metadata": {"result_type": "int"},
    }
    process = AsyncMock()
    process.communicate.return_value = (json.dumps(mocked_payload).encode("utf-8"), b"")
    process.returncode = 0

    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", False), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec", return_value=process) as create_mock:
        response = await execute_python_in_sandbox("result = 40 + 2")

    assert response["success"] is True
    assert "result" not in response
    assert response["metadata"]["result_type"] == "int"
    create_mock.assert_called_once()
    process.communicate.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_waits_for_async_concurrency_slot():
    mocked_payload = {
        "success": True,
        "result": 42,
        "stdout": "ok",
        "stderr": "",
        "error": None,
        "execution_time_ms": 12,
        "timed_out": False,
        "truncated": False,
        "metadata": {"result_type": "int"},
    }
    release_first = asyncio.Event()
    first_started = asyncio.Event()
    running_count = 0
    max_running_count = 0

    async def communicate(_request_json):
        nonlocal running_count, max_running_count
        running_count += 1
        max_running_count = max(max_running_count, running_count)
        first_started.set()
        try:
            await release_first.wait()
            return json.dumps(mocked_payload).encode("utf-8"), b""
        finally:
            running_count -= 1

    def create_process(*_args, **_kwargs):
        process = AsyncMock()
        process.communicate = AsyncMock(side_effect=communicate)
        process.returncode = 0
        return process

    limiter = asyncio.Semaphore(1)

    with patch.object(settings, "PY_SANDBOX_PREWARM_POOL_ENABLED", False), \
         patch("app.ai.agentic.tooling.python_sandbox._get_async_sandbox_limiter", return_value=limiter), \
         patch("app.ai.agentic.tooling.python_sandbox._resolve_executable", return_value="/usr/bin/deno"), \
         patch("app.ai.agentic.tooling.python_sandbox.asyncio.create_subprocess_exec", side_effect=create_process):
        first_task = asyncio.create_task(execute_python_in_sandbox("result = 1"))
        await first_started.wait()
        second_task = asyncio.create_task(execute_python_in_sandbox("result = 2"))
        await asyncio.sleep(0)

        assert max_running_count == 1
        assert second_task.done() is False

        release_first.set()
        first_response, second_response = await asyncio.gather(first_task, second_task)

    assert first_response["success"] is True
    assert second_response["success"] is True
    assert max_running_count == 1


@pytest.mark.asyncio
async def test_execute_python_sandboxed_tool_registered():
    tool_names = {tool.name for tool in get_all_tools()}

    assert "execute_python_sandboxed" in tool_names
    with patch("app.ai.agentic.tools.execute_python_in_sandbox", new=AsyncMock(return_value={"success": True, "stdout": "4"})):
        result = await execute_python_sandboxed.ainvoke({"code": "print(2 + 2)"})
    assert "success" in result
    assert "result" not in result


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_serializes_nested_pandas_timestamp_and_numpy():
    code = """
import pandas as pd
import numpy as np
import json

df = pd.DataFrame({
    "end_date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
    "value": np.array([1, 2], dtype=np.int64),
})

payload = {
    "latest": pd.Timestamp("2024-01-03 12:34:56"),
    "records": [
        {"end_date": row["end_date"].isoformat(), "value": int(row["value"])}
        for row in df.to_dict("records")
    ],
    "series": [item.isoformat() for item in df["end_date"]],
    "array": [float(item) for item in np.array([np.int64(1), np.float64(2.5)])],
}
print(json.dumps(payload, default=str))
"""
    response = await execute_python_in_sandbox(code)
    _skip_if_deno_unavailable(response)

    assert response["success"] is True
    assert "result" not in response
    payload = json.loads(response["stdout"])
    assert payload["latest"] == "2024-01-03 12:34:56"
    assert payload["records"][0]["end_date"] == "2024-01-01T00:00:00"
    assert payload["records"][0]["value"] == 1
    assert payload["series"][1] == "2024-01-02T00:00:00"
    assert payload["array"] == [1.0, 2.5]


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_runs_lambda_key_functions():
    code = """
result = sorted(
    [{"x": 2, "label": "b"}, {"x": 1, "label": "a"}],
    key=lambda row: row["x"],
)
print(",".join(row["label"] for row in result))
"""
    response = await execute_python_in_sandbox(code)
    _skip_if_deno_unavailable(response)

    assert response["success"] is True
    assert "result" not in response
    assert response["stdout"].strip() == "a,b"


@pytest.mark.asyncio
async def test_execute_python_in_sandbox_runs_stateful_and_generator_syntax():
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

try:
    tmp
    deleted = False
except Exception:
    deleted = True

def gen():
    yield 1
    yield from [2, 3]

result = {
    "value": outer(),
    "counter": counter,
    "deleted": deleted,
    "items": list(gen()),
}
print(result)
"""
    response = await execute_python_in_sandbox(code)
    _skip_if_deno_unavailable(response)

    assert response["success"] is True
    assert "result" not in response
    assert "'value': 3" in response["stdout"]
    assert "'counter': 3" in response["stdout"]
    assert "'deleted': True" in response["stdout"]
    assert "'items': [1, 2, 3]" in response["stdout"]
