import json
import logging
import time
from collections.abc import Mapping
from typing import Any, Dict

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from openai import AsyncOpenAI

from app.ai.agentic.tools import get_all_tools
from app.ai.agentic.mcp.runtime import get_mcp_tools
from app.ai.agentic.skills_loader.runtime import build_skills_catalog_prompt, get_skills_loader_tools
from app.ai.llm_routing import API_KEY_ALIAS_SHARED, CACHE_LANE_SHARED
from app.ai.llm_providers.factory import build_chat_completion_kwargs, build_chat_model
from app.ai.memory_client import memory_client
from app.core.config import settings
from app.core.security import get_current_user
from app.crud.llm_usage_log import llm_usage_log, record_llm_usage
from app.models.user import User
from app.schemas.llm_usage import LLMUsageStatsSchema
from app.tasks.task_manager import task_manager

router = APIRouter()
logger = logging.getLogger(__name__)

AI_FUNCTION_TEST_SCENARIOS = {
    "no_tools": "No tools",
    "tools": "Tools",
    "skills": "Skills",
    "tools_and_skills": "Tools and Skills",
    "thinking_tools": "Thinking Tools",
    "thinking_skills": "Thinking Skills",
}
AI_FUNCTION_TEST_MAX_TOOL_ITERATIONS = 50
CACHE_LANE_MARKET_WATCH = "market_watch"
API_KEY_ALIAS_MARKET_WATCH = "market_watch_llm_api_key"


def _elapsed_ms(start_time: float) -> int:
    return int((time.time() - start_time) * 1000)


def _preview_text(value: Any, limit: int = 200) -> str:
    return str(value or "").replace("\r\n", "\n").strip()[:limit]


def _build_llm_probe_model(model: str, max_tokens: int = 256) -> Any:
    """
    构造 LLM 探针使用的聊天模型。

    Args:
        model: LiteLLM 模型别名。
        max_tokens: 探针响应的最大输出 token 数。

    Returns:
        可执行 LangChain 调用的聊天模型。
    """

    return build_chat_model(
        model=model,
        temperature=1,
        max_tokens=max_tokens,
    )


def _llm_usage_observability_for_role(role: str) -> dict[str, Any]:
    """Return usage observability metadata for direct LLM endpoint calls."""

    if role == "market_watch":
        return {
            "workflow": "market_watch",
            "stage": "watch_ai_gate",
            "call_kind": "agent",
            "cache_lane": CACHE_LANE_MARKET_WATCH,
            "api_key_alias": API_KEY_ALIAS_MARKET_WATCH,
        }
    return {
        "workflow": "llm_probe",
        "stage": role,
        "call_kind": "probe",
        "cache_lane": CACHE_LANE_SHARED,
        "api_key_alias": API_KEY_ALIAS_SHARED,
    }


def _normalize_tool_args(raw_args: Any) -> Dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if raw_args is None:
        return {}
    return {"value": str(raw_args)}


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _message_record(role: str, content: str, **extra: Any) -> Dict[str, Any]:
    return {
        "role": role,
        "content": content,
        **extra,
    }


def _serialize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str(tool_call.get("id") or ""),
            "name": str(tool_call.get("name") or ""),
            "args": _json_safe(tool_call.get("args") or {}),
        }
        for tool_call in tool_calls
    ]


def _serialize_llm_response(response: Any) -> Dict[str, Any]:
    additional_kwargs = getattr(response, "additional_kwargs", {}) or {}
    return {
        "content": str(getattr(response, "content", "") or ""),
        "reasoning_content": str(additional_kwargs.get("reasoning_content") or ""),
        "tool_calls": _serialize_tool_calls(list(getattr(response, "tool_calls", []) or [])),
    }


def _is_ai_function_thinking_scenario(scenario: str) -> bool:
    return scenario in {"thinking_tools", "thinking_skills"}


def _scenario_requires_ai_tools(scenario: str) -> bool:
    return scenario in {"tools", "tools_and_skills", "thinking_tools"}


def _scenario_requires_skills(scenario: str) -> bool:
    return scenario in {"skills", "tools_and_skills", "thinking_skills"}


def _get_real_ai_tools() -> list[Any]:
    return get_all_tools()


def _get_tool_by_name(tool_list: list[Any], tool_name: str) -> Any:
    for tool_obj in tool_list:
        if tool_obj.name == tool_name:
            return tool_obj
    raise RuntimeError(f"Required tool is not registered: {tool_name}")


