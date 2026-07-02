from __future__ import annotations

import asyncio
import threading
from functools import partial

import pytest

from app.core.request_context import get_request_id
from app.tasks.async_task_runner import AsyncTaskRunner


@pytest.mark.asyncio
async def test_async_task_runner_binds_request_id_and_updates_status(monkeypatch) -> None:
    observed_request_ids: list[str | None] = []
    status_updates: list[tuple[str, object, str | None]] = []

    async def _task() -> dict[str, str]:
        observed_request_ids.append(get_request_id())
        return {"status": "ok"}

    async def _record_status(*, task_id, status, result=None, error_message=None, notification_result=None):
        del task_id, notification_result
        status_updates.append((status, result, error_message))

    monkeypatch.setattr("app.tasks.task_manager.task_manager.update_task_status", _record_status)

    runner = AsyncTaskRunner(max_concurrent_tasks=1)

    success = runner.submit_task(
        task_id="task-1",
        task_func=_task,
        request_id="request-1",
    )
    await runner.wait_for_all()

    assert success is True
    assert observed_request_ids == ["request-1"]
    assert status_updates == [
        ("running", None, None),
        ("completed", {"status": "ok"}, None),
    ]


@pytest.mark.asyncio
async def test_async_task_runner_can_skip_status_persistence(monkeypatch) -> None:
    observed_request_ids: list[str | None] = []

    async def _task() -> dict[str, str]:
        observed_request_ids.append(get_request_id())
        return {"status": "ok"}

    runner = AsyncTaskRunner(max_concurrent_tasks=1)

    success = runner.submit_task(
        task_id="scheduler-task-1",
        task_func=_task,
        request_id="scheduler-request-1",
        persist_status=False,
    )
    await runner.wait_for_all()

    assert success is True
    assert observed_request_ids == ["scheduler-request-1"]


@pytest.mark.asyncio
async def test_async_task_runner_marks_soft_failure(monkeypatch) -> None:
    status_updates: list[tuple[str, object, str | None]] = []

    async def _task() -> dict[str, str]:
        return {"status": "failed", "error": "upstream failed"}

    async def _record_status(*, task_id, status, result=None, error_message=None, notification_result=None):
        del task_id, notification_result
        status_updates.append((status, result, error_message))

    monkeypatch.setattr("app.tasks.task_manager.task_manager.update_task_status", _record_status)

    runner = AsyncTaskRunner(max_concurrent_tasks=1)

    success = runner.submit_task(
        task_id="task-2",
        task_func=_task,
        request_id="request-2",
    )
    await runner.wait_for_all()

    assert success is True
    assert status_updates == [
        ("running", None, None),
        ("failed", {"status": "failed", "error": "upstream failed"}, "upstream failed"),
    ]


@pytest.mark.asyncio
async def test_async_task_runner_awaits_partial_async_task(monkeypatch) -> None:
    status_updates: list[tuple[str, object, str | None]] = []

    async def _task(value: str) -> dict[str, str]:
        return {"value": value}

    async def _record_status(*, task_id, status, result=None, error_message=None, notification_result=None):
        del task_id, notification_result
        status_updates.append((status, result, error_message))

    monkeypatch.setattr("app.tasks.task_manager.task_manager.update_task_status", _record_status)

    runner = AsyncTaskRunner(max_concurrent_tasks=1)

    success = runner.submit_task(
        task_id="task-3",
        task_func=partial(_task, "ok"),
        request_id="request-3",
    )
    await runner.wait_for_all()

    assert success is True
    assert status_updates == [
        ("running", None, None),
        ("completed", {"value": "ok"}, None),
    ]


@pytest.mark.asyncio
async def test_async_task_runner_stop_all_waits_for_cancelled_tasks(monkeypatch) -> None:
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def _task() -> dict[str, str]:
        try:
            entered.set()
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def _ignore_status(**kwargs):
        del kwargs

    monkeypatch.setattr("app.tasks.task_manager.task_manager.update_task_status", _ignore_status)

    runner = AsyncTaskRunner(max_concurrent_tasks=1)
    runner.submit_task(
        task_id="task-4",
        task_func=_task,
        request_id="request-4",
    )
    await entered.wait()

    await runner.stop_all()

    assert cancelled.is_set()
    assert runner.get_active_task_count() == 0


@pytest.mark.asyncio
async def test_async_task_runner_stop_all_waits_for_sync_task_thread(monkeypatch) -> None:
    entered = threading.Event()
    release = threading.Event()
    completed: list[bool] = []

    def _task() -> dict[str, str]:
        entered.set()
        release.wait(timeout=1)
        completed.append(True)
        return {"status": "ok"}

    async def _ignore_status(**kwargs):
        del kwargs

    monkeypatch.setattr("app.tasks.task_manager.task_manager.update_task_status", _ignore_status)

    runner = AsyncTaskRunner(max_concurrent_tasks=1)
    runner.submit_task(
        task_id="task-5",
        task_func=_task,
        request_id="request-5",
    )
    assert await asyncio.to_thread(entered.wait, 1)

    stop_task = asyncio.create_task(runner.stop_all())
    await asyncio.sleep(0.05)

    assert not stop_task.done()
    release.set()
    await stop_task

    assert completed == [True]
    assert runner.get_active_task_count() == 0
