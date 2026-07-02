import asyncio

from sqlalchemy import select

from app.core import database as database_module
from app.core.logger import get_logger
from app.models.async_task import AsyncTask
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
    sync_result = await task_manager.submit_task(
        task_name=sync_task_name,
        task_type="db_sync",
        parameters={"stock_code": stock_code},
        allow_concurrent=False,
        task_func=sync_stock_data_func,
        task_kwargs={
            "stock_code": stock_code,
            "allow_concurrent": False,
        },
    )

    sync_task_id = sync_result["task_id"]

    for _ in range(60):
        await asyncio.sleep(5)
        async with database_module.AsyncSessionLocal() as db:
            task_row = (
                await db.execute(
                    select(AsyncTask.status, AsyncTask.error_message).where(AsyncTask.task_id == sync_task_id)
                )
            ).first()
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
