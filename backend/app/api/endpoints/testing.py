import json
import time
from collections.abc import Sequence
from typing import Annotated, Any, Dict, List

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.ai.agentic import tools
from app.ai.agentic.skills_loader import skill_tools
from app.ai.agentic.tooling.news_plugins import get_news_plugins
from app.ai.memory_client import memory_client
from app.core.database import SessionLocal
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.core.redis_client import redis_client
from app.data.ingestors.manager import ingestor_manager

logger = get_logger(__name__)

router = APIRouter()

FIXED_TEST_TOOLS: List[Dict[str, Any]] = [
    {"name": "redis", "title_key": "settings.redis_test_title", "route_slug": "redis"},
    {"name": "db", "title_key": "settings.db_test_title", "route_slug": "db"},
    {"name": "tushare", "title_key": "settings.tushare_test_title", "route_slug": "tushare"},
    {"name": "python_sandbox", "title_key": "settings.python_sandbox_test_title", "route_slug": "python_sandbox"},
    {"name": "skills", "title_key": "settings.skills_test_title", "route_slug": "skills"},
    {"name": "db_schema", "title_key": "settings.db_schema_test_title", "route_slug": "db_schema"},
    {"name": "query_calc", "title_key": "settings.query_calc_test_title", "route_slug": "query_calc"},
    {"name": "pdf_tool", "title_key": "settings.pdf_tool_test_title", "route_slug": "pdf_tool"},
    {"name": "memory_write", "title_key": "settings.memory_write_test_title", "route_slug": "memory"},
    {"name": "memory_read", "title_key": "settings.memory_read_test_title", "route_slug": "memory_read"},
    {"name": "docstring", "title_key": "settings.docstring_test_title", "route_slug": "docstrings"},
]

MEMORY_TEST_USER_ID = 999999
MEMORY_TEST_STOCK_CODE = "TEST.MEM"
MEMORY_TEST_CONTENT = (
    "Memory connectivity probe TEST.MEM: current service status is healthy, "
    "and the backend memory write path should persist this probe record."
)
MEMORY_TEST_QUERY = "What is the current service status of the Memory connectivity probe TEST.MEM?"


def _build_test_message_keys(name: str) -> tuple[str, str]:
    return f"{name}-success", f"{name}-failed"


def _translate_testing_message(key: str, fallback_key: str | None = None, **kwargs: Any) -> str:
    full_key = f"testing.{key}"
    message = i18n_service.t(full_key)
    if message == full_key and fallback_key:
        fallback_full_key = f"testing.{fallback_key}"
        message = i18n_service.t(fallback_full_key)
    if message == full_key:
        legacy_full_key = f"testing.{key.replace('-', '_')}"
        message = i18n_service.t(legacy_full_key)
    return message.format(**kwargs) if kwargs else message


def _success_response(name: str, elapsed_ms: int, fallback_key: str | None = None) -> Dict[str, Any]:
    success_key, _ = _build_test_message_keys(name)
    return {
        "status": "success",
        "message": _translate_testing_message(success_key, fallback_key=fallback_key),
        "elapsed_ms": elapsed_ms,
    }


def _error_response(name: str, error: str, fallback_key: str | None = None) -> Dict[str, Any]:
    _, failure_key = _build_test_message_keys(name)
    return {
        "status": "error",
        "message": _translate_testing_message(failure_key, fallback_key=fallback_key, error=error),
    }


def _serialize_test_tool(
    *,
    name: str,
    title: str,
    route_slug: str,
    category: str,
    source: str | None = None,
    default_keyword: str | None = None,
) -> Dict[str, Any]:
    success_key, failure_key = _build_test_message_keys(name)
    payload = {
        "name": name,
        "title": title,
        "category": category,
        "route_slug": route_slug,
        "test_route": f"/testing/{route_slug}",
        "success_key": success_key,
        "failure_key": failure_key,
    }
    if source is not None:
        payload["source"] = source
    if default_keyword is not None:
        payload["default_keyword"] = default_keyword
    return payload


