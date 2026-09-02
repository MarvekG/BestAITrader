from __future__ import annotations

import re

import pytest

from app.core.request_context import get_request_id
from app.tasks.async_task_runner import AsyncTaskRunner


def test_backend_generates_uuid4hex_request_id_header(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    request_id = response.headers["x-request-id"]
    assert re.fullmatch(r"[0-9a-f]{32}", request_id)


def test_backend_preserves_request_id_header(client) -> None:
    request_id = "fedcba9876543210fedcba9876543210"

    response = client.get("/health", headers={"x-request-id": request_id})

    assert response.status_code == 200
    assert response.headers["x-request-id"] == request_id


@pytest.mark.asyncio
async def test_async_task_runner_binds_request_id(monkeypatch) -> None:
    request_id = "ffeeddccbbaa00998877665544332211"
    observed: list[str | None] = []
    status_updates: list[str] = []

    def _task() -> dict[str, str | None]:
        observed.append(get_request_id())
        return {"status": "ok"}

    async def _update_task_status(task_id, status, result=None, error_message=None) -> None:
        del task_id, result, error_message
        status_updates.append(status)

    monkeypatch.setattr("app.tasks.task_manager.task_manager.update_task_status", _update_task_status)

    runner = AsyncTaskRunner(max_concurrent_tasks=1)
    success = runner.submit_task(
        task_id="task-1",
        task_func=_task,
        request_id=request_id,
    )
    await runner.wait_for_all()

    assert success is True
    assert observed == [request_id]
    assert status_updates == ["running", "completed"]
    assert get_request_id() is None
