from __future__ import annotations

from datetime import date
from typing import Any

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.models.account import Account
from app.performance.service import create_account_equity_snapshot
from app.tasks.scheduled_task_registry import ScheduledTask, ScheduledTaskSnapshot

logger = get_logger(__name__)

ACCOUNT_EQUITY_SNAPSHOT_JOB_ID = "account_equity_snapshot_daily"


def _today() -> date:
    """返回当前日期，便于测试替换。

    Returns:
        当前本地日期。
    """
    return date.today()


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """返回账户净值快照调度任务定义。

    Returns:
        中心调度器可加载的任务快照。
    """
    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=generate_daily_account_equity_snapshots,
                task_name="Account Equity Snapshot Daily",
                trigger_type="cron",
                job_id=ACCOUNT_EQUITY_SNAPSHOT_JOB_ID,
                trigger_args={"hour": 16, "minute": 10},
                misfire_grace_time=3600,
            )
        ],
        disabled_job_ids=[],
    )


async def generate_daily_account_equity_snapshots() -> dict[str, Any]:
    """为所有模拟账户生成当日账户净值快照。

    Returns:
        任务执行摘要。
    """
    snapshot_date = _today()
    created = 0
    failed = 0
    with SessionLocal() as db:
        accounts = db.query(Account).all()
        for account in accounts:
            try:
                create_account_equity_snapshot(db, account=account, snapshot_date=snapshot_date)
                created += 1
            except Exception:
                failed += 1
                logger.exception(
                    "failed to create account equity snapshot",
                    extra={
                        "account_id": str(account.account_id),
                        "snapshot_date": snapshot_date.isoformat(),
                    },
                )
    return {"status": "success", "created": created, "failed": failed}
