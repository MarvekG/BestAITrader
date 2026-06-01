"""
Async task runner for in-process background jobs.

Task functions are expected to be async whenever they do I/O. Blocking calls should stay inside the lower-level
ingestors where they already use ``run_in_executor``.
"""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
from collections.abc import Callable
from typing import Any

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.request_context import clear_request_id
from app.core.request_context import set_request_id
from app.tasks.task_manager import task_manager

logger = logging.getLogger(__name__)


class AsyncTaskRunner:
    """在应用事件循环中运行后台任务，并控制最大并发数。"""

    def __init__(self, max_concurrent_tasks: int | None = None) -> None:
        self.max_concurrent_tasks = max_concurrent_tasks or settings.ASYNC_TASK_MAX_CONCURRENT
        self._semaphore = asyncio.Semaphore(self.max_concurrent_tasks)
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._blocking_futures: dict[str, asyncio.Future[Any]] = {}

    def submit_task(
        self,
        task_id: str,
        task_func: Callable[..., Any],
        task_args: tuple = (),
        task_kwargs: dict[str, Any] | None = None,
        task_name: str | None = None,
        *,
        request_id: str,
        persist_status: bool = True,
    ) -> bool:
        """
        提交后台任务到当前事件循环执行。

        Args:
            task_id: 任务 ID。
            task_func: 任务函数，支持同步函数和协程函数。
            task_args: 任务位置参数。
            task_kwargs: 任务关键字参数。
            task_name: 可选任务展示名称，会传入任务函数以兼容现有任务签名。
            request_id: 任务执行期间绑定的请求 ID。
            persist_status: 是否把任务状态写入异步任务表。

        Returns:
            是否提交成功。
        """
        if task_kwargs is None:
            task_kwargs = {}
        else:
            task_kwargs = dict(task_kwargs)

        if task_name:
            task_kwargs["task_name"] = task_name

        try:
            task = asyncio.create_task(
                self._run_task(task_id, task_func, task_args, task_kwargs, request_id, persist_status)
            )
            self._active_tasks[task_id] = task
            task.add_done_callback(lambda completed_task: self._cleanup_task(task_id, completed_task))
            logger.info(
                "Task submitted to async task runner",
                extra={"task_id": task_id},
            )
            return True
        except Exception as exc:
            logger.error(
                "Failed to submit task to async task runner",
                extra={"task_id": task_id, "error": str(exc)},
            )
            return False

    async def _run_task(
        self,
        task_id: str,
        task_func: Callable[..., Any],
        task_args: tuple,
        task_kwargs: dict[str, Any],
        request_id: str,
        persist_status: bool,
    ) -> None:
        """
        在受限并发环境中执行任务并更新任务状态。

        Args:
            task_id: 任务 ID。
            task_func: 任务函数。
            task_args: 任务位置参数。
            task_kwargs: 任务关键字参数。
            request_id: 任务执行期间绑定的请求 ID。
            persist_status: 是否把任务状态写入异步任务表。
        """
        async with self._semaphore:
            request_token = set_request_id(request_id)
            try:
                await asyncio.to_thread(self._update_status, task_id, "running", persist_status=persist_status)
                logger.info(
                    "Task started in async task runner",
                    extra={"task_id": task_id},
                )

                if inspect.iscoroutinefunction(task_func):
                    result = task_func(*task_args, **task_kwargs)
                else:
                    result = await self._run_sync_task(task_id, task_func, task_args, task_kwargs)

                if inspect.isawaitable(result):
                    result = await result

                is_failed, error_message = self._resolve_failure(result)
                if is_failed:
                    await asyncio.to_thread(
                        self._update_status,
                        task_id,
                        "failed",
                        result,
                        error_message,
                        persist_status=persist_status,
                    )
                    logger.warning(
                        "Task failed with soft failure",
                        extra={"task_id": task_id, "error_message": error_message},
                    )
                    return

                await asyncio.to_thread(
                    self._update_status,
                    task_id,
                    "completed",
                    result,
                    persist_status=persist_status,
                )
                logger.info(
                    "Task completed successfully",
                    extra={"task_id": task_id},
                )
            except Exception as exc:
                logger.error(
                    "Task failed with exception",
                    extra={"task_id": task_id, "error": str(exc)},
                    exc_info=True,
                )
                await asyncio.to_thread(
                    self._update_status,
                    task_id,
                    "failed",
                    None,
                    str(exc),
                    persist_status=persist_status,
                )
            finally:
                clear_request_id(request_token)

    def get_active_task_count(self) -> int:
        """
        获取当前仍在执行或排队的任务数量。

        Returns:
            活跃任务数量。
        """
        self._cleanup_finished_tasks()
        return len(self._active_tasks)

    async def wait_for_all(self) -> None:
        """等待当前已提交的所有任务完成，主要供测试使用。"""
        tasks = list(self._active_tasks.values())
        if tasks:
            await asyncio.gather(*tasks)

    async def stop_all(self) -> None:
        """取消所有尚未完成的后台任务并等待取消投递完成。"""
        active_items = list(self._active_tasks.items())
        tasks_to_cancel: list[asyncio.Task[None]] = []
        for task_id, task in active_items:
            if task and not task.done():
                task.cancel()
                tasks_to_cancel.append(task)
                self._update_status(
                    task_id,
                    "failed",
                    error_message="System reload or shutdown, task cancelled",
                )
        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        blocking_futures = list(self._blocking_futures.values())
        if blocking_futures:
            await asyncio.gather(*blocking_futures, return_exceptions=True)
        self._cleanup_finished_tasks()

    def _cleanup_task(self, task_id: str, completed_task: asyncio.Task[None]) -> None:
        """
        从活跃任务表移除已完成任务，并读取异常避免事件循环报警。

        Args:
            task_id: 任务 ID。
            completed_task: 已完成的 asyncio 任务对象。
        """
        self._active_tasks.pop(task_id, None)
        if completed_task.cancelled():
            return
        try:
            completed_task.exception()
        except Exception:
            logger.exception(
                "Failed to inspect completed task",
                extra={"task_id": task_id},
            )

    def _cleanup_finished_tasks(self) -> None:
        """清理已完成的任务引用。"""
        for task_id, task in list(self._active_tasks.items()):
            if task.done():
                self._cleanup_task(task_id, task)

    async def _run_sync_task(
        self,
        task_id: str,
        task_func: Callable[..., Any],
        task_args: tuple,
        task_kwargs: dict[str, Any],
    ) -> Any:
        """
        在线程中执行同步任务函数，并跟踪底层 Future 以支持有序关闭。

        Args:
            task_id: 任务 ID。
            task_func: 同步任务函数。
            task_args: 任务位置参数。
            task_kwargs: 任务关键字参数。

        Returns:
            同步任务函数返回值。
        """
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        future = loop.run_in_executor(None, lambda: context.run(task_func, *task_args, **task_kwargs))
        self._blocking_futures[task_id] = future

        def _cleanup_blocking_future(_future: asyncio.Future[Any]) -> None:
            self._blocking_futures.pop(task_id, None)

        future.add_done_callback(_cleanup_blocking_future)
        return await asyncio.shield(future)

    def _update_status(
        self,
        task_id: str,
        status: str,
        result: Any | None = None,
        error_message: str | None = None,
        persist_status: bool = True,
    ) -> None:
        """
        更新任务状态，兼容没有任务表记录的调度器任务。

        Args:
            task_id: 任务 ID。
            status: 新状态。
            result: 可选任务结果。
            error_message: 可选错误信息。
            persist_status: 是否把任务状态写入异步任务表。
        """
        if not persist_status:
            return

        with SessionLocal() as db:
            task_manager.update_task_status(
                db=db,
                task_id=task_id,
                status=status,
                result=result,
                error_message=error_message,
            )
            db.commit()

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


async_task_runner = AsyncTaskRunner()
