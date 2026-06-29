import asyncio

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.core.request_context import get_or_create_request_id
from app.models.async_task import AsyncTask
from app.tasks.async_task_runner import async_task_runner
from app.tasks.task_functions import sync_stock_data_func
from app.tasks.task_manager import task_manager

logger = get_logger(__name__)


async def sync_stock_data_before_analysis(stock_code: str) -> bool:
    """提交并等待单股数据同步任务，供自动和手动分析启动前复用。

    Args:
        stock_code: 需要刷新数据的股票代码。

    Returns:
        同步任务可正常结束或超时后继续分析时返回 True；任务提交失败时返回 False。
    """
    from app.core.i18n import i18n_service

    sync_task_name = i18n_service.t("tasks.names.data_sync") + f" ({stock_code})"
    with SessionLocal() as db:
        sync_result = task_manager.submit_task(
            db=db,
            task_name=sync_task_name,
            task_type="db_sync",
            parameters={"stock_code": stock_code},
            allow_concurrent=False,
        )

    sync_task_id = sync_result["task_id"]

    if sync_result.get("new_task", True):
        submitted = async_task_runner.submit_task(
            task_id=sync_task_id,
            task_func=sync_stock_data_func,
            task_kwargs={
                "stock_code": stock_code,
                "task_id": sync_task_id,
                "allow_concurrent": False,
            },
            task_name=sync_task_name,
            request_id=get_or_create_request_id(),
        )
        if not submitted:
            logger.warning("Failed to submit data-sync async task for %s", stock_code)
            return False

    for _ in range(60):
        await asyncio.sleep(5)
        with SessionLocal() as db:
            task_row = (
                db.query(AsyncTask.status, AsyncTask.error_message)
                .filter(AsyncTask.task_id == sync_task_id)
                .first()
            )
        if task_row and task_row.status in ("completed", "failed"):
            if task_row.status == "completed":
                logger.info("Data sync completed for %s before analysis", stock_code)
            else:
                logger.warning(
                    "Data sync failed for %s (continuing with analysis): %s",
                    stock_code,
                    task_row.error_message,
                )
            return True

    logger.warning("Data sync for %s timed out, continuing with analysis", stock_code)
    return True
