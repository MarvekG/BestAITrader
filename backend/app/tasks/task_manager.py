from datetime import datetime
import json
import logging
from collections.abc import Callable
from typing import Any, Dict, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database as database_module
from app.core.request_context import get_current_user_id
from app.models.async_task import AsyncTask

logger = logging.getLogger(__name__)


class TaskManager:
    """Async task manager

    Responsible for task submission, status query, and concurrency control
    """

    def __init__(self):
        pass  # Redis connection not needed anymore

    async def _check_running_task(
        self, db: AsyncSession, task_type: str, parameters: Dict[str, Any], user_id: int | None = None
    ) -> Optional[AsyncTask]:
        """异步检查是否存在相同类型和参数的运行中任务。"""
        # Query for running or pending tasks of the same type
        params_str = json.dumps(parameters, sort_keys=True)

        filters = [
            AsyncTask.task_type == task_type,
            AsyncTask.status.in_(["pending", "running"])
        ]
        if user_id is not None:
            filters.append(AsyncTask.user_id == user_id)
        else:
            filters.append(AsyncTask.user_id.is_(None))

        result = await db.execute(select(AsyncTask).where(*filters))
        running_tasks = result.scalars().all()

        # Iterate through all running tasks to check parameters
        for task in running_tasks:
            existing_params_str = json.dumps(
                task.parameters or {}, sort_keys=True)
            if existing_params_str == params_str:
                return task

        return None

    async def submit_task(
        self,
        task_name: str,
        task_type: str,
        parameters: Dict[str, Any],
        allow_concurrent: bool = True,
        celery_task_id: Optional[str] = None,
        user_id: int | None = None,
        task_func: Callable[..., Any] | None = None,
        task_args: tuple = (),
        task_kwargs: dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """异步提交任务并可直接投递到后台 runner。

        Args:
            task_name: Task name
            task_type: Task type
            parameters: Task parameters
            allow_concurrent: Whether to allow concurrent execution
            celery_task_id: Celery task ID (if already created)
            user_id: Optional owner user ID. Defaults to authenticated request context.
            task_func: Optional function submitted to AsyncTaskRunner when a new task is created.
            task_args: Positional args for task_func.
            task_kwargs: Keyword args for task_func.

        Returns:
            Dictionary with task_id, task_name, status, and message
        """
        owner_user_id = user_id if user_id is not None else get_current_user_id()
        async with database_module.AsyncSessionLocal() as db:
            try:
                # Check if concurrent execution is allowed
                if not allow_concurrent:
                    # Check for existing running task
                    existing_task = await self._check_running_task(
                        db, task_type, parameters, owner_user_id)
                    if existing_task:
                        from app.core.i18n import i18n_service
                        msg = i18n_service.t("tasks.already_in_progress").format(
                            task_id=existing_task.task_id)

                        logger.info(
                            f"Task already running: {existing_task.task_id}")
                        return {
                            "task_id": existing_task.task_id,
                            "task_name": existing_task.task_name,
                            "status": existing_task.status,
                            "message": msg,
                            "new_task": False
                        }

                # Create new task record
                task = AsyncTask(
                    task_id=celery_task_id if celery_task_id else None,
                    task_name=task_name,
                    task_type=task_type,
                    status="pending",
                    allow_concurrent=allow_concurrent,
                    parameters=parameters,
                    user_id=owner_user_id
                )

                db.add(task)
                await db.commit()
                await db.refresh(task)

                logger.info(f"Task submitted successfully: {task.task_id}")

                from app.core.i18n import i18n_service
                msg = i18n_service.t("tasks.submission_success").format(
                    task_id=task.task_id)

                task_info = {
                    "task_id": task.task_id,
                    "task_name": task.task_name,
                    "status": task.status,
                    "message": msg,
                    "new_task": True
                }

            except Exception as e:
                logger.error(f"Failed to submit task: {e}")
                await db.rollback()
                raise

        if task_func is not None:
            from app.tasks.async_task_runner import async_task_runner

            final_task_kwargs = dict(task_kwargs or {})
            final_task_kwargs.setdefault("task_id", task_info["task_id"])
            final_task_kwargs.setdefault("task_name", task_name)
            success = async_task_runner.submit_task(
                task_id=task_info["task_id"],
                task_func=task_func,
                task_args=task_args,
                task_kwargs=final_task_kwargs,
            )
            if not success:
                await self.update_task_status(
                    task_id=task_info["task_id"],
                    status="failed",
                    error_message="Failed to submit task to async task runner",
                )
                raise RuntimeError("Failed to submit task to async task runner")

        return task_info

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        notification_result: Optional[Dict[str, Any]] = None,
    ) -> None:
        """异步更新任务状态并发布通知。

        Args:
            task_id: Task ID
            status: New status (pending/running/completed/failed)
            result: Task result
            error_message: Error message (if failed)
            notification_result: Optional smaller result payload for real-time notifications.
        """
        async with database_module.AsyncSessionLocal() as db:
            query_result = await db.execute(select(AsyncTask).where(AsyncTask.task_id == task_id))
            task = query_result.scalar_one_or_none()
            if not task:
                logger.warning(f"Task not found: {task_id}")
                return

            task.status = status

            if status == "running" and not task.started_at:
                task.started_at = datetime.now()

            if status in ["completed", "failed"]:
                task.completed_at = datetime.now()

            if result:
                task.result = result

            if error_message:
                task.error_message = error_message

            task_name = task.task_name
            task_user_id = task.user_id
            await db.commit()
        logger.info(f"Task {task_id} status updated to {status}")

        try:
            from app.core.redis_client import redis_client

            message = {
                "task_id": task_id,
                "task_name": task_name,
                "status": status,
                "user_id": task_user_id,
                "result": notification_result if notification_result is not None else result,
                "error_message": error_message,
                "timestamp": datetime.now().isoformat()
            }
            await redis_client.publish("task_notifications", json.dumps(message))
        except Exception as e:
            logger.error(f"Failed to publish task update to Redis: {e}")

    async def get_task_status(
        self, task_id: str, user_id: int | None = None
    ) -> Optional[Dict[str, Any]]:
        """Get task status

        Args:
            task_id: Task ID
            user_id: Optional owner user ID filter.

        Returns:
            Task information dictionary or None
        """
        filters = [AsyncTask.task_id == task_id]
        if user_id is not None:
            filters.append(AsyncTask.user_id == user_id)

        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(select(AsyncTask).where(*filters))
            task = result.scalar_one_or_none()
            if not task:
                return None

            return task.to_dict()

    async def get_task_list(
        self,
        *,
        user_id: int,
        status: str | None,
        task_type: str | None,
        limit: int,
        skip: int,
    ) -> dict[str, Any]:
        """异步查询指定用户的任务列表。"""
        filters = [AsyncTask.user_id == user_id]
        if status:
            filters.append(AsyncTask.status == status)
        if task_type:
            filters.append(AsyncTask.task_type == task_type)
        async with database_module.AsyncSessionLocal() as db:
            total_result = await db.execute(select(func.count()).select_from(AsyncTask).where(*filters))
            tasks_result = await db.execute(
                select(AsyncTask)
                .where(*filters)
                .order_by(AsyncTask.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            return {
                "total": total_result.scalar_one(),
                "items": [task.to_dict() for task in tasks_result.scalars().all()],
                "limit": limit,
                "skip": skip,
            }

    async def clear_tasks(self, *, user_id: int, task_type: str) -> int:
        """异步清空指定用户的指定类型任务。"""
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                delete(AsyncTask).where(
                    AsyncTask.user_id == user_id,
                    AsyncTask.task_type == task_type,
                )
            )
            await db.commit()
            return int(result.rowcount or 0)

    async def delete_task(self, *, user_id: int, task_id: str) -> bool:
        """异步删除指定用户拥有的任务。"""
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(AsyncTask).where(
                    AsyncTask.task_id == task_id,
                    AsyncTask.user_id == user_id,
                )
            )
            task = result.scalar_one_or_none()
            if task is None:
                return False
            await db.delete(task)
            await db.commit()
            return True

    async def cleanup_zombie_tasks(self) -> int:
        """异步清理服务重启前遗留的运行中任务和陈旧待运行任务。

        Returns:
            被标记为失败的任务数量。
        """
        now = datetime.now()
        async with database_module.AsyncSessionLocal() as db:
            result = await db.execute(
                select(AsyncTask).where(
                    AsyncTask.status.in_(["pending", "running"])
                )
            )
            zombie_tasks = result.scalars().all()
            for task in zombie_tasks:
                task.status = "failed"
                task.error_message = "Task interrupted by server restart"
                task.completed_at = now
            if zombie_tasks:
                await db.commit()
            return len(zombie_tasks)


# Global task manager instance
task_manager = TaskManager()