def _build_ai_function_test_system_prompt(scenario: str) -> str:
    latest_docs_rule = (
        "For `tushare-data`, before calling `scripts/call_tushare.py`, you must call "
        "`scripts/fetch_sdk_docs.py` for the selected Tushare interface URL and read the latest docs. "
    )
    if scenario == "no_tools":
        return "Do not call any tools."
    if scenario in {"tools", "thinking_tools"}:
        return (
            "You are testing real backend AI tools. Call at least one bound AI tool before answering. "
            "Choose suitable bound AI tools according to the user request. Do not call skills loader tools."
        )
    if scenario in {"skills", "thinking_skills"}:
        return (
            "Call `list_skills` exactly once before answering. "
            f"{latest_docs_rule}"
            "If any tool fails, report the tool error and do not fabricate data.\n\n"
            f"{build_skills_catalog_prompt()}"
        )
    return (
        "Call `list_skills` and at least one bound real AI tool before answering. "
        "Choose suitable bound AI tools according to the user request. "
        f"{latest_docs_rule}"
        "If any tool fails, report the tool error and do not fabricate data.\n\n"
        f"{build_skills_catalog_prompt()}"
    )


async def _get_ai_function_test_tools(scenario: str) -> list[Any]:
    """返回 AI 功能测试可绑定工具，包含启用的 MCP 工具。"""
    test_tools: list[Any] = []
    if _scenario_requires_ai_tools(scenario):
        test_tools.extend(_get_real_ai_tools())
    if _scenario_requires_skills(scenario):
        test_tools.extend(get_skills_loader_tools())
    if _scenario_requires_ai_tools(scenario):
        try:
            test_tools.extend(await get_mcp_tools())
        except Exception as exc:
            logger.warning("MCP tools unavailable for AI function test", extra={"scenario": scenario, "error": str(exc)})
    return test_tools


def _expected_ai_function_test_requirements(scenario: str) -> list[str]:
    expected_requirements: list[str] = []
    if _scenario_requires_ai_tools(scenario):
        expected_requirements.append("at least one real AI tool")
    if _scenario_requires_skills(scenario):
        expected_requirements.append("list_skills")
    return expected_requirements