def _get_fixed_testing_tools() -> List[Dict[str, Any]]:
    return [
        _serialize_test_tool(
            name=item["name"],
            title=i18n_service.t(item["title_key"]),
            route_slug=item["route_slug"],
            category="fixed",
            default_keyword=item.get("default_keyword"),
        )
        for item in FIXED_TEST_TOOLS
    ]


def _get_news_testing_tools() -> List[Dict[str, Any]]:
    return [
        _serialize_test_tool(
            name=plugin.name,
            title=plugin.name,
            route_slug=source,
            category="news",
            source=source,
            default_keyword=plugin.keyword_examples[0] if plugin.keyword_examples else None,
        )
        for source, plugin in sorted(get_news_plugins().items())
    ]


async def _run_news_source_test(
    name: str,
    source: str,
    keywords: Sequence[str],
    limit: int,
    label: str,
) -> Dict[str, Any]:
    try:
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y-%m-%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        start_time = time.time()
        seen_keywords = set()
        for raw_keyword in keywords:
            keyword = raw_keyword.strip()
            if not keyword or keyword in seen_keywords:
                continue
            seen_keywords.add(keyword)
            res = await tools.search_news.ainvoke({
                "keyword": keyword,
                "source": source,
                "limit": limit,
                "from_date": week_ago,
                "to_date": today,
            })
            for item in res:
                logger.info("Test %s item for keyword '%s': %s", label, keyword, item)
            if res and isinstance(res, list) and len(res) > 0 and res[0].get("fatal"):
                error_message = res[0].get("error", "Fatal source error")
                return {
                    "status": "error",
                    "message": (
                        f"{name} "
                        f"{i18n_service.t('testing.news_tool_failed_suffix').format(error=error_message)}"
                    ),
                    "keyword": keyword,
                    "fatal": True,
                }
            if res and isinstance(res, list) and len(res) > 0 and "error" not in res[0]:
                elapsed = int((time.time() - start_time) * 1000)
                return {
                    "status": "success",
                    "message": f"{name} {i18n_service.t('testing.news_tool_success_suffix')}",
                    "elapsed_ms": elapsed,
                    "keyword": keyword,
                    "items": res,
                }
        logger.warning("Test %s returned no results for keywords: %s", label, sorted(seen_keywords))
        return {
            "status": "error",
            "message": f"{name} {i18n_service.t('testing.news_tool_failed_suffix').format(error='No news found')}",
            "keywords": sorted(seen_keywords),
        }
    except Exception as e:
        logger.exception("Test %s failed: %s", label, e)
        return {
            "status": "error",
            "message": f"{name} {i18n_service.t('testing.news_tool_failed_suffix').format(error=str(e))}",
        }


def _register_dynamic_news_test_routes() -> None:
    for source, plugin in get_news_plugins().items():
        route_slug = source
        default_keywords = tuple(plugin.keyword_examples) if plugin.keyword_examples else ("AI",)
        limit = 2 if source.endswith("_announcements") else 3
        label = plugin.tool_name

        async def _news_test_handler(
            keyword: str = Query(""),
            name: str = plugin.name,
            source: str = source,
            default_keywords: tuple[str, ...] = default_keywords,
            limit: int = limit,
            label: str = label,
        ) -> Dict[str, Any]:
            keywords = (keyword,) if keyword.strip() else default_keywords
            return await _run_news_source_test(name, source, keywords, limit, label)

        _news_test_handler.__name__ = f"test_{route_slug}"
        router.get(f"/{route_slug}", response_model=Dict[str, Any])(_news_test_handler)


@router.get("/news_plugins", response_model=Dict[str, Any])
async def list_news_plugin_tests():
    return {
        "status": "success",
        "count": len(get_news_plugins()),
        "items": _get_news_testing_tools(),
    }


