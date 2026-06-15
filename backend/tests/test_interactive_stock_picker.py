import uuid
from contextlib import nullcontext

import pytest
from langchain_core.messages import AIMessage

from app.ai.stock_picker.interactive_research import service as service_module
from app.ai.stock_picker.interactive_research import workflow as workflow_module
from app.ai.stock_picker.interactive_research.models import InteractiveResearchMessage
from app.ai.stock_picker.interactive_research.service import InteractiveResearchService
from app.crud.user import create_user
from app.schemas.user import UserCreate


class FakeInteractiveResearchTool:
    """提供最小 LangChain 工具接口的 fake 工具。"""

    def __init__(self, name, result):
        """初始化 fake 工具。

        Args:
            name: 工具名称。
            result: 工具返回值。
        """
        self.name = name
        self.result = result
        self.calls = []

    async def ainvoke(self, arguments):
        """模拟 LangChain 工具异步调用。

        Args:
            arguments: 工具参数。

        Returns:
            预设工具返回值。
        """
        self.calls.append(dict(arguments or {}))
        return self.result


class FakeInteractiveResearchToolRegistry:
    """提供 fake 非交易工具列表。"""

    def __init__(self, tools):
        """初始化 fake 工具注册表。

        Args:
            tools: fake 工具列表。
        """
        self.tools = tools

    async def aload_tools(self):
        """返回 fake 工具列表。

        Returns:
            fake 工具列表。
        """
        return self.tools


class FakeInteractiveResearchLLM:
    """模拟支持 bind_tools 的 LLM。"""

    def __init__(self):
        """初始化 fake LLM。"""
        self.bound_tools = []
        self.research_calls = 0
        self.plan_calls = 0
        self.ask_on_first_research = False
        self.asked = False
        self.invalid_final_response_once = False
        self.invalid_final_returned = False

    def bind_tools(self, tools):
        """记录绑定工具并返回自身。

        Args:
            tools: LangChain 工具列表。

        Returns:
            当前 fake LLM。
        """
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        """先返回工具调用，再返回最终结果。

        Args:
            messages: LangChain 消息上下文。

        Returns:
            AIMessage 响应。
        """
        first_content = str(getattr(messages[0], "content", "") or "") if messages else ""
        if "planning stage" in first_content:
            self.plan_calls += 1
            return AIMessage(content="ACTION: CONTINUE\nPlan updated: exclude banks and favor AI hardware.")

        self.research_calls += 1
        if self.ask_on_first_research and not self.asked:
            self.asked = True
            return AIMessage(content="ACTION: ASK\nShould the research focus only on semiconductor equipment?")
        if self.research_calls == 1 or (self.ask_on_first_research and self.research_calls == 2):
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_news",
                        "args": {"keyword": "semiconductor policy catalyst", "source": "interactive_research"},
                        "id": "tool-call-1",
                    }
                ],
            )
        if self.invalid_final_response_once and not self.invalid_final_returned:
            self.invalid_final_returned = True
            return AIMessage(content="## Final Research\nRaw markdown answer without flow-control action.")
        return AIMessage(
            content="ACTION: DONE\n## Final Research\nSemiconductor equipment remains the focus based on tool evidence."
        )


@pytest.mark.asyncio
async def test_interactive_tool_registry_reuses_agentic_tool_layers_and_filters_trading(monkeypatch):
    """工具 registry 应加载普通工具、Skill、MCP，并过滤交易工具。

    Args:
        monkeypatch: pytest monkeypatch fixture。
    """
    from app.ai.stock_picker.interactive_research import tool_registry as registry_module

    loaded_layers = []

    def fake_get_all_tools():
        """返回普通工具层 fake 工具。

        Returns:
            fake 工具列表。
        """
        loaded_layers.append("normal")
        return [
            FakeInteractiveResearchTool("normal_tool", "normal"),
            FakeInteractiveResearchTool("execute_trading_order", "trade"),
        ]

    def fake_get_skills_loader_tools():
        """返回 Skill 工具层 fake 工具。

        Returns:
            fake 工具列表。
        """
        loaded_layers.append("skill")
        return [FakeInteractiveResearchTool("skill_tool", "skill")]

    async def fake_get_mcp_tools():
        """返回 MCP 工具层 fake 工具。

        Returns:
            fake 工具列表。
        """
        loaded_layers.append("mcp")
        return [FakeInteractiveResearchTool("mcp_tool", "mcp")]

    monkeypatch.setattr(registry_module, "get_all_tools", fake_get_all_tools)
    monkeypatch.setattr(registry_module, "get_skills_loader_tools", fake_get_skills_loader_tools)
    monkeypatch.setattr(registry_module, "get_mcp_tools", fake_get_mcp_tools)

    registry = registry_module.InteractiveResearchToolRegistry(state={"user_id": 42})
    tool_names = [tool.name for tool in await registry.aload_tools()]

    assert "normal_tool" in tool_names
    assert "skill_tool" in tool_names
    assert "mcp_tool" in tool_names
    assert "execute_trading_order" not in tool_names
    assert loaded_layers == ["normal", "skill", "mcp"]


