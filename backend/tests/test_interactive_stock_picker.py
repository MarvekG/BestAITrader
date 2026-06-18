import uuid
from contextlib import nullcontext

import pytest
from langchain_core.messages import AIMessage

from app.ai.stock_picker.interactive_research import persistence as persistence_module
from app.ai.stock_picker.interactive_research import service as service_module
from app.ai.stock_picker.interactive_research import research_agent as research_agent_module
from app.ai.stock_picker.interactive_research.flow_control import FLOW_CONTROL_TOOL_NAME, control_research_flow
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
        return [*self.tools, control_research_flow]


class FakeInteractiveResearchLLM:
    """模拟支持 bind_tools 的 LLM。"""

    def __init__(self):
        """初始化 fake LLM。"""
        self.bound_tools = []
        self.research_calls = 0
        self.plan_calls = 0
        self.plan_message_counts = []
        self.ask_on_first_research = False
        self.asked = False
        self.invalid_final_response_once = False
        self.invalid_final_returned = False
        self.multiple_final_control_calls = False
        self.mixed_tool_and_flow_control = False
        self.never_uses_tools = False
        self.plan_uses_tool = False
        self.research_message_snapshots = []

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
        if "planning stage" in first_content or "规划阶段" in first_content:
            self.plan_calls += 1
            self.plan_message_counts.append(len(messages))
            if self.plan_uses_tool and self.plan_calls == 1:
                return AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "search_news",
                            "args": {"keyword": "AI hardware policy", "source": "interactive_research"},
                            "id": "plan-tool-call-1",
                        }
                    ],
                )
            return AIMessage(content="Plan updated: exclude banks and favor AI hardware.")

        self.research_calls += 1
        self.research_message_snapshots.append([str(getattr(message, "content", "") or "") for message in messages])
        if self.never_uses_tools and self.research_calls <= 10:
            return AIMessage(content="Still thinking without tool calls.")
        if self.ask_on_first_research and not self.asked:
            self.asked = True
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": FLOW_CONTROL_TOOL_NAME,
                        "args": {
                            "action": "ask",
                            "message": "Should the research focus only on semiconductor equipment?",
                        },
                        "id": "control-ask-1",
                    }
                ],
            )
        if self.mixed_tool_and_flow_control and self.research_calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_news",
                        "args": {"keyword": "semiconductor policy catalyst", "source": "interactive_research"},
                        "id": "tool-call-1",
                    },
                    {
                        "name": FLOW_CONTROL_TOOL_NAME,
                        "args": {
                            "action": "done",
                            "message": "## Final Research\nMixed turn flow control completed after tool execution.",
                        },
                        "id": "control-done-mixed",
                    },
                ],
            )
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
            return AIMessage(content="## Final Research\nRaw markdown answer without flow-control tool call.")
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "name": FLOW_CONTROL_TOOL_NAME,
                    "args": {
                        "action": "done",
                        "message": (
                            "## Final Research\n"
                            "Semiconductor equipment remains the focus based on tool evidence."
                        ),
                    },
                    "id": "control-done-1",
                },
                *(
                    [
                        {
                            "name": FLOW_CONTROL_TOOL_NAME,
                            "args": {
                                "action": "done",
                                "message": "## Final Research\nLast control decision should be used.",
                            },
                            "id": "control-done-2",
                        }
                    ]
                    if self.multiple_final_control_calls
                    else []
                ),
            ],
        )


class FakeBackgroundTasks:
    """记录 FastAPI BackgroundTasks 风格的后台任务。"""

    def __init__(self):
        """初始化空任务列表。"""
        self.tasks = []

    def add_task(self, func, *args, **kwargs):
        """记录待执行的后台任务。

        Args:
            func: 后台函数。
            *args: 位置参数。
            **kwargs: 关键字参数。
        """
        self.tasks.append((func, args, kwargs))

    async def run_all(self):
        """按加入顺序执行全部后台任务。"""
        for func, args, kwargs in self.tasks:
            result = func(*args, **kwargs)
            if hasattr(result, "__await__"):
                await result


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
    assert FLOW_CONTROL_TOOL_NAME in tool_names
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
        "max_iterations": 10,
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


@pytest.fixture(autouse=True)
def _interactive_research_session(db_session, monkeypatch):
    """让交互式研究 service 在测试中使用同一个 SQLite 会话。

    Args:
        db_session: SQLite 测试数据库会话。
        monkeypatch: pytest monkeypatch fixture。
    """
    monkeypatch.setattr(persistence_module, "SessionLocal", lambda: nullcontext(db_session))


