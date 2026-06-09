from typing import Any

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.tasks.scheduled_task_registry import ScheduledTask, ScheduledTaskSnapshot
from app.trading.service import trading_service

logger = get_logger(__name__)

PENDING_ORDER_MATCH_JOB_ID = "pending_order_match_scan"
PENDING_ORDER_MATCH_LIMIT = 200


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """
    返回待成交挂单撮合任务定义。

    Returns:
        中央异步调度器可加载的任务快照。
    """
    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=run_pending_order_match_scan,
                task_name="Pending Order Match Scan",
                trigger_type="interval",
                job_id=PENDING_ORDER_MATCH_JOB_ID,
                trigger_args={"minutes": 1},
                coalesce=True,
                max_instances=1,
                misfire_grace_time=60,
            )
        ],
        disabled_job_ids=[],
    )


async def run_pending_order_match_scan() -> dict[str, Any]:
    """
    扫描并撮合交易时间内满足条件的待成交限价单。

    Returns:
        本次扫描统计结果。
    """
    try:
        with SessionLocal() as db:
            result = await trading_service.match_pending_orders(db, limit=PENDING_ORDER_MATCH_LIMIT)
        if result.get("matched"):
            logger.info("Pending order match scan completed", extra={"result": result})
        return result
    except Exception as exc:
        logger.exception("Pending order match scan failed", extra={"error": str(exc)})
        return {"success": False, "error": str(exc)}
