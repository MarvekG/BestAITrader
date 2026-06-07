from importlib import reload
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from app.api.endpoints import llm as llm_endpoint
from app.api.endpoints.llm import _get_ai_function_test_tools, run_llm_probe


class _FakeOpenAIMessage:
    content = "ok"


class _FakeOpenAIChoice:
    message = _FakeOpenAIMessage()


class _FakeOpenAIResponse:
    choices = [_FakeOpenAIChoice()]


class _FakeTestingLLM:
    def __init__(self, model):
        self.model = model
        self.bound_tool_names: set[str] = set()

    def bind_tools(self, bound_tools):
        next_llm = _FakeTestingLLM(self.model)
        next_llm.bound_tool_names = {tool.name for tool in bound_tools}
        return next_llm

    async def ainvoke(self, messages):
        content = "\n".join(str(getattr(message, "content", "")) for message in messages)
        if any(message.__class__.__name__ == "ToolMessage" for message in messages):
            return AIMessage(content="tool final ok")

        if (
            "list_skills" in self.bound_tool_names
            and "execute_python_sandboxed" in self.bound_tool_names
            and "at least one bound real AI tool" in content
        ):
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "list_skills", "args": {}, "id": "skills-tool-1", "type": "tool_call"},
                    {
                        "name": "browse_web_page_html",
                        "args": {"url": "file:///etc/passwd", "wait_after_ms": 0},
                        "id": "browser-tool-1",
                        "type": "tool_call",
                    },
                ],
            )
        if "list_skills" in self.bound_tool_names and "list_skills" in content:
            return AIMessage(
                content="",
                tool_calls=[{"name": "list_skills", "args": {}, "id": "skills-tool-1", "type": "tool_call"}],
            )
        if "execute_python_sandboxed" in self.bound_tool_names:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "execute_python_sandboxed",
                        "args": {"code": "result = 'tool-ok'"},
                        "id": "python-tool-1",
                        "type": "tool_call",
                    }
                ],
            )

        if self.model == "backend-thinking":
            return AIMessage(content="", additional_kwargs={"reasoning_content": "fake reasoning"})
        additional_kwargs = {}
        return AIMessage(content=f"{self.model} ok", additional_kwargs=additional_kwargs)


@pytest.mark.asyncio
async def test_llm_probe_runs_all_probe_steps():
    build_calls = []

    def _fake_build_chat_model(**kwargs):
        build_calls.append(kwargs)
        return _FakeTestingLLM(kwargs.get("model"))

    with (
        patch("app.api.endpoints.llm.build_chat_model", side_effect=_fake_build_chat_model),
        patch("app.api.endpoints.llm.settings.LLM_MODEL", "backend"),
        patch("app.api.endpoints.llm.settings.LLM_THINKING_MODEL", "backend-thinking"),
    ):
        result = await run_llm_probe()

    assert result["status"] == "success"
    assert "input" not in result
    assert set(result["checks"]) == {"thinking_mode", "non_thinking_mode", "tool_call", "skills_call"}
    assert result["checks"]["thinking_mode"]["has_reasoning_content"] is True
    assert result["checks"]["thinking_mode"]["reasoning_content_preview"] == "fake reasoning"
    assert result["checks"]["non_thinking_mode"]["model"] == "backend"
    assert result["checks"]["tool_call"]["tool_calls"][0]["name"] == "execute_python_sandboxed"
    assert result["checks"]["skills_call"]["tool_calls"][0]["name"] == "list_skills"
    assert all(call.get("extra_body") is None for call in build_calls)
    assert all(call.get("temperature") == 1 for call in build_calls)
    assert [call.get("model") for call in build_calls[:2]] == ["backend-thinking", "backend"]
    thinking_call = next(call for call in build_calls if call.get("model") == "backend-thinking")
    assert thinking_call["max_tokens"] == 512


def test_ai_function_test_tools_are_real_tools_and_skills():
    tool_names = {tool_obj.name for tool_obj in _get_ai_function_test_tools("tools_and_skills")}

    assert "execute_python_sandboxed" in tool_names
    assert "browse_web_page_html" in tool_names
    assert "list_skills" in tool_names
    assert "llm_probe_echo" not in tool_names