async def _execute_background(monkeypatch, service, db_session, run_id):
    """通过真实后台入口驱动交互式研究 workflow。

    Args:
        monkeypatch: pytest monkeypatch fixture。
        service: 交互式研究服务。
        db_session: SQLite 测试数据库会话。
        run_id: 研究 run ID。
    """
    await service.execute_workflow_background(run_id, service._plan_agent.latest_plan_output(run_id))


async def _create_run_with_plan(service, user_id, request_data):
    """创建 run 并执行初始计划后台任务。

    Args:
        service: 交互式研究服务。
        user_id: 当前用户 ID。
        request_data: 创建 run 请求数据。

    Returns:
        已生成首轮 plan_card 的 run。
    """
    background_tasks = FakeBackgroundTasks()
    run = await service.create_run(user_id, request_data, background_tasks)
    await background_tasks.run_all()
    refreshed = service.get_run(run.run_id, user_id)
    return refreshed or run


@pytest.mark.asyncio
async def test_create_run_writes_user_message_plan_card_and_checkpoint(db_session):
    """创建 run 只写聊天消息和 checkpoint，不依赖 artifact。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()

    run = await _create_run_with_plan(service, user_id, _request_data())
    messages = service.get_messages(run.run_id, user_id)

    assert run.status == "awaiting_plan_approval"
    assert [message.message_type for message in messages] == ["user_input", "plan_card"]
    serialized_plan_message = service.serialize_message(messages[1])
    assert serialized_plan_message["display_type"] == "assistant"
    assert serialized_plan_message["execution_status"] == "completed"
    assert serialized_plan_message["markdown"] == "Plan updated: exclude banks and favor AI hardware."
    assert "Plan updated" in serialized_plan_message["markdown"]
    assert "当前完整 PLAN" not in serialized_plan_message["markdown"]
    assert "objective_summary" not in serialized_plan_message["markdown"]
    assert "open_questions" not in serialized_plan_message["markdown"]
    assert serialized_plan_message["payload"] == {"actions": ["approve", "cancel"]}
    assert "plan_payload" not in run.checkpoint_payload
    run.checkpoint_payload = {**run.checkpoint_payload, "llm_usage": None}
    assert service.serialize_run_summary(run)["llm_usage"] == {}
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
        "llm_usage",
        "cache_context_version",
        "version",
        "error_message",
        "created_at",
        "updated_at",
        "finished_at",
    }


@pytest.mark.asyncio
async def test_plan_agent_runs_in_background_after_create_and_user_input(db_session):
    """提交需求或补充要求时不等待计划 Agent，后台完成后再写入 plan_card。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    background_tasks = FakeBackgroundTasks()

    run = await service.create_run(user_id, _request_data(), background_tasks)
    messages = service.get_messages(run.run_id, user_id)

    assert [message.message_type for message in messages] == ["user_input"]
    assert fake_llm.plan_calls == 0
    assert len(background_tasks.tasks) == 1

    await background_tasks.run_all()
    messages = service.get_messages(run.run_id, user_id)
    assert [message.message_type for message in messages] == ["user_input", "plan_card"]
    assert fake_llm.plan_calls == 1

    revise_tasks = FakeBackgroundTasks()
    message = await service.append_user_message(
        run.run_id,
        user_id,
        "Exclude banks and favor AI hardware",
        background_tasks=revise_tasks,
    )
    messages = service.get_messages(run.run_id, user_id)
    assert message.status == "completed"
    assert [item.message_type for item in messages] == ["user_input", "plan_card", "user_input"]
    assert fake_llm.plan_calls == 1

    await revise_tasks.run_all()
    messages = service.get_messages(run.run_id, user_id)
    plan_cards = [item for item in messages if item.message_type == "plan_card"]
    assert len(plan_cards) == 2
    assert fake_llm.plan_calls == 2


@pytest.mark.asyncio
async def test_realtime_update_pushes_markdown_display_message(db_session, monkeypatch):
    """实时推送应携带三字段展示消息，顶层 message 使用 Markdown 正文。

    Args:
        db_session: SQLite 测试数据库会话。
        monkeypatch: pytest monkeypatch fixture。
    """
    from app.websocket import manager as websocket_manager_module

    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())
    plan_message = service.get_messages(run.run_id, user_id)[1]
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
    assert pushed_payload["message"] == "Plan updated: exclude banks and favor AI hardware."
    assert display_message == {
        "message_type": "assistant",
        "markdown": pushed_payload["message"],
        "execution_status": "completed",
    }