async def _execute_ai_function_tool_calls(
    tool_calls: list[dict[str, Any]],
    tool_map: dict[str, Any],
    messages: list[Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    tool_results: list[dict[str, Any]] = []
    executed_names: list[str] = []
    for index, tool_call in enumerate(tool_calls):
        tool_name = str(tool_call.get("name") or "")
        tool_args = _normalize_tool_args(tool_call.get("args"))
        tool_call_id = str(tool_call.get("id") or f"tool-{index}")
        tool_func = tool_map.get(tool_name)
        if not tool_func:
            result_payload: Any = {"error": f"unsupported tool: {tool_name}"}
        else:
            try:
                result_payload = await tool_func.ainvoke(tool_args)
                executed_names.append(tool_name)
            except Exception as exc:
                result_payload = {"success": False, "error": str(exc), "tool": tool_name}
        result_payload = _json_safe(result_payload)
        result_content = json.dumps(result_payload, ensure_ascii=False)
        messages.append(ToolMessage(tool_call_id=tool_call_id, content=result_content))
        tool_results.append({
            "name": tool_name,
            "args": _json_safe(tool_args),
            "result": result_payload,
        })
    return tool_results, executed_names


async def _run_llm_text_probe(
    name: str,
    model: str,
    requires_reasoning_content: bool = False,
    max_tokens: int = 128,
) -> Dict[str, Any]:
    start_time = time.time()
    llm = _build_llm_probe_model(model, max_tokens=max_tokens)
    response = await llm.ainvoke(
        [
            HumanMessage(
                content=(
                    "Reply with one short sentence containing the exact marker "
                    f"`{name}-ok`. Do not call tools."
                )
            )
        ]
    )
    additional_kwargs = getattr(response, "additional_kwargs", {}) or {}
    content = _preview_text(getattr(response, "content", ""))
    reasoning_content = _preview_text(additional_kwargs.get("reasoning_content"))
    if not content and not (requires_reasoning_content and reasoning_content):
        return {
            "status": "error",
            "message": f"{name} returned empty content",
            "elapsed_ms": _elapsed_ms(start_time),
            "model": model,
            "has_reasoning_content": bool(reasoning_content),
        }

    return {
        "status": "success",
        "message": f"{name} completed",
        "elapsed_ms": _elapsed_ms(start_time),
        "model": model,
        "content_preview": content,
        "has_reasoning_content": bool(reasoning_content),
        "reasoning_content_preview": reasoning_content,
    }


async def _run_llm_tool_call_probe(tool_value: str = "tool-ok", max_tokens: int = 256) -> Dict[str, Any]:
    start_time = time.time()
    real_tool = _get_tool_by_name(_get_real_ai_tools(), "execute_python_sandboxed")
    llm = _build_llm_probe_model(settings.LLM_MODEL, max_tokens=max_tokens)
    llm_with_tools = llm.bind_tools([real_tool])
    messages: list[Any] = [
        SystemMessage(
            content=(
                "You are testing real backend tool calling. You must call `execute_python_sandboxed` exactly once "
                f"with Python code that sets `result = {tool_value!r}`, then summarize the tool result."
            )
        ),
        HumanMessage(content="Run the tool-call probe now."),
    ]

    response = await llm_with_tools.ainvoke(messages)
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    if not tool_calls:
        return {
            "status": "error",
            "message": "LLM did not request a tool call",
            "elapsed_ms": _elapsed_ms(start_time),
        }

    messages.append(response)
    executed_tools: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        if tool_call.get("name") != real_tool.name:
            continue
        tool_args = _normalize_tool_args(tool_call.get("args"))
        tool_result = await real_tool.ainvoke(tool_args)
        executed_tools.append({
            "name": real_tool.name,
            "args": tool_args,
            "result_preview": _preview_text(tool_result),
        })
        messages.append(
            ToolMessage(
                tool_call_id=str(tool_call.get("id") or f"tool-{len(executed_tools)}"),
                content=str(tool_result),
            )
        )

    if not executed_tools:
        return {
            "status": "error",
            "message": "LLM requested tools, but not the expected execute_python_sandboxed tool",
            "elapsed_ms": _elapsed_ms(start_time),
            "tool_calls": tool_calls,
        }

    final_response = await llm_with_tools.ainvoke(messages)
    return {
        "status": "success",
        "message": "LLM tool call completed",
        "elapsed_ms": _elapsed_ms(start_time),
        "tool_calls": executed_tools,
        "final_preview": _preview_text(getattr(final_response, "content", "")),
    }


async def _run_llm_skills_call_probe(max_tokens: int = 256) -> Dict[str, Any]:
    start_time = time.time()
    skill_loader_tools = get_skills_loader_tools()
    tool_map = {tool_obj.name: tool_obj for tool_obj in skill_loader_tools}
    llm = _build_llm_probe_model(settings.LLM_MODEL, max_tokens=max_tokens)
    llm_with_tools = llm.bind_tools(skill_loader_tools)
    response = await llm_with_tools.ainvoke(
        [
            SystemMessage(
                content=(
                    "You are testing external skills integration. You must call `list_skills` exactly once. "
                    "Do not call run_skill_script."
                )
            ),
            HumanMessage(content="List the available skills now."),
        ]
    )
    tool_calls = list(getattr(response, "tool_calls", []) or [])
    if not tool_calls:
        return {
            "status": "error",
            "message": "LLM did not request a skills tool call",
            "elapsed_ms": _elapsed_ms(start_time),
        }

    executed_tools: list[dict[str, Any]] = []
    for tool_call in tool_calls:
        tool_name = str(tool_call.get("name") or "")
        if tool_name != "list_skills":
            continue
        tool_result = await tool_map[tool_name].ainvoke(_normalize_tool_args(tool_call.get("args")))
        skill_count = int(tool_result.get("count") or 0) if isinstance(tool_result, Mapping) else 0
        executed_tools.append({
            "name": tool_name,
            "skill_count": skill_count,
        })

    if not executed_tools:
        return {
            "status": "error",
            "message": "LLM requested tools, but not the expected list_skills tool",
            "elapsed_ms": _elapsed_ms(start_time),
            "tool_calls": tool_calls,
        }

    return {
        "status": "success",
        "message": "LLM skills tool call completed",
        "elapsed_ms": _elapsed_ms(start_time),
        "tool_calls": executed_tools,
    }


async def _run_llm_probe_step(name: str, runner: Any) -> Dict[str, Any]:
    try:
        return await runner()
    except Exception as exc:
        logger.exception("LLM probe step failed: %s", name)
        return {
            "status": "error",
            "message": str(exc),
        }


async def run_llm_probe() -> Dict[str, Any]:
    """
    Run the one-click LLM probe suite.

    Returns:
        Probe result covering thinking mode, non-thinking mode, tool calls, and skills calls.
    """
    start_time = time.time()
    checks = {
        "thinking_mode": await _run_llm_probe_step(
            "thinking_mode",
            lambda: _run_llm_text_probe(
                "thinking-mode",
                settings.LLM_THINKING_MODEL,
                requires_reasoning_content=True,
                max_tokens=512,
            ),
        ),
        "non_thinking_mode": await _run_llm_probe_step(
            "non_thinking_mode",
            lambda: _run_llm_text_probe(
                "non-thinking-mode",
                settings.LLM_MODEL,
            ),
        ),
        "tool_call": await _run_llm_probe_step(
            "tool_call",
            _run_llm_tool_call_probe,
        ),
        "skills_call": await _run_llm_probe_step(
            "skills_call",
            _run_llm_skills_call_probe,
        ),
    }
    success = all(check.get("status") == "success" for check in checks.values())
    return {
        "status": "success" if success else "error",
        "message": "LLM one-click test passed" if success else "One or more LLM probes failed",
        "elapsed_ms": _elapsed_ms(start_time),
        "checks": checks,
        "model": settings.LLM_MODEL,
        "provider": settings.LLM_PROVIDER,
    }


async def _request_llm_completion(
    *,
    messages: list[dict[str, str]],
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    response_format: Mapping[str, Any] | None = None,
    extra_body: Mapping[str, Any] | None = None,
    role: str = "generic",
) -> Dict[str, Any]:
    resolved_model = model or settings.LLM_MODEL
    request_kwargs = build_chat_completion_kwargs(
        model=resolved_model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        extra_body=extra_body,
    )
    async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_SECONDS) as http_client:
        client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            http_client=http_client,
            max_retries=settings.LLM_MAX_RETRIES,
        )
        response = await client.chat.completions.create(**request_kwargs)
    await record_llm_usage(
        response,
        resolved_model,
        role,
        **_llm_usage_observability_for_role(role),
    )
    content = response.choices[0].message.content
    return {
        "content": content,
        "raw_response": response,
    }


