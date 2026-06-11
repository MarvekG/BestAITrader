import pytest

from app.ai.agentic.mcp import registry as mcp_registry
from app.ai.agentic.mcp import runtime as mcp_runtime
from app.ai.agentic.mcp.models import MCPServerCreateRequest, MCPServerUpdateRequest
from app.ai.agentic.mcp.runtime import build_mcp_catalog_prompt


@pytest.fixture
def mcp_runtime_root(tmp_path, monkeypatch):
    root = tmp_path / "runtimes" / "mcp"
    monkeypatch.setattr(mcp_registry, "MCP_RUNTIME_ROOT", root)
    monkeypatch.setattr(mcp_registry, "MCP_SERVERS_FILE", root / "servers.json")
    return root


def test_default_mcp_server_is_available_when_config_file_missing(mcp_runtime_root):
    list_result = mcp_registry.list_mcp_servers()

    assert list_result["count"] == 1
    assert list_result["items"][0] == {
        "name": "网页抓取",
        "enabled": False,
        "url": "http://scrapling-mcp:8765/mcp",
    }
    assert not (mcp_runtime_root / "servers.json").exists()


def test_create_update_delete_mcp_server_config(mcp_runtime_root):
    create_result = mcp_registry.create_mcp_server(
        MCPServerCreateRequest(
            name="公告检索",
            url="http://127.0.0.1:8000/mcp",
        )
    )

    assert create_result["status"] == "success"
    assert set(create_result["server"]) == {"name", "enabled", "url"}
    assert (mcp_runtime_root / "servers.json").exists()

    list_result = mcp_registry.list_mcp_servers()
    assert list_result["count"] == 2
    assert any(item["name"] == "公告检索" for item in list_result["items"])

    update_result = mcp_registry.update_mcp_server(
        "公告检索",
        MCPServerUpdateRequest(enabled=True),
    )
    assert update_result["server"]["enabled"] is True

    delete_result = mcp_registry.delete_mcp_server("公告检索")
    assert delete_result == {"status": "success", "name": "公告检索"}
    assert [item["name"] for item in mcp_registry.list_mcp_servers()["items"]] == ["网页抓取"]


@pytest.mark.parametrize("url", ["", "ftp://example.com/mcp", "http:///missing-host"])
def test_mcp_url_rejects_unsafe_values(url):
    with pytest.raises(ValueError):
        mcp_registry.validate_mcp_url(url)


def test_build_mcp_catalog_prompt_lists_enabled_servers(mcp_runtime_root):
    mcp_registry.update_mcp_server(
        "网页抓取",
        MCPServerUpdateRequest(enabled=True),
    )

    prompt = build_mcp_catalog_prompt()

    assert "网页抓取" in prompt


@pytest.mark.asyncio
async def test_mcp_runtime_lists_invokes_and_filters_adapter_tools(monkeypatch, mcp_runtime_root):
    class FakeArgsSchema:
        @classmethod
        def model_json_schema(cls):
            return {"type": "object", "properties": {"text": {"type": "string"}}}

    class FakeTool:
        name = "fake__echo"
        description = "Echo input"
        args_schema = FakeArgsSchema

        async def ainvoke(self, arguments):
            return {"content": arguments["text"]}

    async def fake_get_tools(name):
        return [FakeTool()]

    mcp_registry.create_mcp_server(
        MCPServerCreateRequest(
            name="fake",
            enabled=True,
            url="http://127.0.0.1:8000/mcp",
        )
    )
    monkeypatch.setattr(mcp_runtime, "list_mcp_langchain_tools", fake_get_tools)

    bound_tools = await mcp_runtime.get_mcp_tools()
    tools = await mcp_runtime.list_mcp_tools("fake")
    result = await mcp_runtime.invoke_mcp_tool("fake", "echo", {"text": "hello"})

    assert bound_tools[0].name == "fake__echo"
    assert tools["items"][0]["name"] == "echo"
    assert tools["items"][0]["langchain_name"] == "fake__echo"
    assert result["result"] == {"content": "hello"}
