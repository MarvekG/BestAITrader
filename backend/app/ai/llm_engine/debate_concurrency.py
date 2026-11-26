import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from app.ai.llm_engine.runner import run_analysis_task
from app.core import database as database_module
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.core.runtime_settings import get_runtime_settings
from app.models.async_task import AsyncTask

logger = get_logger(__name__)

DEBATE_TASK_TYPE = "ai_analysis"
DEBATE_ACTIVE_STATUSES = ("pending", "running")
DEBATE_FINISHED_STATUSES = ("completed", "failed")

_dispatch_locks: dict[int, asyncio.Lock] = {}
_dispatched_task_ids: set[str] = set()
_background_dispatch_tasks: set[asyncio.Task[None]] = set()


def _get_dispatch_lock() -> asyncio.Lock:
    """获取当前事件循环对应的调度锁。

    Returns:
        当前事件循环内使用的异步锁。
    """
    loop_id = id(asyncio.get_running_loop())
    lock = _dispatch_locks.get(loop_id)
    if lock is None:
        lock = asyncio.Lock()
        _dispatch_locks[loop_id] = lock
    return lock


def format_ai_analysis_task_name(stock_code: str) -> str:
    """生成 AI 分析任务名。

    Args:
        stock_code: 标准股票代码。

    Returns:
        统一的 AI 分析任务名。
    """
    return f"AI Analysis - {stock_code}"


@dataclass(frozen=True)
class DebateStockTaskAlreadyRunning(Exception):
    """指定股票已有待执行或运行中的 AI 投研辩论任务。"""

    stock_code: str
    task_id: str
    session_id: str | None = None

    def __str__(self) -> str:
        """返回可展示给前端的股票任务互斥提示。

        Returns:
            包含股票代码和任务 ID 的错误消息。
        """
        return i18n_service.t(
            "tasks.ai_analysis_stock_already_running",
            stock_code=self.stock_code,
            task_id=self.task_id,
        )


async def count_running_debate_tasks() -> int:
    """异步统计当前运行中的 AI 投研辩论任务数量。"""
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(func.count()).select_from(AsyncTask).where(
                AsyncTask.task_type == DEBATE_TASK_TYPE,
                AsyncTask.status == "running",
            )
        )
        return int(result.scalar_one() or 0)