@router.get("/tools", response_model=Dict[str, Any])
async def list_testing_tools():
    fixed_tools = _get_fixed_testing_tools()
    news_tools = _get_news_testing_tools()
    return {
        "status": "success",
        "fixed_tools": fixed_tools,
        "news_tools": news_tools,
        "count": len(fixed_tools) + len(news_tools),
    }


@router.get("/docstrings", response_model=Dict[str, Any])
async def test_docstrings():
    try:
        start_time = time.time()
        items = []
        for tool in tools.get_all_tools():
            description = getattr(tool, "description", None) or getattr(tool, "__doc__", "") or ""
            items.append({
                "name": getattr(tool, "name", ""),
                "description": description.strip(),
            })
        elapsed = int((time.time() - start_time) * 1000)
        response = _success_response("docstring", elapsed)
        response["items"] = items
        return response
    except Exception as e:
        logger.exception("Test docstrings failed: %s", e)
        return _error_response("docstring", str(e))


@router.get("/redis", response_model=Dict[str, Any])
async def test_redis():
    try:
        start_time = time.time()
        await redis_client.redis.ping()
        elapsed = int((time.time() - start_time) * 1000)
        return {"status": "success", "message": i18n_service.t("testing.redis_success"), "elapsed_ms": elapsed}
    except Exception as e:
        return {"status": "error", "message": i18n_service.t("testing.redis_failed").format(error=str(e))}


@router.get("/db", response_model=Dict[str, Any])
async def test_db():
    try:
        start_time = time.time()
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        elapsed = int((time.time() - start_time) * 1000)
        return {"status": "success", "message": i18n_service.t("testing.db_success"), "elapsed_ms": elapsed}
    except Exception as e:
        return {"status": "error", "message": i18n_service.t("testing.db_failed").format(error=str(e))}


@router.get("/tushare", response_model=Dict[str, Any])
async def test_tushare():
    try:
        ingestor = ingestor_manager.get_ingestor("tushare")
        if not ingestor or not ingestor.pro:
            return {"status": "error", "message": i18n_service.t("testing.tushare_no_token")}

        start_time = time.time()
        # 默认只查询几条数据的接口测试
        df = ingestor.pro.stock_basic(exchange='', list_status='L',
                                      fields='ts_code,symbol,name,area,industry,list_date', limit=5)
        elapsed = int((time.time() - start_time) * 1000)
        if df is not None and not df.empty:
            return {"status": "success", "message": i18n_service.t("testing.tushare_success"), "elapsed_ms": elapsed}
        else:
            return {"status": "error", "message": i18n_service.t("testing.tushare_empty")}
    except Exception as e:
        return {"status": "error", "message": i18n_service.t("testing.tushare_failed").format(error=str(e))}


@router.get("/python_sandbox", response_model=Dict[str, Any])
async def test_python_sandbox():
    try:
        start_time = time.time()
        code = "result = 1 + 1"
        # 使用 ainvoke 并传递 dict 参数 (Use ainvoke with dict params)
        res = await tools.execute_python_sandboxed.ainvoke({"code": code})
        logger.info(f"Test Python Sandbox result: {res}")
        elapsed = int((time.time() - start_time) * 1000)
        if res and "2" in str(res):
            return {
                "status": "success",
                "message": i18n_service.t("testing.python_sandbox_success"),
                "elapsed_ms": elapsed,
            }
        return {
            "status": "error",
            "message": i18n_service.t("testing.python_sandbox_failed").format(error=f"Unexpected result: {res}"),
        }
    except Exception as e:
        logger.exception(f"Test Python Sandbox failed: {e}")
        return {"status": "error", "message": i18n_service.t("testing.python_sandbox_failed").format(error=str(e))}


