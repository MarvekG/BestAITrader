from unittest.mock import patch

import pytest

from app.services.python_sandbox_pool import PrewarmedSandboxPool


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
