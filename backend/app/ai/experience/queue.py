from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from app.core import database as database_module
from app.core.config import settings
from app.core.logger import get_logger
from app.models.experience_review_run import ExperienceReviewRun

logger = get_logger(__name__)

EXPERIENCE_REVIEW_QUEUE_POLL_SECONDS = 5.0


async def claim_next_queued_review_run() -> Optional[str]:
    """按 FIFO 原子领取一个排队中的经验复盘任务。

    Returns:
        被领取的 review_run_id；没有排队任务时返回 None。
    """
    async with database_module.AsyncSessionLocal() as db:
        running_run = aliased(ExperienceReviewRun)
        same_stock_running = exists().where(
            running_run.user_id == ExperienceReviewRun.user_id,
            running_run.stock_code == ExperienceReviewRun.stock_code,
            running_run.status == "running",
        )
        run = (
            await db.execute(
                select(ExperienceReviewRun)
                .where(
                    ExperienceReviewRun.status == "queued",
                    ~same_stock_running,
                )
                .order_by(ExperienceReviewRun.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).scalars().first()
        if run is None:
            return None

        run.status = "running"
        run.started_at = datetime.now()
        run.attempt_count = int(run.attempt_count or 0) + 1
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return None
        return run.review_run_id


class ExperienceReviewQueue:
    """进程内经验复盘 worker 池。"""

    def __init__(self) -> None:
        self._workers: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._started = False
        self._concurrency = self._normalize_concurrency(settings.EXPERIENCE_REVIEW_MAX_CONCURRENT)

    @staticmethod
    def _normalize_concurrency(value: int | None) -> int:
        return max(1, min(20, int(value or 1)))

    @property
    def concurrency(self) -> int:
        """返回全局 worker 并发数。"""
        return self._concurrency

    async def start(self, concurrency: int | None = None) -> None:
        """启动经验复盘 worker 池。

        Args:
            concurrency: 目标并发数；未提供时使用上次设置或环境默认值。
        """
        async with self._lock:
            target = self._normalize_concurrency(
                concurrency if concurrency is not None else self._concurrency
            )
            if self._started:
                self._apply_concurrency_locked(target)
                return
            self._started = True
            self._stop_event.clear()
            self._wake_event.clear()
            self._concurrency = target
            self._workers = [
                asyncio.create_task(
                    self._worker_loop(index),
                    name=f"experience-review-worker-{index}",
                )
                for index in range(target)
            ]
        logger.info("Experience review queue started: concurrency=%s", target)

    async def set_concurrency(self, concurrency: int) -> None:
        """动态调整 worker 并发数。

        增加时立即补充 worker；减少时多余 worker 在完成当前任务后退出。

        Args:
            concurrency: 目标并发数。
        """
        async with self._lock:
            target = self._normalize_concurrency(concurrency)
            if not self._started:
                self._concurrency = target
                return
            self._apply_concurrency_locked(target)
        logger.info("Experience review queue concurrency updated: %s", target)

    def _apply_concurrency_locked(self, target: int) -> None:
        current = self._concurrency
        if target == current:
            return
        self._concurrency = target
        self._wake_event.set()
        if target > current:
            self._workers.extend(
                asyncio.create_task(
                    self._worker_loop(index),
                    name=f"experience-review-worker-{index}",
                )
                for index in range(current, target)
            )
        self._workers = [task for task in self._workers if not task.done()]

    async def stop(self) -> None:
        """停止 worker 池并取消仍在等待或执行的 worker。"""
        if not self._started:
            return
        self._started = False
        self._stop_event.set()
        self._wake_event.set()
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []
        logger.info("Experience review queue stopped")

    def notify(self) -> None:
        """唤醒空闲 worker 检查队列。"""
        self._wake_event.set()

    async def _worker_loop(self, worker_index: int) -> None:
        while not self._stop_event.is_set():
            if worker_index >= self._concurrency:
                return
            try:
                review_run_id = await claim_next_queued_review_run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Experience review worker failed to claim a queued run")
                review_run_id = None

            if review_run_id:
                try:
                    from app.ai.experience.service import experience_service

                    await experience_service.execute_queued_review(review_run_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "Experience review worker failed: worker=%s review_run_id=%s",
                        worker_index,
                        review_run_id,
                    )
                continue

            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=EXPERIENCE_REVIEW_QUEUE_POLL_SECONDS,
                )
            except asyncio.TimeoutError:
                pass
            self._wake_event.clear()


experience_review_queue = ExperienceReviewQueue()