@router.get("/skills", response_model=Dict[str, Any])
async def test_skills():
    try:
        start_time = time.time()
        catalog = skill_tools.list_skills.invoke({})
        skills = catalog.get("skills") if isinstance(catalog, dict) else None
        if not isinstance(skills, list) or not skills:
            return _error_response("skills", "No skills found")

        skill = next((item for item in skills if isinstance(item, dict)), None)
        if not skill:
            return _error_response("skills", "Invalid skills catalog")

        skill_id = str(skill.get("skill_id") or "").strip()
        if not skill_id:
            return _error_response("skills", "Skill id missing from catalog")

        loaded = skill_tools.load_skill.invoke({"skill_id": skill_id})
        if not isinstance(loaded, dict) or not loaded.get("success") or not loaded.get("content"):
            return _error_response("skills", str((loaded or {}).get("error") or "Failed to load skill"))

        skill_file = skill_tools.read_skill_file.invoke({"skill_id": skill_id, "relative_path": "SKILL.md"})
        if not isinstance(skill_file, dict) or not skill_file.get("success") or not skill_file.get("content"):
            return _error_response("skills", str((skill_file or {}).get("error") or "Failed to read SKILL.md"))

        script_probe: Dict[str, Any] = {"status": "skipped"}
        scripts = skill.get("scripts") if isinstance(skill.get("scripts"), list) else []
        if "scripts/fetch_sdk_docs.py" in scripts:
            script_result = await skill_tools.run_skill_script.ainvoke(
                {
                    "skill_id": skill_id,
                    "command": ["python", "scripts/fetch_sdk_docs.py", "--help"],
                    "timeout_seconds": 10,
                }
            )
            if not isinstance(script_result, dict) or not script_result.get("success"):
                return _error_response("skills", str((script_result or {}).get("error") or "Skill script probe failed"))
            script_probe = {
                "status": "success",
                "exit_code": script_result.get("exit_code"),
                "stdout_preview": str(script_result.get("stdout") or "")[:200],
            }

        elapsed = int((time.time() - start_time) * 1000)
        response = _success_response("skills", elapsed)
        response["skill_count"] = len(skills)
        response["skill_id"] = skill_id
        response["script_probe"] = script_probe
        logger.info("Test skills result: %s", response)
        return response
    except Exception as e:
        logger.exception("Test skills failed: %s", e)
        return _error_response("skills", str(e))


@router.get("/db_schema", response_model=Dict[str, Any])
async def test_db_schema():
    try:
        start_time = time.time()
        res = await tools.get_database_schema.ainvoke({})
        logger.info(f"Test DB Schema result length: {len(str(res))}")
        elapsed = int((time.time() - start_time) * 1000)
        # 校验：返回大纲中包含关键数据表 (Validation: Schema contains key tables)
        if res and ("StockBasic" in str(res) or "StockDaily" in str(res)):
            return {"status": "success", "message": i18n_service.t("testing.db_schema_success"), "elapsed_ms": elapsed}
        return {
            "status": "error",
            "message": i18n_service.t("testing.db_schema_failed").format(error="Key tables missing in schema"),
        }
    except Exception as e:
        logger.exception(f"Test DB Schema failed: {e}")
        return {"status": "error", "message": i18n_service.t("testing.db_schema_failed").format(error=str(e))}