def _merge_usage_counts(
    backend_counts: Dict[str, int] | None,
    memory_stats: Dict[str, Any] | None,
) -> Dict[str, int]:
    combined: Dict[str, int] = {str(key): int(value or 0) for key, value in (backend_counts or {}).items()}
    by_operation = (memory_stats or {}).get("by_operation")
    if isinstance(by_operation, dict):
        for operation, payload in by_operation.items():
            calls = 0
            if isinstance(payload, dict):
                calls = int(payload.get("calls") or 0)
            combined[str(operation)] = combined.get(str(operation), 0) + calls
    return combined


def _usage_int(stats: Dict[str, Any] | None, key: str) -> int:
    return int((stats or {}).get(key) or 0)


def _cache_hit_rate(cached_tokens: int, input_tokens: int) -> float:
    if input_tokens <= 0:
        return 0.0
    return cached_tokens / input_tokens


@router.get("/usage-stats", response_model=LLMUsageStatsSchema)
async def get_llm_usage_stats():
    """
    获取 LLM 使用统计数据
    
    Returns:
        使用统计汇总
    """
    try:
        backend_stats = await llm_usage_log.get_stats()
        memory_stats = await memory_client.get_usage_stats()
        memory_error = memory_client.get_last_error("usage_stats")
        if not memory_stats and memory_error:
            memory_stats = {
                "status": "error",
                "error": memory_error,
            }
        memory_totals = memory_stats if memory_stats and memory_stats.get("status") != "error" else {}
        combined_by_role = _merge_usage_counts(backend_stats.get("by_role"), memory_stats)
        combined_total_calls = _usage_int(backend_stats, "total_calls") + _usage_int(memory_totals, "total_calls")
        combined_input_tokens = _usage_int(backend_stats, "input_tokens") + _usage_int(memory_totals, "input_tokens")
        combined_output_tokens = _usage_int(backend_stats, "output_tokens") + _usage_int(memory_totals, "output_tokens")
        combined_total_tokens = _usage_int(backend_stats, "total_tokens") + _usage_int(memory_totals, "total_tokens")
        combined_cached_tokens = _usage_int(backend_stats, "cached_tokens") + _usage_int(memory_totals, "cached_tokens")
        combined_cache_miss_tokens = _usage_int(backend_stats, "cache_miss_tokens") + _usage_int(
            memory_totals,
            "cache_miss_tokens",
        )
        combined_reasoning_tokens = _usage_int(backend_stats, "reasoning_tokens") + _usage_int(
            memory_totals,
            "reasoning_tokens",
        )
        combined_cache_hit_rate = _cache_hit_rate(combined_cached_tokens, combined_input_tokens)
        return {
            "total_calls": combined_total_calls,
            "input_tokens": combined_input_tokens,
            "output_tokens": combined_output_tokens,
            "total_tokens": combined_total_tokens,
            "cached_tokens": combined_cached_tokens,
            "cache_miss_tokens": combined_cache_miss_tokens,
            "reasoning_tokens": combined_reasoning_tokens,
            "cache_hit_rate": combined_cache_hit_rate,
            "by_role": combined_by_role,
            "by_role_detail": backend_stats.get("by_role_detail"),
            "by_workflow": backend_stats.get("by_workflow"),
            "by_stage": backend_stats.get("by_stage"),
            "by_workflow_stage": backend_stats.get("by_workflow_stage"),
            "by_workflow_call_kind": backend_stats.get("by_workflow_call_kind"),
            "by_call_kind": backend_stats.get("by_call_kind"),
            "by_cache_lane": backend_stats.get("by_cache_lane"),
            "by_api_key_alias": backend_stats.get("by_api_key_alias"),
            "backend": backend_stats,
            "memory": memory_stats or None,
            "combined": {
                "total_calls": combined_total_calls,
                "input_tokens": combined_input_tokens,
                "output_tokens": combined_output_tokens,
                "total_tokens": combined_total_tokens,
                "cached_tokens": combined_cached_tokens,
                "cache_miss_tokens": combined_cache_miss_tokens,
                "reasoning_tokens": combined_reasoning_tokens,
                "cache_hit_rate": combined_cache_hit_rate,
                "by_role": combined_by_role,
                "by_role_detail": backend_stats.get("by_role_detail"),
                "by_workflow": backend_stats.get("by_workflow"),
                "by_stage": backend_stats.get("by_stage"),
                "by_workflow_stage": backend_stats.get("by_workflow_stage"),
                "by_workflow_call_kind": backend_stats.get("by_workflow_call_kind"),
                "by_call_kind": backend_stats.get("by_call_kind"),
                "by_cache_lane": backend_stats.get("by_cache_lane"),
                "by_api_key_alias": backend_stats.get("by_api_key_alias"),
            },
        }
    except Exception as e:
        logger.error(f"Failed to fetch LLM usage stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": str(e)}
        )


