"""备用的进程任务执行器。"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import logging.config
import multiprocessing
import os
from collections.abc import Callable
from typing import Any

from app.core.database import SessionLocal
from app.core.request_context import clear_request_id
from app.core.request_context import set_request_id
from app.tasks.task_manager import task_manager

logger = logging.getLogger(__name__)


class ProcessTaskExecutor:
    """使用独立进程执行任务的备用执行器。"""

    def __init__(self) -> None:
        """初始化进程执行器的活跃进程表。"""
        self.active_processes: dict[str, multiprocessing.Process] = {}

    def submit_task(
        self,
        task_id: str,
        task_func: Callable[..., Any],
        task_args: tuple = (),
        task_kwargs: dict[str, Any] | None = None,
        task_name: str | None = None,
        *,
        request_id: str,
    ) -> bool:
        """
        提交任务到独立进程执行。

        Args:
            task_id: 任务 ID。
            task_func: 任务函数。
            task_args: 任务位置参数。
            task_kwargs: 任务关键字参数。
            task_name: 可选任务展示名称，会传入任务函数以兼容现有任务签名。
            request_id: 子进程执行期间绑定的请求 ID。

        Returns:
            是否提交成功。
        """
        final_kwargs = dict(task_kwargs or {})
        if task_name:
            final_kwargs["task_name"] = task_name

        try:
            process = multiprocessing.Process(
                target=self._run_task_in_process,
                args=(task_id, task_func, task_args, final_kwargs, request_id),
                daemon=False,
            )
            process.start()
            self.active_processes[task_id] = process
            logger.info(
                "Task submitted to process executor",
                extra={"task_id": task_id, "process_pid": process.pid},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to submit task to process executor",
                extra={"task_id": task_id, "error": str(exc)},
            )
            return False

    @staticmethod
    def _run_task_in_process(
        task_id: str,
        task_func: Callable[..., Any],
        task_args: tuple,
        task_kwargs: dict[str, Any],
        request_id: str,
    ) -> None:
        """
        在独立子进程中执行任务并更新任务状态。

        Args:
            task_id: 任务 ID。
            task_func: 任务函数。
            task_args: 任务位置参数。
            task_kwargs: 任务关键字参数。
            request_id: 子进程执行期间绑定的请求 ID。
        """
        request_token = set_request_id(request_id)
        ProcessTaskExecutor._dispose_inherited_database_engine(task_id)
        ProcessTaskExecutor._configure_child_logging(task_id)

        try:
            with SessionLocal() as db:
                task_manager.update_task_status(db=db, task_id=task_id, status="running")
                db.commit()

                logger.info(
                    "Task started in process executor",
                    extra={"task_id": task_id, "process_pid": multiprocessing.current_process().pid},
                )

                result = task_func(*task_args, **task_kwargs)
                if inspect.isawaitable(result):
                    result = asyncio.run(ProcessTaskExecutor._run_async_result(result))

                is_failed, error_message = ProcessTaskExecutor._resolve_failure(result)
                if is_failed:
                    task_manager.update_task_status(
                        db=db,
                        task_id=task_id,
                        status="failed",
                        result=result,
                        error_message=error_message,
                    )
                    db.commit()
                    logger.warning(
                        "Task failed with soft failure in process executor",
                        extra={"task_id": task_id, "error_message": error_message},
                    )
                    return

                task_manager.update_task_status(db=db, task_id=task_id, status="completed", result=result)
                db.commit()
                logger.info("Task completed successfully in process executor", extra={"task_id": task_id})
        except Exception as exc:
            logger.error(
                "Task failed with exception in process executor",
                extra={"task_id": task_id, "error": str(exc)},
                exc_info=True,
            )
            ProcessTaskExecutor._mark_task_failed(task_id, str(exc))
        finally:
            clear_request_id(request_token)

    def cleanup_finished_processes(self) -> None:
        """清理已完成的子进程引用。"""
        finished_task_ids = []
        for task_id, process in self.active_processes.items():
            if not process.is_alive():
                process.join(timeout=0.1)
                finished_task_ids.append(task_id)

        for task_id in finished_task_ids:
            del self.active_processes[task_id]
            logger.info("Cleaned up finished process task", extra={"task_id": task_id})

    def get_active_task_count(self) -> int:
        """
        获取仍在运行的进程任务数量。

        Returns:
            活跃进程任务数量。
        """
        self.cleanup_finished_processes()
        return len(self.active_processes)

    def stop_all(self) -> None:
        """强制停止所有仍在运行的子进程任务。"""
        if not self.active_processes:
            return

        logger.info("Stopping all process executor tasks", extra={"active_count": len(self.active_processes)})
        task_ids = list(self.active_processes.keys())

        with SessionLocal() as db:
            for task_id in task_ids:
                process = self.active_processes.get(task_id)
                if process and process.is_alive():
                    try:
                        process.kill()
                        process.join(timeout=1.0)
                        task_manager.update_task_status(
                            db=db,
                            task_id=task_id,
                            status="failed",
                            error_message="System reload or shutdown, task forcefully stopped",
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to stop process executor task",
                            extra={"task_id": task_id, "error": str(exc)},
                        )

                self.active_processes.pop(task_id, None)
            db.commit()
        logger.info("All process executor tasks stopped")

    @staticmethod
    async def _run_async_result(result: Any) -> Any:
        """
        在子进程内执行协程结果，并维护 Redis 连接生命周期。

        Args:
            result: 任务函数返回的 awaitable 对象。

        Returns:
            awaitable 的执行结果。
        """
        from app.core.redis_client import redis_client

        try:
            await redis_client.init_pool()
            return await result
        finally:
            await redis_client.close()

    @staticmethod
    def _dispose_inherited_database_engine(task_id: str) -> None:
        """
        释放 fork 继承的数据库连接池，避免子进程复用父进程连接。

        Args:
            task_id: 任务 ID。
        """
        try:
            from app.core.database import engine as core_engine

            core_engine.dispose()
        except Exception as exc:
            logger.exception(
                "Failed to dispose inherited database engine in child process",
                extra={"task_id": task_id, "error": str(exc)},
            )

    @staticmethod
    def _configure_child_logging(task_id: str) -> None:
        """
        在子进程内重新配置日志系统。

        Args:
            task_id: 任务 ID。
        """
        try:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                "config",
                "log_config.json",
            )
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as config_file:
                    config = json.load(config_file)
                logging.config.dictConfig(config)
                return
            logging.basicConfig(level=logging.INFO)
        except Exception as exc:
            logging.basicConfig(level=logging.INFO)
            logger.exception(
                "Failed to configure logging in child process",
                extra={"task_id": task_id, "error": str(exc)},
            )

    @staticmethod
    def _mark_task_failed(task_id: str, error_message: str) -> None:
        """
        将任务标记为失败。

        Args:
            task_id: 任务 ID。
            error_message: 错误信息。
        """
        with SessionLocal() as db_err:
            try:
                task_manager.update_task_status(
                    db=db_err,
                    task_id=task_id,
                    status="failed",
                    error_message=error_message,
                )
                db_err.commit()
            except Exception as exc:
                logger.error(
                    "Failed to update failed process task status",
                    extra={"task_id": task_id, "error": str(exc)},
                )

    @staticmethod
    def _resolve_failure(result: Any) -> tuple[bool, str | None]:
        """
        解析任务返回值中的软失败状态。

        Args:
            result: 任务返回值。

        Returns:
            是否失败以及错误信息。
        """
        if isinstance(result, dict) and result.get("status") == "failed":
            return True, result.get("error", result.get("message", "Task failed"))
        return False, None


process_executor = ProcessTaskExecutor()
