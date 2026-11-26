from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models.async_task import AsyncTask
from app.models.session import Session
from app.models.system_setting import SystemSetting
from app.models.user import User


@pytest.mark.asyncio
async def test_runtime_settings_default_and_update(client, auth_headers, async_db_session) -> None:
    """运行参数默认值可读取，并可保存到 system_settings。"""
    response = client.get("/api/v1/general/runtime-settings", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {"ai_debate_max_concurrent": 5}

    update_response = client.put(
        "/api/v1/general/runtime-settings",
        headers=auth_headers,
        json={"ai_debate_max_concurrent": 7},
    )

    assert update_response.status_code == 200
    assert update_response.json() == {"ai_debate_max_concurrent": 7}

    row = (
        await async_db_session.execute(select(SystemSetting).where(SystemSetting.key == "ai_debate.max_concurrent"))
    ).scalar_one()
    assert row.user_id is None
    assert row.value == 7


def test_runtime_settings_reject_invalid_concurrency(client, auth_headers) -> None:
    """运行参数拒绝非正数并发配置。"""
    response = client.put(
        "/api/v1/general/runtime-settings",
        headers=auth_headers,
        json={"ai_debate_max_concurrent": 0},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_debate_run_queues_when_running_limit_reached(client, auth_headers, async_db_session) -> None:
    """AI 投研辩论达到运行并发上限时，新任务进入 pending 队列而不是被拒绝。"""
    client.put(
        "/api/v1/general/runtime-settings",
        headers=auth_headers,
        json={"ai_debate_max_concurrent": 1},
    )

    user = (await async_db_session.execute(select(User))).scalars().first()
    existing_session_id = uuid4()
    new_session_id = uuid4()
    async_db_session.add_all([
        Session(
            session_id=existing_session_id,
            user_id=user.id,
            stock_code="000001",
            trading_frequency="daily",
            trading_strategy="value",
            status="active",
        ),
        Session(
            session_id=new_session_id,
            user_id=user.id,
            stock_code="000002",
            trading_frequency="daily",
            trading_strategy="value",
            status="active",
        ),
        AsyncTask(
            task_name="AI Analysis - 000001",
            task_type="ai_analysis",
            status="running",
            allow_concurrent=False,
            parameters={
                "session_id": str(existing_session_id),
                "stock_code": "000001",
                "trading_frequency": "daily",
                "trading_strategy": "value",
            },
            user_id=user.id,
        ),
    ])
    await async_db_session.commit()

    response = client.post(
        "/api/v1/debate/run",
        headers=auth_headers,
        json={
            "session_id": str(new_session_id),
            "stock_code": "000002",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["queued"] is True

    tasks = (
        await async_db_session.execute(
            select(AsyncTask).where(AsyncTask.task_type == "ai_analysis")
        )
    ).scalars().all()
    assert len(tasks) == 2
    queued_task = next(task for task in tasks if task.status == "pending")
    assert queued_task.parameters["stock_code"] == "000002"
    assert queued_task.parameters["session_id"] == str(new_session_id)


@pytest.mark.asyncio
async def test_runtime_settings_increase_dispatches_queued_tasks(
    client,
    auth_headers,
    async_db_session,
    monkeypatch,
) -> None:
    """提高运行并发上限后应立即调度排队中的任务。"""
    client.put(
        "/api/v1/general/runtime-settings",
        headers=auth_headers,
        json={"ai_debate_max_concurrent": 1},
    )

    user = (await async_db_session.execute(select(User))).scalars().first()
    running_session_id = uuid4()
    queued_session_id = uuid4()
    running_task = AsyncTask(
        task_name="AI Analysis - 000001",
        task_type="ai_analysis",
        status="running",
        allow_concurrent=False,
        parameters={
            "session_id": str(running_session_id),
            "stock_code": "000001",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
        user_id=user.id,
    )
    queued_task = AsyncTask(
        task_name="AI Analysis - 000002",
        task_type="ai_analysis",
        status="pending",
        allow_concurrent=False,
        parameters={
            "session_id": str(queued_session_id),
            "stock_code": "000002",
            "trading_frequency": "daily",
            "trading_strategy": "value",
        },
        user_id=user.id,
    )
    async_db_session.add_all([
        Session(
            session_id=running_session_id,
            user_id=user.id,
            stock_code="000001",
            trading_frequency="daily",
            trading_strategy="value",
            status="active",
        ),
        Session(
            session_id=queued_session_id,
            user_id=user.id,
            stock_code="000002",
            trading_frequency="daily",
            trading_strategy="value",
            status="active",
        ),
        running_task,
        queued_task,
    ])
    await async_db_session.commit()

    submitted: list[str] = []

    def _fake_submit_task(*, task_id, task_func, task_kwargs, respect_global_limit):
        assert respect_global_limit is False
        submitted.append(task_id)
        return True

    monkeypatch.setattr(
        "app.tasks.async_task_runner.async_task_runner.submit_task",
        _fake_submit_task,
    )

    response = client.put(
        "/api/v1/general/runtime-settings",
        headers=auth_headers,
        json={"ai_debate_max_concurrent": 2},
    )

    assert response.status_code == 200
    assert submitted == [queued_task.task_id]