@pytest.mark.asyncio
async def test_llm_chat_completion_helper_uses_configured_timeout_and_retries():
    captured = {}
    patched_request_llm_completion = llm_endpoint._request_llm_completion
    reloaded_llm_endpoint = reload(llm_endpoint)

    class _FakeHttpClient:
        def __init__(self, *, timeout):
            captured["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    class _FakeCompletions:
        async def create(self, **kwargs):
            captured["request_kwargs"] = kwargs
            return _FakeOpenAIResponse()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = _FakeChat()

    try:
        with (
            patch("app.api.endpoints.llm.httpx.AsyncClient", _FakeHttpClient),
            patch("app.api.endpoints.llm.AsyncOpenAI", _FakeAsyncOpenAI),
            patch("app.api.endpoints.llm.record_llm_usage"),
        ):
            result = await reloaded_llm_endpoint._request_llm_completion(
                messages=[{"role": "user", "content": "hello"}],
                model="backend",
            )
    finally:
        llm_endpoint._request_llm_completion = patched_request_llm_completion

    assert result["content"] == "ok"
    assert captured["timeout"] == 240.0
    assert captured["client_kwargs"]["max_retries"] == 3


@pytest.mark.asyncio
async def test_ai_function_test_executes_real_tool_and_skills_loader():
    def _fake_build_chat_model(**kwargs):
        return _FakeTestingLLM(kwargs.get("model"))

    with patch("app.api.endpoints.llm.build_chat_model", side_effect=_fake_build_chat_model):
        result = await llm_endpoint.execute_ai_function_test(
            scenario="tools_and_skills",
            user_input="先列出 skills，再用真实 AI 工具计算 17 * 23。",
        )

    tool_result_names = [item["name"] for item in result["output"]["tool_results"]]
    assert result["status"] == "success"
    assert result["input"]["expected_requirements"] == ["at least one real AI tool", "list_skills"]
    assert "list_skills" in tool_result_names
    assert "browse_web_page_html" in tool_result_names
    assert "llm_probe_echo" not in result["input"]["bound_tools"]


@pytest.mark.asyncio
async def test_ai_function_test_endpoint_submits_background_task():
    class FakeBackgroundTasks:
        def __init__(self):
            self.tasks = []

        def add_task(self, func, *args, **kwargs):
            self.tasks.append((func, args, kwargs))

    submitted = {}

    def _fake_submit_task(**kwargs):
        submitted.update(kwargs)
        return {
            "task_id": "task-123",
            "task_name": "AI Function Test - Thinking Tools",
            "status": "pending",
            "message": "submitted",
            "new_task": True,
        }

    background_tasks = FakeBackgroundTasks()
    with patch("app.api.endpoints.llm.task_manager.submit_task", side_effect=_fake_submit_task):
        result = await llm_endpoint.run_ai_function_test(
            {"scenario": "thinking_tools", "user_input": "查官网年报"},
            background_tasks,
            db=object(),
            current_user=SimpleNamespace(id=42),
        )

    assert result["task_id"] == "task-123"
    assert result["status"] == "started"
    assert result["new_task"] is True
    assert submitted["task_type"] == "ai_function_test"
    assert submitted["parameters"] == {
        "scenario": "thinking_tools",
        "user_input": "查官网年报",
    }
    assert submitted["user_id"] == 42
    assert background_tasks.tasks == [
        (
            llm_endpoint.run_ai_function_test_task,
            (),
            {
                "task_id": "task-123",
                "scenario": "thinking_tools",
                "user_input": "查官网年报",
            },
        )
    ]


@pytest.mark.asyncio
async def test_ai_function_test_background_task_persists_result():
    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    updates = []

    def _fake_update_task_status(
        db,
        task_id,
        status,
        result=None,
        error_message=None,
        notification_result=None,
    ):
        updates.append({
            "task_id": task_id,
            "status": status,
            "result": result,
            "error_message": error_message,
            "notification_result": notification_result,
        })

    async def _fake_execute_ai_function_test(*, scenario, user_input):
        return {
            "status": "success",
            "message": "AI function test completed",
            "scenario": scenario,
            "scenario_label": "Thinking Tools",
            "elapsed_ms": 1234,
            "input": {"user_input": user_input},
        }

    with patch("app.api.endpoints.llm.SessionLocal", return_value=FakeSession()), \
         patch("app.api.endpoints.llm.task_manager.update_task_status", side_effect=_fake_update_task_status), \
         patch("app.api.endpoints.llm.execute_ai_function_test", side_effect=_fake_execute_ai_function_test):
        await llm_endpoint.run_ai_function_test_task(
            task_id="task-123",
            scenario="thinking_tools",
            user_input="查官网年报",
        )

    assert updates == [
        {
            "task_id": "task-123",
            "status": "running",
            "result": None,
            "error_message": None,
            "notification_result": None,
        },
        {
            "task_id": "task-123",
            "status": "completed",
            "result": {
                "status": "success",
                "message": "AI function test completed",
                "scenario": "thinking_tools",
                "scenario_label": "Thinking Tools",
                "elapsed_ms": 1234,
                "input": {"user_input": "查官网年报"},
            },
            "error_message": None,
            "notification_result": {
                "status": "success",
                "message": "AI function test completed",
                "scenario": "thinking_tools",
                "scenario_label": "Thinking Tools",
                "elapsed_ms": 1234,
                "result_available": True,
            },
        },
    ]
