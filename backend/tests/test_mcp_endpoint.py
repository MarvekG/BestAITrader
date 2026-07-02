import pytest
from sqlalchemy import select

from app.models.system_setting import SystemSetting


@pytest.mark.asyncio
async def test_mcp_server_crud_api(client, auth_headers, async_db_session):
    create_response = client.post(
        "/api/v1/mcp/servers",
        headers=auth_headers,
        json={
            "name": "公告检索",
            "enabled": True,
            "url": "http://127.0.0.1:8000/mcp",
            "token": "secret-token",
            "allowed_tools": ["search"],
        },
    )

    assert create_response.status_code == 200
    assert create_response.json()["status"] == "success"
    assert set(create_response.json()["server"]) == {"name", "enabled", "url", "allowed_tools"}
    assert "token" not in create_response.json()["server"]
    setting = (
        await async_db_session.execute(select(SystemSetting).where(SystemSetting.key == "mcp.servers"))
    ).scalar_one()
    assert setting.user_id is None
    assert any(item["name"] == "公告检索" and item["token"] == "secret-token" for item in setting.value["servers"])

    list_response = client.get("/api/v1/mcp/servers", headers=auth_headers)
    assert list_response.status_code == 200
    assert list_response.json()["count"] == 2

    prompt_response = client.get("/api/v1/mcp/prompt", headers=auth_headers)
    assert prompt_response.status_code == 200
    assert "公告检索" in prompt_response.json()["prompt"]

    update_response = client.put(
        "/api/v1/mcp/servers/%E5%85%AC%E5%91%8A%E6%A3%80%E7%B4%A2",
        headers=auth_headers,
        json={"enabled": False},
    )
    assert update_response.status_code == 200
    assert update_response.json()["server"]["enabled"] is False

    delete_response = client.delete("/api/v1/mcp/servers/%E5%85%AC%E5%91%8A%E6%A3%80%E7%B4%A2", headers=auth_headers)
    assert delete_response.status_code == 200
    assert delete_response.json() == {"status": "success", "name": "公告检索"}