@pytest.mark.asyncio
async def test_plan_stage_user_input_iterates_plan_card(db_session):
    """计划确认阶段的普通聊天输入应迭代 plan，而不是走 revise action。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())

    revise_tasks = FakeBackgroundTasks()
    message = await service.append_user_message(
        run.run_id,
        user_id,
        "Exclude banks and favor AI hardware",
        background_tasks=revise_tasks,
    )
    await revise_tasks.run_all()
    messages = service.get_messages(run.run_id, user_id)
    plan_cards = [item for item in messages if item.message_type == "plan_card"]

    assert message.status == "completed"
    assert run.status == "awaiting_plan_approval"
    assert len(plan_cards) == 2
    assert fake_llm.plan_message_counts[-1] > fake_llm.plan_message_counts[0]
    assert "Plan updated" in plan_cards[-1].content
    assert "当前完整 PLAN" not in plan_cards[-1].content
    assert "user_inputs" not in plan_cards[-1].content
    assert "plan_payload" not in run.checkpoint_payload


@pytest.mark.asyncio
async def test_plan_agent_can_use_online_tool_before_writing_plan(db_session):
    """计划阶段允许先调用联网工具，再把工具结果纳入计划卡。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, fake_tool = _service_with_fake_runner()
    fake_llm.plan_uses_tool = True

    run = await _create_run_with_plan(service, user_id, _request_data())
    messages = service.get_messages(run.run_id, user_id)
    plan_card = messages[-1]

    assert fake_tool.calls == [{"keyword": "AI hardware policy", "source": "interactive_research"}]
    assert fake_llm.plan_calls == 2
    assert [tool.name for tool in fake_llm.bound_tools] == ["search_news"]
    assert _message_types(db_session, run.run_id) == [
        "user_input",
        "tool_start",
        "tool_result",
        "progress_update",
        "plan_card",
    ]
    assert plan_card.content == "Plan updated: exclude banks and favor AI hardware."


@pytest.mark.asyncio
async def test_research_agent_receives_planning_user_inputs_by_round(db_session, monkeypatch):
    """研究阶段输入应包含计划阶段多轮用户输入及轮次。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())
    revise_tasks = FakeBackgroundTasks()
    await service.append_user_message(
        run.run_id,
        user_id,
        "Second planning round: exclude banks",
        background_tasks=revise_tasks,
    )
    await revise_tasks.run_all()

    await service.process_action(run.run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    await _execute_background(monkeypatch, service, db_session, run.run_id)

    first_research_context = "\n".join(fake_llm.research_message_snapshots[0])
    assert "第 1 轮" in first_research_context
    assert "Find A-share opportunities with policy catalysts" in first_research_context
    assert "第 2 轮" in first_research_context
    assert "Second planning round: exclude banks" in first_research_context


def test_tool_result_success_false_marks_trace_failed():
    """工具返回 success=false 时 trace 不能被标记为成功。"""
    raw_result = {
        "message": "sync completed but returned False or None",
        "resolved_method": "fetch_and_ingest_stock_valuation",
        "success": False,
        "target": "600199.SH",
        "task_type": "valuation",
    }

    assert research_agent_module._is_successful_tool_result(raw_result, "") is False
    assert research_agent_module._is_successful_tool_result({"success": True}, "") is True


@pytest.mark.asyncio
async def test_approve_plan_runs_single_chat_workflow_and_writes_final_result(db_session, monkeypatch):
    """确认计划后执行单 Agent 工具循环并把结果写入消息流。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, fake_tool = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())
    run_id = run.run_id

    approved = await service.process_action(run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    assert approved.status == "researching"
    assert fake_tool.calls == []

    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(run_id, user_id)
    final_message = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="final_result")
        .one()
    )

    assert completed.status == "completed"
    assert [tool.name for tool in fake_llm.bound_tools] == ["search_news", FLOW_CONTROL_TOOL_NAME]
    assert fake_tool.calls[0]["keyword"] == "semiconductor policy catalyst"
    assert "tool_start" in _message_types(db_session, run_id)
    assert "tool_result" in _message_types(db_session, run_id)
    assert "Semiconductor equipment" in final_message.payload["answer_markdown"]