@router.get("/query_calc", response_model=Dict[str, Any])
async def test_query_calc():
    try:
        # 先同步数据，确保数据库中有该股票信息
        await ingestor_manager.fetch_and_ingest_stock_info(stock_code="600519.SH")

        start_time = time.time()
        res = await tools.query_and_calculate.ainvoke({
            "table_name": "StockBasic",
            "filters": [{"column": "stock_code", "op": "==", "value": "600519.SH"}],
            "compute_code": "print(json.dumps({'result': len(data)}, ensure_ascii=False))",
            "limit": 1
        })
        logger.info(f"Test Query Calc result: {res}")
        elapsed = int((time.time() - start_time) * 1000)
        # 校验：返回计算结果 (Validation: Result contains the calculated value)
        stdout = str(res.get("stdout") or "") if isinstance(res, dict) else ""
        try:
            calc_output = json.loads(stdout.strip()) if stdout.strip() else {}
        except json.JSONDecodeError:
            calc_output = {}
        if isinstance(res, dict) and res.get("success") and calc_output.get("result") == 1:
            return {"status": "success", "message": i18n_service.t("testing.query_calc_success"), "elapsed_ms": elapsed}
        return {
            "status": "error",
            "message": i18n_service.t("testing.query_calc_failed").format(error="Calculation result missing"),
        }
    except Exception as e:
        logger.exception(f"Test Query Calc failed: {e}")
        return {"status": "error", "message": i18n_service.t("testing.query_calc_failed").format(error=str(e))}


@router.get("/pdf_tool", response_model=Dict[str, Any])
async def test_pdf_tool(
    url: Annotated[str, Query(min_length=1, max_length=2048)],
):
    try:
        pdf_url = url.strip()
        if not pdf_url:
            return _error_response("pdf_tool", "PDF URL is required")

        start_time = time.time()
        result = await tools.parse_pdf_to_markdown.ainvoke({
            "url": pdf_url,
            "engine": "word",
            "timeout": 120.0,
            "max_chars": 40_000,
        })
        elapsed = int((time.time() - start_time) * 1000)
        if isinstance(result, dict) and result.get("status") == "success" and result.get("markdown"):
            response = _success_response("pdf_tool", elapsed)
            response["url"] = pdf_url
            response["engine"] = result.get("engine")
            response["markdown_length"] = result.get("markdown_length")
            response["truncated"] = result.get("truncated")
            response["preview"] = str(result.get("markdown") or "")[:500]
            logger.info(
                "Test PDF tool result: engine=%s markdown_length=%s",
                response["engine"],
                response["markdown_length"],
            )
            return response
        error = str(result.get("error") if isinstance(result, dict) else result)
        return _error_response("pdf_tool", error or "PDF tool returned empty result")
    except Exception as e:
        logger.exception("Test PDF tool failed: %s", e)
        return _error_response("pdf_tool", str(e))


@router.get("/memory", response_model=Dict[str, Any])
async def test_memory():
    try:
        if not memory_client.enabled:
            return _error_response("memory_write", "Memory service not enabled")

        start_time = time.time()
        response = await memory_client.write_memory(
            user_id=MEMORY_TEST_USER_ID,
            stock_code=MEMORY_TEST_STOCK_CODE,
            content=MEMORY_TEST_CONTENT,
        )
        elapsed = int((time.time() - start_time) * 1000)
        data = response.get("data") if isinstance(response, dict) else None
        memory_id = data.get("memory_id") if isinstance(data, dict) else None
        if isinstance(response, dict) and memory_id:
            result = _success_response("memory_write", elapsed)
            result["memory_id"] = memory_id
            result["data"] = data
            logger.info(f"Test memory write result: {result}")
            return result
        last_error = memory_client.get_last_error("ingest")
        if last_error:
            return _error_response("memory_write", str(last_error.get("message") or "Memory write request failed"))
        return _error_response("memory_write", "Empty or invalid write response")
    except Exception as e:
        logger.exception("Test memory failed: %s", e)
        return _error_response("memory_write", str(e))