def _create_user_id(db_session) -> int:
    """创建测试用户并返回用户 ID。

    Args:
        db_session: SQLite 测试数据库会话。

    Returns:
        新建测试用户 ID。
    """
    username = f"interactive_{uuid.uuid4().hex[:8]}"
    user = create_user(
        db_session,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password="password123",
        ),
    )
    return user.id


def _request_data(**overrides):
    """生成交互式选股测试请求数据。

    Args:
        overrides: 需要覆盖的请求字段。

    Returns:
        请求数据字典。
    """
    data = {
        "requirement": "Find A-share opportunities with policy catalysts and controlled drawdown",
        "scope": "all",
        "research_depth": "standard",
        "expected_count": 2,
        "risk_level": "medium",
        "style": None,
        "allowed_industries": [],
        "excluded_industries": [],
        "exclude_recent_ipos": False,
        "min_listing_days": None,
    }
    data.update(overrides)
    return data


def _service_with_fake_runner():
    """构造注入 fake LLM 和 fake 工具的交互式研究服务。

    Returns:
        service、fake LLM 和 fake 工具三元组。
    """
    fake_tool = FakeInteractiveResearchTool(
        "search_news",
        [
            {
                "title": "Semiconductor policy catalyst continues",
                "summary": "Policy support remains active for advanced equipment localization.",
                "url": "https://example.com/policy",
                "source": "fake_news",
                "published_at": "2026-03-20",
            }
        ],
    )
    fake_llm = FakeInteractiveResearchLLM()

    def tool_loader_factory(state):
        """返回 fake 工具注册表。

        Args:
            state: 工具运行上下文。

        Returns:
            fake 工具注册表。
        """
        return FakeInteractiveResearchToolRegistry([fake_tool])

    return (
        InteractiveResearchService(tool_loader_factory=tool_loader_factory, llm_factory=lambda: fake_llm),
        fake_llm,
        fake_tool,
    )


def _message_types(db_session, run_id):
    """读取 run 的消息类型序列。

    Args:
        db_session: SQLite 测试数据库会话。
        run_id: 研究 run ID。

    Returns:
        按 sequence_no 排列的消息类型列表。
    """
    return [
        message.message_type
        for message in db_session.query(InteractiveResearchMessage)
        .filter(InteractiveResearchMessage.run_id == run_id)
        .order_by(InteractiveResearchMessage.sequence_no.asc())
        .all()
    ]


async def _execute_background(monkeypatch, service, db_session, run_id, plan_payload=None):
    """通过真实后台入口驱动交互式研究 workflow。

    Args:
        monkeypatch: pytest monkeypatch fixture。
        service: 交互式研究服务。
        db_session: SQLite 测试数据库会话。
        run_id: 研究 run ID。
        plan_payload: 可选计划 payload。
    """
    monkeypatch.setattr(service_module, "SessionLocal", lambda: nullcontext(db_session))
    monkeypatch.setattr(workflow_module, "SessionLocal", lambda: nullcontext(db_session))
    await service.execute_workflow_background(run_id, plan_payload)


