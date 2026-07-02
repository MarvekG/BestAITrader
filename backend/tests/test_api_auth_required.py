import logging
import uuid

import pytest
from fastapi.routing import APIRoute

from app.core.security import get_current_user
from app.main import app


PUBLIC_HTTP_PATHS = {
    "/health",
    "/api/v1/auth/register",
    "/api/v1/auth/login",
    "/api/v1/general/i18n/{lang}",
}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/sources/"),
        ("get", "/api/v1/sessions/"),
        ("get", "/api/v1/data/stocks/000001"),
        ("get", "/api/v1/prompt/"),
        ("get", "/api/v1/llm/health"),
        ("get", "/api/v1/tasks"),
        ("get", "/api/v1/testing/tools"),
        ("post", "/api/v1/stock-analysis/run"),
        ("post", "/api/v1/ai-stock-picker/interactive/runs"),
        ("get", "/api/v1/news-plugins"),
        ("get", "/api/v1/skills"),
        ("get", "/api/v1/mcp/servers"),
        ("get", "/api/v1/general/language"),
    ],
)
def test_business_api_requires_authentication(client, method, path):
    response = getattr(client, method)(path)

    assert response.status_code == 401


def test_route_table_requires_authentication_for_non_public_http_routes():
    missing_auth = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path in PUBLIC_HTTP_PATHS:
            continue

        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        if get_current_user not in dependencies:
            missing_auth.append(f"{','.join(sorted(route.methods or []))} {route.path}")

    assert missing_auth == []


def test_registration_is_disabled_and_login_remains_public(client, test_db, run_async, async_create_user):
    username = f"auth_bootstrap_{uuid.uuid4().hex[:8]}"
    password = "password123"

    register_response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": password,
        },
    )
    assert register_response.status_code == 403
    assert register_response.json()["detail"] == "User registration is disabled"

    async def _seed_user():
        async with test_db() as db:
            await async_create_user(db, username=username, email=f"{username}@example.com", password=password)

    run_async(_seed_user())

    login_response = client.post(
        "/api/v1/auth/login",
        data={
            "username": username,
            "password": password,
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()["token_type"] == "bearer"


def test_i18n_resource_endpoint_remains_public_for_login_screen(client):
    response = client.get("/api/v1/general/i18n/zh")

    assert response.status_code == 200
    assert "common" in response.json()


def test_root_requires_authentication_but_health_remains_public(client):
    root_response = client.get("/")
    health_response = client.get("/health")

    assert root_response.status_code == 401
    assert health_response.status_code == 200


def test_cors_does_not_allow_arbitrary_origins_by_default(client):
    response = client.get("/health", headers={"Origin": "https://attacker.example"})

    assert "access-control-allow-origin" not in response.headers


def test_access_log_uses_dedicated_logger_and_redacts_query_string(client, caplog):
    caplog.set_level(logging.INFO, logger="app.access")
    caplog.set_level(logging.INFO, logger="app.main")

    client.get("/health?token=secret-token&api_key=secret-key&safe=value")

    main_access_records = [
        record for record in caplog.records
        if record.name == "app.main" and record.getMessage().startswith("http request ")
    ]
    started_records = [
        record for record in caplog.records
        if record.name == "app.access" and record.getMessage().startswith("http request started")
    ]

    assert main_access_records == []
    assert started_records
    for record in started_records:
        assert "secret-token" not in record.getMessage()
        assert "secret-key" not in record.getMessage()
        assert "token=[REDACTED]" in record.getMessage()
        assert "api_key=[REDACTED]" in record.getMessage()
        assert "safe=value" in record.getMessage()