@router.delete("/usage-stats", response_model=Dict[str, Any])
async def clear_llm_usage_stats():
    """清空 LLM 使用统计数据。"""

    try:
        backend_deleted = await llm_usage_log.clear()
        memory_result = await memory_client.clear_usage_stats()
        memory_error = memory_client.get_last_error("clear_usage_stats")
        if not memory_result and memory_error:
            memory_result = {
                "status": "error",
                "error": memory_error,
            }
        memory_deleted = int(memory_result.get("deleted") or 0) if isinstance(memory_result, dict) else 0
        clear_status = "ok" if not memory_error else "partial"
        return {
            "status": clear_status,
            "backend": {"deleted": backend_deleted},
            "memory": memory_result or None,
            "total_deleted": backend_deleted + memory_deleted,
        }
    except Exception as e:
        logger.error(f"Failed to clear LLM usage stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": str(e)}
        )


@router.get("/health", response_model=Dict[str, Any])
async def llm_health_check():
    """
    检查LLM服务健康状态
    
    Returns:
        健康检查结果
    """
    try:
        completion = await _request_llm_completion(
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Say 'OK' if you can read this."},
            ],
            temperature=0.1,
            max_tokens=10,
            role="health_check",
        )
        return {
            "status": "healthy",
            "model": settings.LLM_MODEL,
            "base_url": settings.LLM_BASE_URL,
            "response": str(completion.get("content") or "")[:50],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LLM health check failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "unhealthy",
                "model": settings.LLM_MODEL,
                "base_url": settings.LLM_BASE_URL,
                "error": str(e),
            },
        )


