from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.i18n import i18n_service
from app.core.runtime_settings import get_runtime_settings
from app.models.async_task import AsyncTask


def format_ai_analysis_task_name(stock_code: str) -> str:
    """生成 AI 分析任务名。

    Args:
        stock_code: 标准股票代码。

    Returns:
        统一的 AI 分析任务名。
    """
    return f"AI Analysis - {stock_code}"


@dataclass(frozen=True)
class DebateConcurrencyLimitReached(Exception):
    """AI 投研辩论并发达到上限。"""

    running_count: int
    max_concurrent: int

    def __str__(self) -> str:
        """返回可展示给前端的国际化错误消息。

        Returns:
            根据当前系统语言生成的并发超限提示。
        """
        return format_debate_concurrency_limit_message(
            running_count=self.running_count,
            max_concurrent=self.max_concurrent,
        )


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


def count_running_debate_tasks(db: Session) -> int:
    """统计当前待执行或运行中的 AI 投研辩论任务数量。

    Args:
        db: 数据库会话。

    Returns:
        当前全局 pending/running 的 ai_analysis 任务数。
    """
    return db.query(AsyncTask).filter(
        AsyncTask.task_type == "ai_analysis",
        AsyncTask.status.in_(["pending", "running"]),
    ).count()


def find_running_debate_task_for_session(db: Session, session_id: str) -> AsyncTask | None:
    """查找指定会话正在排队或运行的 AI 投研辩论任务。

    Args:
        db: 数据库会话。
        session_id: 辩论会话 ID。

    Returns:
        匹配的异步任务；没有则返回 None。
    """
    running_tasks = db.query(AsyncTask).filter(
        AsyncTask.task_type == "ai_analysis",
        AsyncTask.status.in_(["pending", "running"]),
    ).all()
    for task in running_tasks:
        parameters = task.parameters if isinstance(task.parameters, dict) else {}
        if parameters.get("session_id") == session_id:
            return task
    return None


def find_running_debate_task_for_stock(db: Session, stock_code: str) -> AsyncTask | None:
    """查找指定股票正在排队或运行的 AI 投研辩论任务。

    Args:
        db: 数据库会话。
        stock_code: 标准股票代码。

    Returns:
        匹配的异步任务；没有则返回 None。
    """
    task_name = format_ai_analysis_task_name(stock_code)
    running_tasks = db.query(AsyncTask).filter(
        AsyncTask.task_type == "ai_analysis",
        AsyncTask.status.in_(["pending", "running"]),
    ).all()
    for task in running_tasks:
        parameters = task.parameters if isinstance(task.parameters, dict) else {}
        if task.task_name == task_name or parameters.get("stock_code") == stock_code:
            return task
    return None


def format_debate_concurrency_limit_message(*, running_count: int, max_concurrent: int) -> str:
    """生成 AI 投研辩论并发超限的国际化消息。

    Args:
        running_count: 当前待执行或运行中的辩论任务数。
        max_concurrent: 系统允许的最大并发数。

    Returns:
        可返回给前端展示的国际化消息。
    """
    return i18n_service.t(
        "tasks.ai_debate_concurrency_limit_reached",
        running_count=running_count,
        max_concurrent=max_concurrent,
    )


def ensure_debate_concurrency_available(db: Session) -> None:
    """校验 AI 投研辩论全局并发是否仍有余量。

    Args:
        db: 数据库会话。

    Raises:
        DebateConcurrencyLimitReached: 当前并发任务数已达到系统设置上限时抛出。
    """
    max_concurrent = get_runtime_settings(db).ai_debate_max_concurrent
    running_count = count_running_debate_tasks(db)
    if running_count >= max_concurrent:
        raise DebateConcurrencyLimitReached(
            running_count=running_count,
            max_concurrent=max_concurrent,
        )


def ensure_debate_launch_available(db: Session, stock_code: str) -> None:
    """校验指定股票可启动新的 AI 投研辩论任务。

    规则：同一股票全系统只允许一个 pending/running 的 AI 分析任务；
    全系统 AI 分析任务总并发数按运行时配置限制。

    Args:
        db: 数据库会话。
        stock_code: 标准股票代码。

    Raises:
        DebateStockTaskAlreadyRunning: 指定股票已有运行中任务。
        DebateConcurrencyLimitReached: 全局并发数已达到系统设置上限。
    """
    existing_task = find_running_debate_task_for_stock(db, stock_code)
    if existing_task:
        parameters = existing_task.parameters if isinstance(existing_task.parameters, dict) else {}
        raise DebateStockTaskAlreadyRunning(
            stock_code=stock_code,
            task_id=existing_task.task_id,
            session_id=parameters.get("session_id"),
        )
    ensure_debate_concurrency_available(db)
