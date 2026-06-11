import pytest

from app.ai.agentic.mcp import registry as mcp_registry
from app.ai.agentic.mcp import runtime as mcp_runtime
from app.ai.agentic.mcp.models import MCPServerConfig, MCPServerCreateRequest, MCPServerUpdateRequest
from app.ai.agentic.mcp.runtime import build_mcp_catalog_prompt


def test_default_mcp_server_is_available_when_system_config_missing(db_session):
    list_result = mcp_registry.list_mcp_servers()

    assert list_result["count"] == 1
    assert list_result["items"][0] == {
        "name": "网页抓取",
        "enabled": False,
        "url": "http://scrapling-mcp:8765/mcp",
        "allowed_tools": [],
    }


def test_create_update_delete_mcp_server_config(db_session):
    create_result = mcp_registry.create_mcp_server(
        MCPServerCreateRequest(
            name="公告检索",
            url="http://127.0.0.1:8000/mcp",
            token="secret-token",
            allowed_tools=["search"],
        )
    )

    assert create_result["status"] == "success"
    assert set(create_result["server"]) == {"name", "enabled", "url", "allowed_tools"}
    assert "token" not in create_result["server"]

    stored_config = mcp_registry.get_mcp_server_config("公告检索")
    assert stored_config.token == "secret-token"

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


def test_build_mcp_catalog_prompt_lists_enabled_servers(db_session):
    mcp_registry.update_mcp_server(
        "网页抓取",
        MCPServerUpdateRequest(enabled=True),
    )

    prompt = build_mcp_catalog_prompt()

    assert "网页抓取" in prompt


@pytest.mark.asyncio
async def test_mcp_runtime_lists_invokes_and_filters_adapter_tools(monkeypatch, db_session):
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
            allowed_tools=["echo"],
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


def test_mcp_adapter_config_includes_token_and_filters_allowed_tools():
    class FakeTool:
        def __init__(self, name):
            self.name = name

    config = MCPServerConfig(
        name="网页抓取",
        url="http://127.0.0.1:8000/mcp",
        token="secret-token",
        allowed_tools=["echo"],
    )

    adapter_config = mcp_runtime.build_adapter_config(config)
    filtered_tools = mcp_runtime.filter_allowed_tools(config, [FakeTool("网页抓取__echo"), FakeTool("网页抓取__other")])

    assert adapter_config["headers"] == {"Authorization": "Bearer secret-token"}
    assert [tool.name for tool in filtered_tools] == ["网页抓取__echo"]


def test_tool_to_item_accepts_dict_args_schema():
    class FakeTool:
        name = "server__search"
        description = "Search tool"
        args_schema = {"type": "object", "properties": {"query": {"type": "string"}}}

    item = mcp_runtime.tool_to_item("server", FakeTool())

    assert item["name"] == "search"
    assert item["input_schema"] == {"type": "object", "properties": {"query": {"type": "string"}}}