@router.get("/models", response_model=Dict[str, Any])
async def get_llm_models():
    """
    获取当前配置的LLM模型信息
    
    Returns:
        模型配置信息
    """
    return {
        "current_model": settings.LLM_MODEL,
        "base_url": settings.LLM_BASE_URL,
        "available_models": [
            {
                "name": settings.LLM_MODEL,
                "provider": "LiteLLM",
                "description": "LiteLLM backend alias",
            },
            {
                "name": settings.LLM_THINKING_MODEL,
                "provider": "LiteLLM",
                "description": "LiteLLM thinking alias",
            },
        ],
    }


@router.get("/probe", response_model=Dict[str, Any])
async def probe_llm_capabilities():
    """
    一键测试 LLM thinking、non-thinking、tool call 和 skills call。

    Returns:
        LLM 能力探针结果。
    """
    return await run_llm_probe()


async def execute_ai_function_test(*, scenario: str, user_input: str) -> Dict[str, Any]:
    """
    Execute one AI function test scenario and return the full LLM/tool transcript.

    Args:
        scenario: Scenario name.
        user_input: User prompt for the scenario.

    Returns:
        Full request messages, tool calls, tool results, and final LLM output.
    """
    start_time = time.time()
    system_prompt = _build_ai_function_test_system_prompt(scenario)
    messages: list[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_input),
    ]
    input_messages = [
        _message_record("system", system_prompt),
        _message_record("user", user_input),
    ]
    test_tools = await _get_ai_function_test_tools(scenario)
    expected_requirements = _expected_ai_function_test_requirements(scenario)
    skill_tool_names = {tool_obj.name for tool_obj in get_skills_loader_tools()}
    ai_tool_names = {tool_obj.name for tool_obj in test_tools if tool_obj.name not in skill_tool_names}
    model = settings.LLM_THINKING_MODEL if _is_ai_function_thinking_scenario(scenario) else settings.LLM_MODEL
    llm = _build_llm_probe_model(model, max_tokens=1024)
    runnable = llm.bind_tools(test_tools) if test_tools else llm

    tool_map = {tool_obj.name: tool_obj for tool_obj in test_tools}
    first_response = await runnable.ainvoke(messages)
    first_response_payload = _serialize_llm_response(first_response)
    tool_results: list[dict[str, Any]] = []
    final_response_payload = first_response_payload
    executed_tool_names: list[str] = []
    current_response = first_response
    tool_iteration_count = 0

    while test_tools and tool_iteration_count < AI_FUNCTION_TEST_MAX_TOOL_ITERATIONS:
        raw_tool_calls = list(getattr(current_response, "tool_calls", []) or [])
        if not raw_tool_calls:
            break
        messages.append(current_response)
        iteration_results, iteration_names = await _execute_ai_function_tool_calls(raw_tool_calls, tool_map, messages)
        tool_iteration_count += 1
        for result in iteration_results:
            result["iteration"] = tool_iteration_count
        tool_results.extend(iteration_results)
        executed_tool_names.extend(iteration_names)
        current_response = await runnable.ainvoke(messages)
        final_response_payload = _serialize_llm_response(current_response)

    executed_tool_name_set = set(executed_tool_names)
    missing_requirements: list[str] = []
    if _scenario_requires_ai_tools(scenario) and not executed_tool_name_set.intersection(ai_tool_names):
        missing_requirements.append("at least one real AI tool")
    if _scenario_requires_skills(scenario) and "list_skills" not in executed_tool_name_set:
        missing_requirements.append("list_skills")
    reached_tool_limit = bool(
        test_tools
        and tool_iteration_count >= AI_FUNCTION_TEST_MAX_TOOL_ITERATIONS
        and getattr(current_response, "tool_calls", [])
    )
    failed_tool_results = [
        result
        for result in tool_results
        if isinstance(result.get("result"), dict) and result["result"].get("success") is False
    ]
    success = not missing_requirements and not reached_tool_limit and not failed_tool_results
    return {
        "status": "success" if success else "error",
        "message": (
            "AI function test completed"
            if success
            else (
                "AI function test reached the max tool iterations"
                if reached_tool_limit
                else (
                    "AI function test tool execution failed"
                    if failed_tool_results
                    else f"AI function test missing expected requirements: {', '.join(missing_requirements)}"
                )
            )
        ),
        "scenario": scenario,
        "scenario_label": AI_FUNCTION_TEST_SCENARIOS[scenario],
        "elapsed_ms": _elapsed_ms(start_time),
        "input": {
            "scenario": scenario,
            "user_input": user_input,
            "messages": input_messages,
            "bound_tools": [tool_obj.name for tool_obj in test_tools],
            "expected_requirements": expected_requirements,
            "model": model,
            "max_tool_iterations": AI_FUNCTION_TEST_MAX_TOOL_ITERATIONS,
        },
        "output": {
            "first_response": first_response_payload,
            "tool_iteration_count": tool_iteration_count,
            "reached_tool_limit": reached_tool_limit,
            "failed_tool_results": failed_tool_results,
            "tool_results": tool_results,
            "final_response": final_response_payload,
        },
        "model": model,
        "provider": settings.LLM_PROVIDER,
    }


