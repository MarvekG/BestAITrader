from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.i18n import i18n_service
from app.core.runtime_settings import get_runtime_settings
from app.models.async_task import AsyncTask


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