@pytest.mark.asyncio
async def test_invalid_flow_control_output_retries_before_completing(db_session, monkeypatch):
    """无工具调用且协议错误时应要求 LLM 重输，再按协议完成。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    fake_llm.invalid_final_response_once = True
    run = await _create_run_with_plan(service, user_id, _request_data())
    run_id = run.run_id

    await service.process_action(run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(run_id, user_id)
    final_message = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="final_result")
        .one()
    )

    assert completed.status == "completed"
    assert fake_llm.invalid_final_returned is True
    assert "Semiconductor equipment" in final_message.payload["answer_markdown"]


@pytest.mark.asyncio
async def test_multiple_flow_control_calls_use_last_decision(db_session, monkeypatch):
    """同轮多个流程控制工具调用时应使用最后一个决策。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    fake_llm.multiple_final_control_calls = True
    run = await _create_run_with_plan(service, user_id, _request_data())
    run_id = run.run_id

    await service.process_action(run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    await _execute_background(monkeypatch, service, db_session, run_id)
    final_message = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="final_result")
        .one()
    )

    assert "Last control decision should be used" in final_message.payload["answer_markdown"]


@pytest.mark.asyncio
async def test_mixed_evidence_and_flow_control_executes_tool_before_decision(db_session, monkeypatch):
    """同轮同时有证据工具和流程控制时，应先执行证据工具再应用流程决策。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, fake_tool = _service_with_fake_runner()
    fake_llm.mixed_tool_and_flow_control = True
    run = await _create_run_with_plan(service, user_id, _request_data())
    run_id = run.run_id

    await service.process_action(run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(run_id, user_id)
    final_message = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="final_result")
        .one()
    )

    assert completed.status == "completed"
    assert fake_tool.calls[0]["keyword"] == "semiconductor policy catalyst"
    assert "Mixed turn flow control completed" in final_message.payload["answer_markdown"]


@pytest.mark.asyncio
async def test_search_news_result_reuses_tool_output_summarizer(db_session, monkeypatch):
    """新闻搜索结果进入 agent 上下文前应复用现有工具输出压缩链路。"""
    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())
    run_id = run.run_id

    def fake_should_summarize(tool_name, content):
        """只对 search_news 触发压缩。"""
        return tool_name == "search_news" and "Semiconductor policy catalyst" in content

    async def fake_summarize_tool_output(*args, **kwargs):
        """返回可断言的压缩结果。"""
        return "[Structured Summary of search_news]:\ncompressed policy catalyst facts"

    monkeypatch.setattr(research_agent_module, "should_summarize_tool_output", fake_should_summarize)
    monkeypatch.setattr(research_agent_module, "summarize_tool_output", fake_summarize_tool_output)

    await service.process_action(run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    await _execute_background(monkeypatch, service, db_session, run_id)
    tool_result = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="tool_result")
        .one()
    )

    assert "compressed policy catalyst facts" in tool_result.content
    assert "compressed policy catalyst facts" in tool_result.payload["result_preview"]


@pytest.mark.asyncio
async def test_running_user_input_is_queued_and_processed_after_tool_step(db_session, monkeypatch):
    """运行中插入的用户输入先排队，下一轮工具安全点后并入上下文。"""
    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())
    run_id = run.run_id
    run.status = "researching"
    run.current_stage = "researching"
    db_session.commit()

    message = await service.append_user_message(
        run_id,
        user_id,
        "Also avoid crowded momentum names",
        background_tasks=FakeBackgroundTasks(),
    )
    message_id = message.message_id
    assert message.status == "queued"

    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(run_id, user_id)
    message = db_session.query(InteractiveResearchMessage).filter_by(message_id=message_id).one()

    assert message.status == "completed"
    assert completed.status == "completed"
    assert "system_status" in _message_types(db_session, run_id)


@pytest.mark.asyncio
async def test_research_iteration_budget_uses_frontend_max_iterations(db_session, monkeypatch):
    """研究阶段最大迭代次数应使用创建 run 时前端传入的 max_iterations。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    fake_llm.never_uses_tools = True
    run = await _create_run_with_plan(service, user_id, {**_request_data(), "max_iterations": 10})
    run_id = run.run_id

    assert run.checkpoint_payload["run_config"]["max_iterations"] == 10

    await service.process_action(run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    await _execute_background(monkeypatch, service, db_session, run_id)
    final_message = (
        db_session.query(InteractiveResearchMessage)
        .filter_by(run_id=run_id, message_type="final_result")
        .one()
    )

    assert fake_llm.research_calls == 11
    assert final_message.payload["iteration_budget"] == 10


@pytest.mark.asyncio
async def test_answer_pending_question_writes_parented_user_message_and_continues(db_session, monkeypatch):
    """awaiting_user_input 的回答应关联问题并继续执行 workflow。"""
    user_id = _create_user_id(db_session)
    service, fake_llm, _ = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())
    run_id = run.run_id
    fake_llm.ask_on_first_research = True

    approved = await service.process_action(run_id, user_id, "approve", background_tasks=FakeBackgroundTasks())
    assert approved.status == "researching"

    await _execute_background(monkeypatch, service, db_session, run_id)
    paused = service.get_run(run_id, user_id)
    assert paused.status == "awaiting_user_input"
    pending_message_id = paused.pending_message_id

    answer = await service.append_user_message(
        run_id,
        user_id,
        "Focus on semiconductor equipment only",
        background_tasks=FakeBackgroundTasks(),
    )
    paused = service.get_run(run_id, user_id)
    assert answer.parent_message_id == pending_message_id
    assert paused.status == "researching"

    await _execute_background(monkeypatch, service, db_session, run_id)
    completed = service.get_run(run_id, user_id)

    assert completed.status == "completed"
    assert completed.pending_message_id is None