def test_create_run_writes_user_message_plan_card_and_checkpoint(db_session):
    """创建 run 只写聊天消息和 checkpoint，不依赖 artifact。"""
    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()

    run = service.create_run(db_session, user_id, _request_data())
    messages = service.get_messages(db_session, run.run_id, user_id)

    assert run.status == "awaiting_plan_approval"
    assert [message.message_type for message in messages] == ["user_input", "plan_card"]
    serialized_plan_message = service.serialize_message(messages[1])
    assert serialized_plan_message["display_type"] == "assistant"
    assert serialized_plan_message["execution_status"] == "completed"
    assert serialized_plan_message["markdown"].startswith("### 研究计划")
    assert "- **股票来源**:" in serialized_plan_message["markdown"]
    assert run.checkpoint_payload["plan_payload"]["selection_mode"] == "llm_driven"
    assert run.checkpoint_payload["plan_payload"]["tool_policy"]["allowed_tools"] == "all_non_trading_agentic_tools"
    assert "trading" in run.checkpoint_payload["plan_payload"]["tool_policy"]["blocked_tools"]
    assert set(service.serialize_run_summary(run)) == {
        "run_id",
        "user_id",
        "status",
        "current_stage",
        "current_phase",
        "title",
        "raw_requirement",
        "pending_message_id",
        "checkpoint_payload",
        "cache_context_version",
        "version",
        "error_message",
        "created_at",
        "updated_at",
        "finished_at",
    }


@pytest.mark.asyncio
async def test_realtime_update_pushes_markdown_display_message(db_session, monkeypatch):
    """实时推送应携带三字段展示消息，顶层 message 使用 Markdown 正文。

    Args:
        db_session: SQLite 测试数据库会话。
        monkeypatch: pytest monkeypatch fixture。
    """
    from app.websocket import manager as websocket_manager_module

    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()
    run = service.create_run(db_session, user_id, _request_data())
    plan_message = service.get_messages(db_session, run.run_id, user_id)[1]
    pushed_payload = {}

    async def fake_send_stock_picker_update(**kwargs):
        """记录 WebSocket 推送参数。

        Args:
            kwargs: send_stock_picker_update 收到的关键字参数。
        """
        pushed_payload.update(kwargs)

    monkeypatch.setattr(
        websocket_manager_module.ws_manager,
        "send_stock_picker_update",
        fake_send_stock_picker_update,
    )

    await service._push_realtime_update(
        {
            "event": "plan_card",
            "run": service.serialize_run_summary(run),
            "message": service.serialize_message(plan_message),
            "message_text": plan_message.content,
        }
    )

    display_message = pushed_payload["payload"]["display_message"]
    assert pushed_payload["message"].startswith("### 研究计划")
    assert display_message == {
        "message_type": "assistant",
        "markdown": pushed_payload["message"],
        "execution_status": "completed",
    }


@pytest.mark.asyncio
async def test_plan_stage_user_input_iterates_plan_card(db_session):
    """计划确认阶段的普通聊天输入应迭代 plan，而不是走 revise action。"""
    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()
    run = service.create_run(db_session, user_id, _request_data())

    message = await service.append_user_message(db_session, run.run_id, user_id, "Exclude banks and favor AI hardware")
    messages = service.get_messages(db_session, run.run_id, user_id)
    plan_cards = [item for item in messages if item.message_type == "plan_card"]

    assert message.status == "completed"
    assert run.status == "awaiting_plan_approval"
    assert len(plan_cards) == 2
    assert run.checkpoint_payload["plan_payload"]["user_inputs"][-1]["content"] == "Exclude banks and favor AI hardware"


@pytest.mark.asyncio
async def test_approve_plan_runs_single_chat_workflow_and_writes_final_result(db_session, monkeypatch):
    """确认计划后执行单 Agent 工具循环并把结果写入消息流。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, fake_tool = _service_with_fake_runner()
    run = service.create_run(db_session, user_id, _request_data())
    run_id = run.run_id

    approved = await service.process_action(db_session, run_id, user_id, "approve")
    assert approved.status == "researching"
    assert fake_tool.calls == []

    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(db_session, run_id, user_id)
    final_message = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="final_result")
        .one()
    )

    assert completed.status == "completed"
    assert [tool.name for tool in fake_llm.bound_tools] == ["search_news"]
    assert fake_tool.calls[0]["keyword"] == "semiconductor policy catalyst"
    assert "tool_start" in _message_types(db_session, run_id)
    assert "tool_result" in _message_types(db_session, run_id)
    assert final_message.payload["selection_mode"] == "llm_driven"
    assert "Semiconductor equipment" in final_message.payload["answer_markdown"]


@pytest.mark.asyncio
async def test_invalid_flow_control_output_retries_before_completing(db_session, monkeypatch):
    """无工具调用且协议错误时应要求 LLM 重输，再按协议完成。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    fake_llm.invalid_final_response_once = True
    run = service.create_run(db_session, user_id, _request_data())
    run_id = run.run_id

    await service.process_action(db_session, run_id, user_id, "approve")
    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(db_session, run_id, user_id)
    final_message = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="final_result")
        .one()
    )

    assert completed.status == "completed"
    assert fake_llm.invalid_final_returned is True
    assert "Semiconductor equipment" in final_message.payload["answer_markdown"]


