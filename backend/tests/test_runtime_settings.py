from uuid import uuid4

from app.models.async_task import AsyncTask
from app.models.session import Session
from app.models.system_setting import SystemSetting
from app.models.user import User


def test_runtime_settings_default_and_update(client, auth_headers, db_session) -> None:
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

    row = db_session.query(SystemSetting).filter(SystemSetting.key == "ai_debate.max_concurrent").one()
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


def test_debate_run_rejects_when_global_concurrency_limit_reached(client, auth_headers, db_session) -> None:
    """AI 投研辩论达到全局并发上限时拒绝新任务。"""
    client.put(
        "/api/v1/general/runtime-settings",
        headers=auth_headers,
        json={"ai_debate_max_concurrent": 1},
    )

    user = db_session.query(User).first()
    existing_session_id = uuid4()
    new_session_id = uuid4()
    db_session.add_all([
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
            parameters={"session_id": str(existing_session_id), "stock_code": "000001"},
            user_id=user.id,
        ),
    ])
    db_session.commit()

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

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert "1/1" in detail
