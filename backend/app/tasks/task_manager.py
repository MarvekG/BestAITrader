from datetime import datetime
import json
import logging
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.core.request_context import get_current_user_id
from app.models.async_task import AsyncTask

logger = logging.getLogger(__name__)


class TaskManager:
    """Async task manager

    Responsible for task submission, status query, and concurrency control
    """

    def __init__(self):
        pass  # Redis connection not needed anymore

    def _check_running_task(
        self, db: Session, task_type: str, parameters: Dict[str, Any], user_id: int | None = None
    ) -> Optional[AsyncTask]:
        """Check if there is a running task with same type and parameters"""
        # Query for running or pending tasks of the same type
        params_str = json.dumps(parameters, sort_keys=True)

        running_query = db.query(AsyncTask).filter(
            AsyncTask.task_type == task_type,
            AsyncTask.status.in_(["pending", "running"])
        )
        if user_id is not None:
            running_query = running_query.filter(AsyncTask.user_id == user_id)
        else:
            running_query = running_query.filter(AsyncTask.user_id.is_(None))

        running_tasks = running_query.all()

        # Iterate through all running tasks to check parameters
        for task in running_tasks:
            existing_params_str = json.dumps(
                task.parameters or {}, sort_keys=True)
            if existing_params_str == params_str:
                return task

        return None

    def submit_task(
        self,
        db: Session,
        task_name: str,
        task_type: str,
        parameters: Dict[str, Any],
        allow_concurrent: bool = True,
        celery_task_id: Optional[str] = None,
        user_id: int | None = None
    ) -> Dict[str, Any]:
        """Submit an async task

        Args:
            db: Database session
            task_name: Task name
            task_type: Task type
            parameters: Task parameters
            allow_concurrent: Whether to allow concurrent execution
            celery_task_id: Celery task ID (if already created)
            user_id: Optional owner user ID. Defaults to authenticated request context.

        Returns:
            Dictionary with task_id, task_name, status, and message
        """
        try:
            owner_user_id = user_id if user_id is not None else get_current_user_id()

            # Check if concurrent execution is allowed
            if not allow_concurrent:
                # Check for existing running task
                existing_task = self._check_running_task(
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
            db.commit()
            db.refresh(task)

            logger.info(f"Task submitted successfully: {task.task_id}")

            from app.core.i18n import i18n_service
            msg = i18n_service.t("tasks.submission_success").format(
                task_id=task.task_id)

            return {
                "task_id": task.task_id,
                "task_name": task.task_name,
                "status": task.status,
                "message": msg,
                "new_task": True
            }

        except Exception as e:
            logger.error(f"Failed to submit task: {e}")
            db.rollback()
            raise

    def update_task_status(
        self,
        db: Session,
        task_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        notification_result: Optional[Dict[str, Any]] = None,
    ):
        """Update task status

        Args:
            db: Database session
            task_id: Task ID
            status: New status (pending/running/completed/failed)
            result: Task result
            error_message: Error message (if failed)
            notification_result: Optional smaller result payload for real-time notifications.
        """
        task = db.query(AsyncTask).filter(AsyncTask.task_id == task_id).first()
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

        db.commit()
        logger.info(f"Task {task_id} status updated to {status}")

        # Publish task notification to Redis for real-time WebSocket updates
        try:
            import redis
            import json
            from app.core.config import settings

            # Using synchronous Redis client since this runs within
            # synchronous SQLAlchemy context
            r = redis.from_url(settings.REDIS_URL,
                               encoding="utf-8", decode_responses=True)
            message = {
                "task_id": task_id,
                "task_name": task.task_name,
                "status": status,
                "result": notification_result if notification_result is not None else result,
                "error_message": error_message,
                "timestamp": datetime.now().isoformat()
            }
            r.publish("task_notifications", json.dumps(message))
            r.close()
        except Exception as e:
            logger.error(f"Failed to publish task update to Redis: {e}")

    def get_task_status(
        self, db: Session, task_id: str, user_id: int | None = None
    ) -> Optional[Dict[str, Any]]:
        """Get task status

        Args:
            db: Database session
            task_id: Task ID
            user_id: Optional owner user ID filter.

        Returns:
            Task information dictionary or None
        """
        query = db.query(AsyncTask).filter(AsyncTask.task_id == task_id)
        if user_id is not None:
            query = query.filter(AsyncTask.user_id == user_id)

        task = query.first()
        if not task:
            return None

        return task.to_dict()

    def cleanup_zombie_tasks(self, db: Session):
        """Cleanup zombie tasks that are still running after restart"""
        try:
            # Find all tasks with running status
            zombie_tasks = db.query(AsyncTask).filter(
                AsyncTask.status == "running"
            ).all()

            count = 0
            for task in zombie_tasks:
                task.status = "failed"
                task.error_message = "Task interrupted by server restart"
                task.completed_at = datetime.now()
                count += 1

            if count > 0:
                db.commit()
                logger.warning(f"Cleaned up {count} zombie tasks")

        except Exception as e:
            logger.error(f"Failed to cleanup zombie tasks: {e}")
            db.rollback()


# Global task manager instance
task_manager = TaskManager()