@pytest.mark.asyncio
async def test_running_user_input_is_queued_and_processed_after_tool_step(db_session, monkeypatch):
    """运行中插入的用户输入先排队，下一轮工具安全点后并入上下文。"""
    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()
    run = service.create_run(db_session, user_id, _request_data())
    run_id = run.run_id
    plan_payload = run.checkpoint_payload["plan_payload"]
    run.status = "researching"
    run.current_stage = "researching"
    db_session.commit()

    message = await service.append_user_message(db_session, run_id, user_id, "Also avoid crowded momentum names")
    message_id = message.message_id
    assert message.status == "queued"

    await _execute_background(monkeypatch, service, db_session, run_id, plan_payload=plan_payload)
    completed = service.get_run(db_session, run_id, user_id)
    message = db_session.query(InteractiveResearchMessage).filter_by(message_id=message_id).one()

    assert message.status == "completed"
    assert completed.status == "completed"
    assert "system_status" in _message_types(db_session, run_id)


@pytest.mark.asyncio
async def test_answer_pending_question_writes_parented_user_message_and_continues(db_session, monkeypatch):
    """awaiting_user_input 的回答应关联问题并继续执行 workflow。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    run = service.create_run(db_session, user_id, _request_data())
    run_id = run.run_id
    fake_llm.ask_on_first_research = True

    approved = await service.process_action(db_session, run_id, user_id, "approve")
    assert approved.status == "researching"

    await _execute_background(monkeypatch, service, db_session, run_id)
    paused = service.get_run(db_session, run_id, user_id)
    assert paused.status == "awaiting_user_input"
    pending_message_id = paused.pending_message_id

    answer = await service.append_user_message(db_session, run_id, user_id, "Focus on semiconductor equipment only")
    paused = service.get_run(db_session, run_id, user_id)
    assert answer.parent_message_id == pending_message_id
    assert paused.status == "researching"

    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(db_session, run_id, user_id)

    assert completed.status == "completed"
    assert completed.pending_message_id is None


@pytest.mark.asyncio
async def test_cancel_run_marks_terminal_without_artifacts(db_session):
    """cancel 立即终止 run 且只写系统消息。"""
    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()
    run = service.create_run(db_session, user_id, _request_data())

    cancelled = await service.process_action(db_session, run.run_id, user_id, "cancel", content="User stopped")

    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None
    assert "system_status" in _message_types(db_session, run.run_id)


def test_interactive_http_contract_is_chat_only(client, auth_headers):
    """HTTP 契约只暴露 run、messages 和 actions，不再暴露 result/artifacts。"""
    create_response = client.post(
        "/api/v1/ai-stock-picker/interactive/runs",
        json={"requirement": "Find resilient A-share policy catalyst opportunities"},
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["run"]["run_id"]

    messages_response = client.get(f"/api/v1/ai-stock-picker/interactive/runs/{run_id}/messages", headers=auth_headers)
    result_response = client.get(f"/api/v1/ai-stock-picker/interactive/runs/{run_id}/result", headers=auth_headers)
    artifacts_response = client.get(
        f"/api/v1/ai-stock-picker/interactive/runs/{run_id}/artifacts",
        headers=auth_headers,
    )
    revise_response = client.post(
        f"/api/v1/ai-stock-picker/interactive/runs/{run_id}/actions",
        json={"action": "revise", "content": "revise this"},
        headers=auth_headers,
    )

    assert messages_response.status_code == 200
    assert result_response.status_code == 404
    assert artifacts_response.status_code == 404
    assert revise_response.status_code == 422