@router.get("/memory_read", response_model=Dict[str, Any])
async def test_memory_read():
    try:
        if not memory_client.enabled:
            return _error_response("memory_read", "Memory service not enabled")

        start_time = time.time()
        data = await memory_client.recall(
            user_id=MEMORY_TEST_USER_ID,
            stock_code=MEMORY_TEST_STOCK_CODE,
            query=MEMORY_TEST_QUERY,
        )
        elapsed = int((time.time() - start_time) * 1000)
        if isinstance(data, dict):
            last_error = memory_client.get_last_error("recall")
            if last_error:
                return _error_response("memory_read", str(last_error.get("message") or "Memory recall request failed"))
            result = _success_response("memory_read", elapsed)
            references = data.get("references") if isinstance(data.get("references"), list) else []
            result["count"] = len(references)
            result["data"] = data
            logger.info(f"Test memory read result: {result}")
            return result
        return _error_response("memory_read", "Invalid recall response")
    except Exception as e:
        logger.exception("Test memory read failed: %s", e)
        return _error_response("memory_read", str(e))


@router.get("/memory_preview", response_model=Dict[str, Any])
async def test_memory_preview(
    user_id: Annotated[int | None, Query(ge=1)] = None,
    stock_code: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    try:
        if not memory_client.enabled:
            return _error_response("memory_preview", "Memory service not enabled")

        start_time = time.time()
        response = await memory_client.preview_memories(
            user_id=user_id,
            stock_code=stock_code,
            status=status,
            limit=limit,
            offset=offset,
        )
        elapsed = int((time.time() - start_time) * 1000)
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, dict):
            result = _success_response("memory_preview", elapsed)
            items = [item for item in data.get("items") or [] if isinstance(item, dict)]
            result["data"] = data
            result["total"] = int(data.get("total") if isinstance(data.get("total"), int) else len(items))
            result["limit"] = int(data.get("limit") or limit)
            result["offset"] = int(data.get("offset") or offset)
            logger.info(f"Test memory preview result: total={result['total']} count={len(items)}")
            return result
        last_error = memory_client.get_last_error("preview")
        if last_error:
            return _error_response("memory_preview", str(last_error.get("message") or "Memory preview request failed"))
        return _error_response("memory_preview", "Empty or invalid preview response")
    except Exception as e:
        logger.exception("Test memory preview failed: %s", e)
        return _error_response("memory_preview", str(e))


@router.get("/memory_recall_audits", response_model=Dict[str, Any])
async def test_memory_recall_audits(
    user_id: Annotated[int | None, Query(ge=1)] = None,
    stock_code: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[str | None, Query(max_length=64)] = None,
    error_code: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    try:
        if not memory_client.enabled:
            return _error_response("memory_recall_audits", "Memory service not enabled", fallback_key="memory_preview")

        start_time = time.time()
        response = await memory_client.preview_recall_audits(
            user_id=user_id,
            stock_code=stock_code,
            status=status,
            error_code=error_code,
            limit=limit,
            offset=offset,
        )
        elapsed = int((time.time() - start_time) * 1000)
        data = response.get("data") if isinstance(response, dict) else None
        if isinstance(data, dict):
            result = _success_response("memory_recall_audits", elapsed, fallback_key="memory_preview")
            items = [item for item in data.get("items") or [] if isinstance(item, dict)]
            for item in items:
                item["audit_id"] = item.get("audit_id") or item.get("query_id") or item.get("delete_id") or ""
            result["data"] = data
            result["total"] = int(data.get("total") if isinstance(data.get("total"), int) else len(items))
            result["limit"] = int(data.get("limit") or limit)
            result["offset"] = int(data.get("offset") or offset)
            logger.info(
                "Test memory recall audit preview result: total=%s count=%s",
                result["total"],
                len(items),
            )
            return result
        last_error = memory_client.get_last_error("recall_audit_preview")
        if last_error:
            return _error_response(
                "memory_recall_audits",
                str(last_error.get("message") or "Memory recall audit preview request failed"),
                fallback_key="memory_preview",
            )
        return _error_response(
            "memory_recall_audits",
            "Empty or invalid recall audit preview response",
            fallback_key="memory_preview",
        )
    except Exception as e:
        logger.exception("Test memory recall audit preview failed: %s", e)
        return _error_response("memory_recall_audits", str(e), fallback_key="memory_preview")


_register_dynamic_news_test_routes()
