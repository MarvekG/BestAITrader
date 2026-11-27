from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from sqlalchemy import select

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
        run = (
            await db.execute(
                select(ExperienceReviewRun)
                .where(ExperienceReviewRun.status == "queued")
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
        await db.commit()
        return run.review_run_id


class ExperienceReviewQueue:
    """进程内经验复盘 worker 池。"""

    def __init__(self) -> None:
        self._workers: list[asyncio.Task[None]] = []
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._started = False

    @property
    def concurrency(self) -> int:
        """返回全局 worker 并发数。"""
        return max(1, int(settings.EXPERIENCE_REVIEW_MAX_CONCURRENT))

    async def start(self) -> None:
        """启动固定数量的经验复盘 worker。"""
        if self._started:
            return
        self._started = True
        self._stop_event.clear()
        self._wake_event.clear()
        self._workers = [
            asyncio.create_task(self._worker_loop(index), name=f"experience-review-worker-{index}")
            for index in range(self.concurrency)
        ]
        logger.info("Experience review queue started: concurrency=%s", self.concurrency)

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
