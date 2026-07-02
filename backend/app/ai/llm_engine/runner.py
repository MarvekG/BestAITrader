import traceback
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select, update

from app.core import database as database_module
from app.core.logger import get_logger
from app.core.request_context import clear_current_user_id
from app.core.request_context import clear_request_id
from app.core.request_context import set_current_user_id
from app.core.request_context import set_request_id
from app.ai.llm_engine.orchestrator import create_analyst_workflow
from app.models.async_task import AsyncTask
from app.models.session import Session as SessionModel
from app.core.utils.json_utils import sanitize_for_json

logger = get_logger(__name__)


async def _resolve_session_user_id(session_id: Optional[str]) -> int | None:
    """根据会话 ID 查找后台分析任务所属用户。

    Args:
        session_id: 会话 ID 字符串；为空时表示当前任务没有会话归属。

    Returns:
        会话所属用户 ID；缺少会话或未找到记录时返回 None。
    """
    if not session_id:
        return None

    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(SessionModel.user_id).where(SessionModel.session_id == UUID(session_id))
        )
        user_id = result.scalar_one_or_none()
        if user_id is None:
            return None
        return int(user_id)


async def _update_task_status(
    task_id: str,
    status: str,
    result: Optional[dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> None:
    """异步更新后台任务状态，避免在事件循环中执行同步数据库访问。

    Args:
        task_id: 任务 ID。
        status: 新状态。
        result: 成功或失败结果。
        error_message: 失败错误信息。
    """
    values: dict[str, Any] = {"status": status}
    if status == "running":
        values["started_at"] = datetime.now()
    if status in ["completed", "failed"]:
        values["completed_at"] = datetime.now()
    if result:
        values["result"] = result
    if error_message:
        values["error_message"] = error_message

    async with database_module.AsyncSessionLocal() as db:
        existing = await db.execute(select(AsyncTask.task_id).where(AsyncTask.task_id == task_id))
        if existing.scalar_one_or_none() is None:
            logger.warning(f"Task not found: {task_id}")
            return
        await db.execute(update(AsyncTask).where(AsyncTask.task_id == task_id).values(**values))
        await db.commit()


async def _update_session_status(session_id: str, status: str):
    """异步更新会话状态。

    Args:
        session_id: 会话 ID。
        status: 新会话状态。
    """
    try:
        async with database_module.AsyncSessionLocal() as db:
            await db.execute(
                update(SessionModel)
                .where(SessionModel.session_id == UUID(session_id))
                .values(status=status)
            )
            await db.commit()
    except Exception as e:
        logger.exception(f"Failed to update session {session_id} status to {status}: {e}")


def _build_initial_state(
    stock_code: str,
    trading_frequency: str,
    trading_strategy: str,
    session_id: Optional[str],
    trigger_reason: Optional[str] = None,
    evidence_summary: Optional[str] = None,
    discipline_trigger: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """构建 AI 分析工作流初始状态。

    Args:
        stock_code: 待分析股票代码。
        trading_frequency: 交易频率。
        trading_strategy: 交易策略。
        session_id: 会话 ID 字符串。
        trigger_reason: 盯盘自动启动辩论时传入的触发原因。
        evidence_summary: 盯盘自动启动辩论时传入的证据摘要。
        discipline_trigger: 持仓纪律扫描触发复议时传入的结构化触发上下文。

    Returns:
        可传入 LangGraph 工作流的初始状态。
    """
    static_context: dict[str, Any] = {}
    if trigger_reason or evidence_summary:
        trigger_context = {
            "source": "market_watch",
        }
        if trigger_reason:
            trigger_context["trigger_reason"] = trigger_reason
        if evidence_summary:
            trigger_context["evidence_summary"] = evidence_summary
        static_context["market_watch_trigger"] = trigger_context
    if discipline_trigger:
        static_context["discipline_trigger"] = discipline_trigger

    return {
        "stock_code": stock_code,
        "trading_frequency": trading_frequency,
        "trading_strategy": trading_strategy,
        "static_context": static_context,
        "context": {},
        "sentiment_report": "",
        "news_report": "",
        "policy_report": "",
        "vertical_reports": {},
        "strategic_reports": {},
        "strategic_round_2_1_reports": {},
        "pm_decision": "",
        "post_trade_reflection": {},
        "errors": [],
        "user_id": None,
        "session_id": UUID(session_id) if session_id else None,
    }


async def run_analysis_task(
    task_id: str,
    stock_code: str,
    trading_frequency: str,
    trading_strategy: str,
    session_id: Optional[str] = None,
    trigger_reason: Optional[str] = None,
    evidence_summary: Optional[str] = None,
    discipline_trigger: Optional[dict[str, Any]] = None,
    sync_before_analysis: bool = False,
    task_name: Optional[str] = None,
):
    """运行后台 AI 分析任务并维护任务状态。

    Args:
        task_id: 异步任务 ID。
        stock_code: 待分析股票代码。
        trading_frequency: 交易频率。
        trading_strategy: 交易策略。
        session_id: 会话 ID；存在时用于绑定用户上下文并持久化报告。
        trigger_reason: 盯盘自动启动辩论时传入的触发原因。
        evidence_summary: 盯盘自动启动辩论时传入的证据摘要。
        discipline_trigger: 持仓纪律扫描触发复议时传入的结构化触发上下文。
        sync_before_analysis: 是否在启动工作流前复用数据同步任务刷新单股数据。
        task_name: 异步任务展示名称，由后台任务运行器注入。
    """
    _ = task_name
    request_token = set_request_id(task_id)
    user_token = None

    try:
        user_token = set_current_user_id(await _resolve_session_user_id(session_id))

        # 1. Update status to running
        await _update_task_status(task_id, "running")

        # 2. Notification: Analysis started
        from app.api.endpoints.debate_ws import send_debate_status
        if session_id:
            await send_debate_status(session_id, "started")

        if sync_before_analysis:
            from app.tasks.analysis_data_sync import sync_stock_data_before_analysis

            await sync_stock_data_before_analysis(stock_code)

        # 3. Run workflow
        logger.info(f"Starting analysis workflow for task {task_id}, stock {stock_code}, session {session_id}")
        workflow = create_analyst_workflow()

        initial_state = _build_initial_state(
            stock_code,
            trading_frequency,
            trading_strategy,
            session_id,
            trigger_reason,
            evidence_summary,
            discipline_trigger,
        )

        # Run the graph (Long running async call, NO DB session held here)
        final_state = await workflow.ainvoke(initial_state)

        # Sanitize state for JSON serialization (handles NaN, Infinity, UUID, datetime)
        final_state = sanitize_for_json(final_state)

        # 4. Finalize status
        if final_state.get("errors"):
            logger.warning(f"Analysis task {task_id} completed with functional errors: {final_state['errors']}")
            error_message = str(final_state["errors"])
            await _update_task_status(
                task_id,
                "failed",
                result=final_state,
                error_message=error_message
            )
            if session_id:
                await _update_session_status(session_id, "failed")
                await send_debate_status(session_id, "error")
            return {"status": "failed", "error": error_message, "result": final_state}
        else:
            logger.info(f"Analysis task {task_id} completed successfully")
            await _update_task_status(
                task_id,
                "completed",
                result=final_state
            )
            if session_id:
                await _update_session_status(session_id, "completed")
                await send_debate_status(session_id, "completed")
            return final_state

    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        logger.error(f"Analysis task {task_id} failed with exception: {error_msg}")
        await _update_task_status(
            task_id,
            "failed",
            error_message=error_msg
        )
        if session_id:
            try:
                await _update_session_status(session_id, "failed")
                await send_debate_status(session_id, "error")
            except Exception as ws_err:
                logger.error(f"Failed to send error status via WS: {ws_err}")
        return {"status": "failed", "error": error_msg}
    finally:
        clear_current_user_id(user_token)
        clear_request_id(request_token)
