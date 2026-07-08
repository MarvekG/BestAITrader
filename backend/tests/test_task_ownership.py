import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.crud.user import create_user
from app.models.async_task import AsyncTask
from app.schemas.user import UserCreate


async def _create_authenticated_user(client, session_factory):
    username = f"task_owner_{uuid.uuid4().hex[:8]}"
    password = "password123"
    async with session_factory() as db:
        user = await create_user(
            db,
            UserCreate(username=username, email=f"{username}@example.com", password=password),
        )
        user_id = user.id
    response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    return user_id, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_task_list_returns_only_current_user_tasks(client, test_db):
    owner_id, owner_headers = await _create_authenticated_user(client, test_db)
    other_id, _ = await _create_authenticated_user(client, test_db)
    async with test_db() as db:
        db.add_all(
            [
                AsyncTask(
                    task_id="owner-task",
                    user_id=owner_id,
                    task_name="Owner Task",
                    task_type="db_sync",
                    status="completed",
                ),
                AsyncTask(
                    task_id="other-task",
                    user_id=other_id,
                    task_name="Other Task",
                    task_type="db_sync",
                    status="completed",
                ),
            ]
        )
        await db.commit()

    response = client.get("/api/v1/tasks", headers=owner_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["task_id"] for item in payload["items"]] == ["owner-task"]


@pytest.mark.asyncio
async def test_task_status_hides_tasks_owned_by_other_users(client, test_db):
    owner_id, owner_headers = await _create_authenticated_user(client, test_db)
    other_id, _ = await _create_authenticated_user(client, test_db)
    async with test_db() as db:
        db.add_all(
            [
                AsyncTask(
                    task_id="owner-task",
                    user_id=owner_id,
                    task_name="Owner Task",
                    task_type="db_sync",
                    status="completed",
                ),
                AsyncTask(
                    task_id="other-task",
                    user_id=other_id,
                    task_name="Other Task",
                    task_type="db_sync",
                    status="completed",
                ),
            ]
        )
        await db.commit()

    own_response = client.get("/api/v1/tasks/owner-task", headers=owner_headers)
    other_response = client.get("/api/v1/tasks/other-task", headers=owner_headers)

    assert own_response.status_code == 200
    assert own_response.json()["task_id"] == "owner-task"
    assert other_response.status_code == 404


@pytest.mark.asyncio
async def test_submitted_api_task_records_current_user(client, test_db):
    owner_id, owner_headers = await _create_authenticated_user(client, test_db)

    with patch("app.tasks.async_task_runner.async_task_runner.submit_task", return_value=True) as submit_mock:
        response = client.post("/api/v1/data/db/sync/stock-basic", headers=owner_headers)

    assert response.status_code == 200
    async with test_db() as db:
        task = (
            await db.execute(select(AsyncTask).where(AsyncTask.task_id == response.json()["task_id"]))
        ).scalar_one()
    assert task.user_id == owner_id

    submitted = submit_mock.call_args.kwargs
    with patch(
        "app.data.ingestors.manager.ingestor_manager.fetch_and_ingest_all_stock_basic",
        AsyncMock(return_value=True),
    ):
        result = await submitted["task_func"](**submitted["task_kwargs"])
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_task_status_update_notification_includes_owner_user(test_db, monkeypatch):
    async with test_db() as db:
        owner = await create_user(
            db,
            UserCreate(
                username="notification_owner",
                email="notification_owner@example.com",
                password="password123",
            ),
        )
        owner_id = owner.id
        db.add(
            AsyncTask(
                task_id="notification-owner-task",
                user_id=owner_id,
                task_name="Notification Owner Task",
                task_type="db_sync",
                status="pending",
            )
        )
        await db.commit()

    published = {}

    async def _capture_publish(channel, payload):
        published["channel"] = channel
        published["payload"] = json.loads(payload)
        return 1

    monkeypatch.setattr("app.core.redis_client.redis_client.publish", _capture_publish)

    from app.tasks.task_manager import task_manager

    await task_manager.update_task_status("notification-owner-task", "running")

    assert published["channel"] == "task_notifications"
    assert published["payload"]["task_id"] == "notification-owner-task"
    assert published["payload"]["user_id"] == owner_id


@pytest.mark.asyncio
async def test_delete_task_removes_only_current_user_task(client, test_db):
    owner_id, owner_headers = await _create_authenticated_user(client, test_db)
    other_id, _ = await _create_authenticated_user(client, test_db)
    async with test_db() as db:
        db.add_all(
            [
                AsyncTask(
                    task_id="owner-delete-task",
                    user_id=owner_id,
                    task_name="Owner Task",
                    task_type="stock_analysis",
                    status="completed",
                ),
                AsyncTask(
                    task_id="other-delete-task",
                    user_id=other_id,
                    task_name="Other Task",
                    task_type="stock_analysis",
                    status="completed",
                ),
            ]
        )
        await db.commit()

    own_response = client.delete("/api/v1/tasks/owner-delete-task", headers=owner_headers)
    other_response = client.delete("/api/v1/tasks/other-delete-task", headers=owner_headers)

    assert own_response.status_code == 204
    assert other_response.status_code == 404
    async with test_db() as db:
        owner_task = (
            await db.execute(select(AsyncTask).where(AsyncTask.task_id == "owner-delete-task"))
        ).scalar_one_or_none()
        other_task = (
            await db.execute(select(AsyncTask).where(AsyncTask.task_id == "other-delete-task"))
        ).scalar_one_or_none()
    assert owner_task is None
    assert other_task is not None


@pytest.mark.asyncio
async def test_clear_tasks_removes_only_current_user_matching_type(client, test_db):
    owner_id, owner_headers = await _create_authenticated_user(client, test_db)
    other_id, _ = await _create_authenticated_user(client, test_db)
    async with test_db() as db:
        db.add_all(
            [
                AsyncTask(
                    task_id="owner-stock-analysis-task",
                    user_id=owner_id,
                    task_name="Owner Stock Analysis Task",
                    task_type="stock_analysis",
                    status="completed",
                ),
                AsyncTask(
                    task_id="owner-db-sync-task",
                    user_id=owner_id,
                    task_name="Owner DB Sync Task",
                    task_type="db_sync",
                    status="completed",
                ),
                AsyncTask(
                    task_id="other-stock-analysis-task",
                    user_id=other_id,
                    task_name="Other Stock Analysis Task",
                    task_type="stock_analysis",
                    status="completed",
                ),
            ]
        )
        await db.commit()

    response = client.delete("/api/v1/tasks/clear?task_type=stock_analysis", headers=owner_headers)

    async with test_db() as db:
        remaining_task_ids = {task.task_id for task in (await db.execute(select(AsyncTask))).scalars().all()}
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1
    assert remaining_task_ids == {"owner-db-sync-task", "other-stock-analysis-task"}
