from __future__ import annotations

import re

import pytest

from app.core.request_context import get_request_id
from app.core.request_context import clear_request_id
from app.core.request_context import set_request_id
from app.ai.memory_client import MemoryServiceClient
from app.tasks.async_task_runner import AsyncTaskRunner
from app.tasks.process_executor import ProcessTaskExecutor


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
async def test_memory_client_forwards_request_id(monkeypatch) -> None:
    client = MemoryServiceClient()
    request_id = "00112233445566778899aabbccddeeff"
    captured_headers: dict[str, str] = {}

    class _FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"status": "ok"}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        async def post(self, url: str, json: dict[str, object], headers: dict[str, str]):
            del url, json
            captured_headers.update(headers)
            return _FakeResponse()

    token = set_request_id(request_id)
    monkeypatch.setattr("app.ai.memory_client.httpx.AsyncClient", _FakeAsyncClient)
    try:
        response = await client._post(
            "/v1/ingest",
            {"session": "user:7:general", "content": "note", "occurred_at": "2026-06-01T00:00:00Z"},
            operation="ingest",
        )
    finally:
        clear_request_id(token)

    assert response == {"status": "ok"}
    assert captured_headers["x-request-id"] == request_id


@pytest.mark.asyncio
async def test_async_task_runner_binds_request_id(monkeypatch) -> None:
    request_id = "ffeeddccbbaa00998877665544332211"
    observed: list[str | None] = []
    status_updates: list[str] = []

    class _FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            del exc_type, exc, tb

        def commit(self) -> None:
            return None

    class _FakeTaskManager:
        def update_task_status(self, db, task_id, status, result=None, error_message=None) -> None:
            del db, task_id, result, error_message
            status_updates.append(status)

    def _task() -> dict[str, str | None]:
        observed.append(get_request_id())
        return {"status": "ok"}

    monkeypatch.setattr("app.tasks.async_task_runner.SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr("app.tasks.async_task_runner.task_manager", _FakeTaskManager())

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


def test_process_executor_stays_available_as_optional_runner() -> None:
    executor = ProcessTaskExecutor()

    assert executor.get_active_task_count() == 0
