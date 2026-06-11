from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytz
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.tasks.scheduled_task_registry import ScheduledTask, ScheduledTaskSnapshot
from app.trading.discipline_service import scan_position_disciplines
from app.trading.discipline_settings import MIN_DISCIPLINE_SCAN_INTERVAL_SECONDS
from app.trading.discipline_settings import get_position_discipline_settings

logger = get_logger(__name__)

POSITION_DISCIPLINE_SCAN_JOB_ID = "position_discipline_auto_scan"
POSITION_DISCIPLINE_TICK_SECONDS = MIN_DISCIPLINE_SCAN_INTERVAL_SECONDS
last_scan_at_by_user: dict[int, datetime] = {}
running_user_ids: set[int] = set()


def _shanghai_now() -> datetime:
    """返回上海时区本地无时区时间。"""
    timezone = pytz.timezone("Asia/Shanghai")
    return datetime.now(timezone).replace(tzinfo=None)


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """返回止损止盈独立扫描任务定义。"""
    return ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=run_due_scans,
                task_name="Position Discipline Auto Scan",
                trigger_type="interval",
                job_id=POSITION_DISCIPLINE_SCAN_JOB_ID,
                trigger_args={"seconds": POSITION_DISCIPLINE_TICK_SECONDS},
                misfire_grace_time=POSITION_DISCIPLINE_TICK_SECONDS,
            )
        ],
        disabled_job_ids=[],
    )


async def run_due_scans() -> dict[str, Any]:
    """执行已到期的用户止损止盈扫描。"""
    now = _shanghai_now()
    scanned: list[dict[str, Any]] = []
    skipped = 0
    with SessionLocal() as db:
        user_ids = _load_active_user_ids(db)

    for user_id in user_ids:
        settings = get_position_discipline_settings(user_id)
        if not settings.enabled:
            skipped += 1
            continue
        if not _is_due(user_id, settings.scan_interval_seconds, now):
            skipped += 1
            continue
        if user_id in running_user_ids:
            skipped += 1
            continue

        last_scan_at_by_user[user_id] = now
        running_user_ids.add(user_id)
        try:
            result = await scan_position_disciplines(
                user_id,
                settings=settings,
                now=now,
                debate_launcher=_launch_debate_task,
            )
            scanned.append({"user_id": user_id, "status": result.get("status"), "triggered_count": len(result.get("triggered", []))})
        except Exception:
            logger.exception("Position discipline scheduled scan failed", extra={"user_id": user_id})
        finally:
            running_user_ids.discard(user_id)

    return {"scanned": len(scanned), "skipped": skipped, "items": scanned}


def _load_active_user_ids(db: Session) -> list[int]:
    """读取可执行扫描的活跃用户 ID。"""
    from app.models.user import User

    rows = db.query(User.id).filter(User.is_active.is_(True)).order_by(User.id.asc()).all()
    return [int(user_id) for user_id, in rows]


def _is_due(user_id: int, interval_seconds: int, now: datetime) -> bool:
    """判断用户扫描间隔是否到期。"""
    last_scan_at = last_scan_at_by_user.get(user_id)
    if last_scan_at is None:
        return True
    return (now - last_scan_at).total_seconds() >= interval_seconds


def _launch_debate_task(**launch_kwargs: Any) -> None:
    """从后台扫描调度 AI 复议辩论。"""
    from app.ai.llm_engine.runner import run_analysis_task

    asyncio.create_task(
        run_analysis_task(**launch_kwargs),
        name=f"position-discipline-analysis-{launch_kwargs['task_id']}",
    )