@pytest.mark.asyncio
async def test_cancel_run_marks_terminal_without_artifacts(db_session):
    """cancel 立即终止 run 且只写系统消息。"""
    user_id = _create_user_id(db_session)
    service, _, _ = _service_with_fake_runner()
    run = await _create_run_with_plan(service, user_id, _request_data())

    cancelled = await service.process_action(
        run.run_id,
        user_id,
        "cancel",
        content="User stopped",
        background_tasks=FakeBackgroundTasks(),
    )

    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None
    assert "system_status" in _message_types(db_session, run.run_id)


def test_interactive_http_contract_is_chat_only(client, auth_headers, monkeypatch):
    """HTTP 契约只暴露 run、messages 和 actions，不再暴露 result/artifacts。"""
    fake_llm = FakeInteractiveResearchLLM()
    monkeypatch.setattr(service_module.interactive_research_service, "_llm_factory", lambda: fake_llm)
    monkeypatch.setattr(
        service_module.interactive_research_service._plan_agent, "_llm_factory", lambda: fake_llm
    )

    create_response = client.post(
        "/api/v1/ai-stock-picker/interactive/runs",
        json={"requirement": "Find resilient A-share policy catalyst opportunities"},
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["run"]["run_id"]

    list_response = client.get("/api/v1/ai-stock-picker/interactive/runs", headers=auth_headers)
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

    assert list_response.status_code == 200
    assert list_response.json()[0]["run_id"] == run_id
    assert "llm_usage" in list_response.json()[0]
    assert messages_response.status_code == 200
    assert result_response.status_code == 404
    assert artifacts_response.status_code == 404
    assert revise_response.status_code == 422


def test_delete_interactive_research_run_removes_messages_and_is_user_scoped(
    client,
    auth_headers,
    db_session,
    monkeypatch,
):
    """删除 Deep Research run 时应按用户隔离，并级联删除消息。"""
    fake_llm = FakeInteractiveResearchLLM()
    monkeypatch.setattr(service_module.interactive_research_service, "_llm_factory", lambda: fake_llm)
    monkeypatch.setattr(
        service_module.interactive_research_service._plan_agent, "_llm_factory", lambda: fake_llm
    )

    create_response = client.post(
        "/api/v1/ai-stock-picker/interactive/runs",
        json={"requirement": "Find resilient A-share policy catalyst opportunities"},
        headers=auth_headers,
    )
    assert create_response.status_code == 201
    run_id = create_response.json()["run"]["run_id"]

    other_username = f"interactive_other_{uuid.uuid4().hex[:8]}"
    create_user(
        db_session,
        UserCreate(
            username=other_username,
            email=f"{other_username}@example.com",
            password="password123",
        ),
    )
    login_response = client.post(
        "/api/v1/auth/login",
        data={"username": other_username, "password": "password123"},
    )
    other_headers = {"Authorization": f"Bearer {login_response.json()['access_token']}"}

    forbidden_response = client.delete(
        f"/api/v1/ai-stock-picker/interactive/runs/{run_id}",
        headers=other_headers,
    )
    delete_response = client.delete(
        f"/api/v1/ai-stock-picker/interactive/runs/{run_id}",
        headers=auth_headers,
    )
    missing_response = client.get(f"/api/v1/ai-stock-picker/interactive/runs/{run_id}", headers=auth_headers)
    messages_count = db_session.query(InteractiveResearchMessage).filter_by(run_id=uuid.UUID(run_id)).count()

    assert forbidden_response.status_code == 404
    assert delete_response.status_code == 200
    assert delete_response.json()["message"]
    assert missing_response.status_code == 404
    assert messages_count == 0
