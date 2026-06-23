import uuid
from unittest.mock import patch

from app.crud.user import create_user
from app.models.async_task import AsyncTask
from app.schemas.user import UserCreate


def _create_authenticated_user(client, db_session):
    username = f"task_owner_{uuid.uuid4().hex[:8]}"
    password = "password123"
    user = create_user(
        db_session,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password=password,
        ),
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    token = response.json()["access_token"]
    return user, {"Authorization": f"Bearer {token}"}


def test_task_list_returns_only_current_user_tasks(client, db_session):
    owner, owner_headers = _create_authenticated_user(client, db_session)
    other, _ = _create_authenticated_user(client, db_session)
    db_session.add_all(
        [
            AsyncTask(
                task_id="owner-task",
                user_id=owner.id,
                task_name="Owner Task",
                task_type="db_sync",
                status="completed",
            ),
            AsyncTask(
                task_id="other-task",
                user_id=other.id,
                task_name="Other Task",
                task_type="db_sync",
                status="completed",
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/tasks", headers=owner_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert [item["task_id"] for item in payload["items"]] == ["owner-task"]


def test_task_status_hides_tasks_owned_by_other_users(client, db_session):
    owner, owner_headers = _create_authenticated_user(client, db_session)
    other, _ = _create_authenticated_user(client, db_session)
    db_session.add_all(
        [
            AsyncTask(
                task_id="owner-task",
                user_id=owner.id,
                task_name="Owner Task",
                task_type="db_sync",
                status="completed",
            ),
            AsyncTask(
                task_id="other-task",
                user_id=other.id,
                task_name="Other Task",
                task_type="db_sync",
                status="completed",
            ),
        ]
    )
    db_session.commit()

    own_response = client.get("/api/v1/tasks/owner-task", headers=owner_headers)
    other_response = client.get("/api/v1/tasks/other-task", headers=owner_headers)

    assert own_response.status_code == 200
    assert own_response.json()["task_id"] == "owner-task"
    assert other_response.status_code == 404


def test_submitted_api_task_records_current_user(client, db_session):
    owner, owner_headers = _create_authenticated_user(client, db_session)

    with patch("app.tasks.async_task_runner.async_task_runner.submit_task", return_value=True):
        response = client.post("/api/v1/data/db/sync/stock-basic", headers=owner_headers)

    assert response.status_code == 200
    task = db_session.query(AsyncTask).filter(AsyncTask.task_id == response.json()["task_id"]).one()
    assert task.user_id == owner.id


def test_delete_task_removes_only_current_user_task(client, db_session):
    owner, owner_headers = _create_authenticated_user(client, db_session)
    other, _ = _create_authenticated_user(client, db_session)
    db_session.add_all(
        [
            AsyncTask(
                task_id="owner-delete-task",
                user_id=owner.id,
                task_name="Owner Task",
                task_type="stock_analysis",
                status="completed",
            ),
            AsyncTask(
                task_id="other-delete-task",
                user_id=other.id,
                task_name="Other Task",
                task_type="stock_analysis",
                status="completed",
            ),
        ]
    )
    db_session.commit()

    own_response = client.delete("/api/v1/tasks/owner-delete-task", headers=owner_headers)
    other_response = client.delete("/api/v1/tasks/other-delete-task", headers=owner_headers)

    assert own_response.status_code == 204
    assert other_response.status_code == 404
    assert db_session.query(AsyncTask).filter(AsyncTask.task_id == "owner-delete-task").first() is None
    assert db_session.query(AsyncTask).filter(AsyncTask.task_id == "other-delete-task").first() is not None


def test_clear_tasks_removes_only_current_user_matching_type(client, db_session):
    owner, owner_headers = _create_authenticated_user(client, db_session)
    other, _ = _create_authenticated_user(client, db_session)
    db_session.add_all(
        [
            AsyncTask(
                task_id="owner-stock-analysis-task",
                user_id=owner.id,
                task_name="Owner Stock Analysis Task",
                task_type="stock_analysis",
                status="completed",
            ),
            AsyncTask(
                task_id="owner-db-sync-task",
                user_id=owner.id,
                task_name="Owner DB Sync Task",
                task_type="db_sync",
                status="completed",
            ),
            AsyncTask(
                task_id="other-stock-analysis-task",
                user_id=other.id,
                task_name="Other Stock Analysis Task",
                task_type="stock_analysis",
                status="completed",
            ),
        ]
    )
    db_session.commit()

    response = client.delete("/api/v1/tasks/clear?task_type=stock_analysis", headers=owner_headers)

    remaining_task_ids = {task.task_id for task in db_session.query(AsyncTask).all()}
    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1
    assert remaining_task_ids == {"owner-db-sync-task", "other-stock-analysis-task"}