async def find_running_debate_task_for_session(session_id: str) -> AsyncTask | None:
    """异步查找指定会话正在排队或运行的 AI 投研辩论任务。

    Args:
        session_id: 辩论会话 ID。

    Returns:
        匹配的异步任务；没有则返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(AsyncTask).where(
                AsyncTask.task_type == DEBATE_TASK_TYPE,
                AsyncTask.status.in_(DEBATE_ACTIVE_STATUSES),
            )
        )
        for task in result.scalars().all():
            parameters = task.parameters if isinstance(task.parameters, dict) else {}
            if parameters.get("session_id") == session_id:
                return task
    return None


async def find_running_debate_task_for_stock(stock_code: str) -> AsyncTask | None:
    """异步查找指定股票正在排队或运行的 AI 投研辩论任务。

    Args:
        stock_code: 标准股票代码。

    Returns:
        匹配的异步任务；没有则返回 None。
    """
    task_name = format_ai_analysis_task_name(stock_code)
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(AsyncTask).where(
                AsyncTask.task_type == DEBATE_TASK_TYPE,
                AsyncTask.status.in_(DEBATE_ACTIVE_STATUSES),
            )
        )
        for task in result.scalars().all():
            parameters = task.parameters if isinstance(task.parameters, dict) else {}
            if task.task_name == task_name or parameters.get("stock_code") == stock_code:
                return task
    return None


async def ensure_debate_launch_available(stock_code: str) -> None:
    """校验指定股票可入队新的 AI 投研辩论任务。

    规则：同一股票全系统只允许一个 pending/running 的 AI 分析任务；
    全局并发上限只限制 running 数量，超出部分进入 FIFO 队列。

    Args:
        stock_code: 标准股票代码。

    Raises:
        DebateStockTaskAlreadyRunning: 指定股票已有排队中或运行中的任务。
    """
    existing_task = await find_running_debate_task_for_stock(stock_code)
    if existing_task:
        parameters = existing_task.parameters if isinstance(existing_task.parameters, dict) else {}
        raise DebateStockTaskAlreadyRunning(
            stock_code=stock_code,
            task_id=existing_task.task_id,
            session_id=parameters.get("session_id"),
        )


def build_debate_task_kwargs(task: AsyncTask) -> dict[str, Any]:
    """从异步任务参数还原 `run_analysis_task` 的调用参数。

    Args:
        task: 待调度的 AI 分析异步任务。

    Returns:
        可直接传给 `run_analysis_task` 的关键字参数。

    Raises:
        ValueError: 任务缺少启动分析所需的基础参数时抛出。
    """
    parameters = task.parameters if isinstance(task.parameters, dict) else {}
    stock_code = parameters.get("stock_code")
    trading_frequency = parameters.get("trading_frequency")
    trading_strategy = parameters.get("trading_strategy")
    if not stock_code or not trading_frequency or not trading_strategy:
        raise ValueError(f"Task {task.task_id} is missing required debate parameters")
    return {
        "task_id": task.task_id,
        "task_name": task.task_name,
        "session_id": parameters.get("session_id"),
        "stock_code": stock_code,
        "trading_frequency": trading_frequency,
        "trading_strategy": trading_strategy,
        "trigger_reason": parameters.get("trigger_reason"),
        "evidence_summary": parameters.get("evidence_summary"),
        "discipline_trigger": parameters.get("discipline_trigger"),
        "sync_before_analysis": bool(parameters.get("sync_before_analysis", False)),
    }


async def _list_pending_debate_tasks(*, limit: int, exclude_ids: set[str]) -> list[AsyncTask]:
    """按提交顺序读取排队中的 AI 投研辩论任务。

    Args:
        limit: 本次最多读取的任务数量。
        exclude_ids: 已提交给后台运行器、但尚未进入 running 的任务 ID。

    Returns:
        按创建时间升序排列的待执行任务。
    """
    async with database_module.AsyncSessionLocal() as db:
        stmt = select(AsyncTask).where(
            AsyncTask.task_type == DEBATE_TASK_TYPE,
            AsyncTask.status == "pending",
        )
        if exclude_ids:
            stmt = stmt.where(AsyncTask.task_id.notin_(list(exclude_ids)))
        result = await db.execute(
            stmt.order_by(AsyncTask.created_at.asc(), AsyncTask.task_id.asc()).limit(limit)
        )
        return list(result.scalars().all())


async def _mark_task_failed(task: AsyncTask, error_message: str) -> None:
    """把调度失败的任务标记为 failed，并同步会话失败状态。

    Args:
        task: 调度失败的 AI 分析异步任务。
        error_message: 调度失败原因。
    """
    from app.ai.llm_engine.runner import update_session_status
    from app.api.endpoints.debate_ws import send_debate_status
    from app.tasks.task_manager import task_manager

    try:
        await task_manager.update_task_status(
            task_id=task.task_id,
            status="failed",
            error_message=error_message,
            notify_dispatcher=False,
        )
    except Exception:
        logger.exception(
            "Failed to mark debate task failed",
            extra={"task_id": task.task_id},
        )
        return

    parameters = task.parameters if isinstance(task.parameters, dict) else {}
    session_id = parameters.get("session_id")
    if not session_id:
        return
    try:
        await update_session_status(str(session_id), "failed")
        await send_debate_status(str(session_id), "error")
    except Exception:
        logger.exception(
            "Failed to mark debate session failed after dispatch failure",
            extra={"task_id": task.task_id, "session_id": str(session_id)},
        )


async def _prune_dispatched_task_ids() -> None:
    """清理已结束、已开始运行或已删除任务在调度集合中的残留记录。"""
    if not _dispatched_task_ids:
        return
    async with database_module.AsyncSessionLocal() as db:
        result = await db.execute(
            select(AsyncTask.task_id).where(
                AsyncTask.task_id.in_(list(_dispatched_task_ids)),
                AsyncTask.status == "pending",
            )
        )
        pending_ids = set(result.scalars().all())
    _dispatched_task_ids.intersection_update(pending_ids)


async def dispatch_pending_debate_tasks() -> int:
    """按 FIFO 顺序启动排队中的辩论任务，直到达到运行并发上限。

    调度失败的任务会立即标记为 failed，并继续尝试后续任务，避免队首无效
    任务长期阻塞队列。

    Returns:
        本次实际提交到后台运行器的任务数量。
    """
    dispatched_count = 0
    attempted_task_ids: set[str] = set()
    async with _get_dispatch_lock():
        while True:
            await _prune_dispatched_task_ids()
            max_concurrent = (await get_runtime_settings()).ai_debate_max_concurrent
            running_count = await count_running_debate_tasks()
            available = max_concurrent - running_count - len(_dispatched_task_ids)
            if available <= 0:
                break

            pending_tasks = await _list_pending_debate_tasks(
                limit=available,
                exclude_ids=set(_dispatched_task_ids) | attempted_task_ids,
            )
            if not pending_tasks:
                break

            for task in pending_tasks:
                attempted_task_ids.add(task.task_id)
                try:
                    task_kwargs = build_debate_task_kwargs(task)
                except ValueError as exc:
                    logger.error(
                        "Failed to build debate task kwargs",
                        extra={"task_id": task.task_id, "error": str(exc)},
                    )
                    await _mark_task_failed(task, str(exc))
                    continue

                _dispatched_task_ids.add(task.task_id)
                error_message = "Failed to submit debate task to async task runner"
                try:
                    from app.tasks.async_task_runner import async_task_runner

                    submitted = async_task_runner.submit_task(
                        task_id=task.task_id,
                        task_func=run_analysis_task,
                        task_kwargs=task_kwargs,
                        respect_global_limit=False,
                    )
                except Exception as exc:
                    logger.exception(
                        "Failed to dispatch debate task",
                        extra={"task_id": task.task_id},
                    )
                    submitted = False
                    error_message = str(exc)

                if submitted:
                    dispatched_count += 1
                    logger.info("Debate task dispatched", extra={"task_id": task.task_id})
                    continue

                _dispatched_task_ids.discard(task.task_id)
                await _mark_task_failed(task, error_message)

    return dispatched_count


async def dispatch_pending_debate_tasks_safely() -> int:
    """触发一次队列调度，调度异常只记录日志，不影响调用方主流程。

    Returns:
        本次实际提交到后台运行器的任务数量；调度异常时返回 0。
    """
    try:
        return await dispatch_pending_debate_tasks()
    except Exception:
        logger.exception("Failed to dispatch pending debate tasks")
        return 0


def is_debate_task_dispatched(task_id: str) -> bool:
    """判断任务是否已提交给后台运行器但尚未进入 running。

    Args:
        task_id: 异步任务 ID。

    Returns:
        已提交给运行器时返回 True。
    """
    return task_id in _dispatched_task_ids


def schedule_debate_dispatch(*, name: str) -> None:
    """在后台触发一次辩论队列调度，并记录未处理异常。

    Args:
        name: asyncio 任务名称，便于排查调度来源。
    """

    async def _run() -> None:
        await dispatch_pending_debate_tasks_safely()

    task = asyncio.create_task(_run(), name=name)
    _background_dispatch_tasks.add(task)
    task.add_done_callback(_background_dispatch_tasks.discard)


async def notify_debate_task_status(task_id: str, status: str) -> None:
    """响应辩论任务状态变化，并在运行名额释放时继续调度队列。

    Args:
        task_id: 异步任务 ID。
        status: 更新后的任务状态。
    """
    if status == "running":
        _dispatched_task_ids.discard(task_id)
        return
    if status in DEBATE_FINISHED_STATUSES:
        _dispatched_task_ids.discard(task_id)
        await dispatch_pending_debate_tasks()


def reset_debate_dispatcher_state() -> None:
    """清空进程内调度状态，仅供测试使用。"""
    _dispatched_task_ids.clear()
    _background_dispatch_tasks.clear()
    _dispatch_locks.clear()