@router.post("/function-test", response_model=Dict[str, Any])
async def run_ai_function_test(
    request: Dict[str, Any],
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """
    Submit one AI function test scenario for background execution.

    Args:
        request: Scenario name and user input.
        background_tasks: FastAPI background task collector.
        current_user: Authenticated user.

    Returns:
        Async task submission payload with task_id.
    """
    scenario = str(request.get("scenario") or "").strip()
    user_input = str(request.get("user_input") or "").strip()
    if scenario not in AI_FUNCTION_TEST_SCENARIOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Invalid AI function test scenario"},
        )
    if not user_input:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "User input is required"},
        )

    task_info = await task_manager.submit_task(
        task_name=f"AI Function Test - {AI_FUNCTION_TEST_SCENARIOS[scenario]}",
        task_type="ai_function_test",
        parameters={
            "scenario": scenario,
            "user_input": user_input,
        },
        allow_concurrent=False,
        user_id=current_user.id,
    )

    if task_info.get("new_task", True):
        background_tasks.add_task(
            run_ai_function_test_task,
            task_id=task_info["task_id"],
            scenario=scenario,
            user_input=user_input,
        )

    return {
        "task_id": task_info["task_id"],
        "task_name": task_info["task_name"],
        "status": "started" if task_info.get("new_task", True) else task_info["status"],
        "message": task_info["message"],
        "new_task": task_info.get("new_task", True),
        "scenario": scenario,
        "scenario_label": AI_FUNCTION_TEST_SCENARIOS[scenario],
    }


async def run_ai_function_test_task(*, task_id: str, scenario: str, user_input: str) -> None:
    """
    Run an AI function test in the background and persist the full result on the async task.

    Args:
        task_id: Async task ID.
        scenario: Scenario name.
        user_input: User prompt for the scenario.
    """
    await task_manager.update_task_status(task_id=task_id, status="running")

    try:
        result = await execute_ai_function_test(scenario=scenario, user_input=user_input)
    except Exception as exc:
        logger.exception("AI function test task failed: task_id=%s scenario=%s", task_id, scenario)
        await task_manager.update_task_status(task_id=task_id, status="failed", error_message=str(exc))
        return

    notification_result = {
        "status": result.get("status"),
        "message": result.get("message"),
        "scenario": result.get("scenario"),
        "scenario_label": result.get("scenario_label"),
        "elapsed_ms": result.get("elapsed_ms"),
        "result_available": True,
    }
    await task_manager.update_task_status(
        task_id=task_id,
        status="completed",
        result=result,
        notification_result=notification_result,
    )


@router.post("/test", response_model=Dict[str, Any])
async def test_llm_call(request: Dict[str, Any]):
    """
    测试LLM调用
    
    Request Body:
        {
            "prompt": "测试提示词",
            "temperature": 0.7,
            "max_tokens": 100
        }
    
    Returns:
        LLM响应结果
    """
    try:
        prompt = request.get("prompt", "Hello, please respond with 'OK'.")
        temperature = request.get("temperature", 0.7)
        max_tokens = request.get("max_tokens", 100)
        
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt}
        ]
        
        completion = await _request_llm_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            role="test",
        )
        
        return {
            "success": True,
            "prompt": prompt,
            "response": completion.get("content"),
            "model": settings.LLM_MODEL
        }
    except Exception as e:
        logger.error(f"LLM test call failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": str(e)}
        )
