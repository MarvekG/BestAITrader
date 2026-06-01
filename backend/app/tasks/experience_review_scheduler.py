from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.ai.experience.horizons import ReviewHorizon
from app.ai.experience.horizons import eligible_horizons
from app.ai.experience.horizons import normalize_review_horizon
from app.ai.experience.service import experience_service
from app.ai.llm_engine.roles import AGENT_ROLE_PORTFOLIO_MANAGER
from app.core.database import SessionLocal
from app.core.logger import get_logger
from app.crud.system_setting import system_setting
from app.models.data_storage import KlineData
from app.models.debate_message import DebateMessage
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.session import Session as DebateSession
from app.tasks.scheduled_task_registry import ScheduledTask
from app.tasks.scheduled_task_registry import ScheduledTaskSnapshot

logger = get_logger(__name__)

EXPERIENCE_REVIEW_JOB_ID = "experience_review_auto_scan"
EXPERIENCE_REVIEW_SCHEDULE_HOUR = 18
EXPERIENCE_REVIEW_SCHEDULE_MINUTE = 30
EXPERIENCE_REVIEW_MIN_MARKET_DAYS = 6
EXPERIENCE_REVIEW_CANDIDATE_LOOKBACK = 200
EXPERIENCE_REVIEW_MAX_RUNS_PER_TICK = 2
EXPERIENCE_REVIEW_CONFIG_KEY = "experience_review_scheduler_config"
EXPERIENCE_REVIEW_TIMEZONE = ZoneInfo("Asia/Shanghai")
EXPERIENCE_REVIEW_BLOCKING_STATUSES = {"started", "running", "completed", "failed"}


DEFAULT_EXPERIENCE_REVIEW_CONFIG: dict[str, Any] = {
    "enabled": False,
    "schedule_hour": EXPERIENCE_REVIEW_SCHEDULE_HOUR,
    "schedule_minute": EXPERIENCE_REVIEW_SCHEDULE_MINUTE,
    "candidate_lookback": EXPERIENCE_REVIEW_CANDIDATE_LOOKBACK,
    "max_runs_per_tick": EXPERIENCE_REVIEW_MAX_RUNS_PER_TICK,
}


def _coerce_bool(value: Any, default: bool) -> bool:
    """将用户提供的调度配置值转换为布尔值。

    Args:
        value: 来自持久化配置或 API 入参的原始值。
        default: 输入无法识别时使用的默认值。

    Returns:
        解析后的布尔值，或传入的默认值。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    """将调度配置值转换为带边界的整数。

    Args:
        value: 来自持久化配置或 API 入参的原始值。
        default: 解析失败时使用的默认值。
        min_value: 允许的最小值，包含边界。
        max_value: 允许的最大值，包含边界。

    Returns:
        已解析并限制在配置范围内的整数。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def normalize_experience_review_config(value: Any) -> dict[str, Any]:
    """规范化已存储的经验复盘调度配置。

    Args:
        value: 持久化配置中的原始值。

    Returns:
        已应用默认值和数值边界的调度配置。
    """
    raw = value if isinstance(value, dict) else {}
    return {
        "enabled": _coerce_bool(raw.get("enabled"), DEFAULT_EXPERIENCE_REVIEW_CONFIG["enabled"]),
        "schedule_hour": _coerce_int(
            raw.get("schedule_hour"),
            default=DEFAULT_EXPERIENCE_REVIEW_CONFIG["schedule_hour"],
            min_value=0,
            max_value=23,
        ),
        "schedule_minute": _coerce_int(
            raw.get("schedule_minute"),
            default=DEFAULT_EXPERIENCE_REVIEW_CONFIG["schedule_minute"],
            min_value=0,
            max_value=59,
        ),
        "candidate_lookback": _coerce_int(
            raw.get("candidate_lookback"),
            default=DEFAULT_EXPERIENCE_REVIEW_CONFIG["candidate_lookback"],
            min_value=1,
            max_value=5000,
        ),
        "max_runs_per_tick": _coerce_int(
            raw.get("max_runs_per_tick"),
            default=DEFAULT_EXPERIENCE_REVIEW_CONFIG["max_runs_per_tick"],
            min_value=1,
            max_value=20,
        ),
    }


def get_experience_review_scheduler_config(db: Session) -> dict[str, Any]:
    """获取带默认值的经验复盘调度配置。

    Args:
        db: 用于读取系统设置的数据库会话。

    Returns:
        规范化后的调度配置。
    """
    return normalize_experience_review_config(
        system_setting.get_value(
            db,
            EXPERIENCE_REVIEW_CONFIG_KEY,
            DEFAULT_EXPERIENCE_REVIEW_CONFIG,
        )
    )


def update_experience_review_scheduler_config(db: Session, value: dict[str, Any]) -> dict[str, Any]:
    """持久化并返回规范化后的经验复盘调度配置。

    Args:
        db: 用于更新系统设置的数据库会话。
        value: 调用方提交的原始调度配置。

    Returns:
        已持久化的规范化调度配置。
    """
    normalized = normalize_experience_review_config(value)
    system_setting.set_value(
        db,
        EXPERIENCE_REVIEW_CONFIG_KEY,
        normalized,
        description="Experience review scheduler configuration",
    )
    return normalized


@dataclass(frozen=True)
class ExperienceReviewCandidate:
    """A debate session eligible for scheduled experience review."""

    session_id: UUID
    user_id: int
    stock_code: str
    pm_created_at: datetime
    market_day_count: int
    review_horizon: ReviewHorizon


def _extract_review_horizon_from_event(row: ExperienceReviewEvent) -> ReviewHorizon | None:
    """从复盘事件中提取调度去重使用的复盘周期。

    Args:
        row: 经验复盘事件记录。

    Returns:
        事件中携带的复盘周期；缺少周期或周期无效时返回 ``None``。
    """
    payload = row.payload or {}
    candidates = [
        payload.get("review_horizon"),
        (payload.get("result") or {}).get("review_horizon") if isinstance(payload.get("result"), dict) else None,
    ]
    for value in candidates:
        try:
            parsed = normalize_review_horizon(value)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed
    return None


def _load_existing_review_horizons(
    db: Session,
    *,
    session_ids: list[UUID],
) -> dict[UUID, set[ReviewHorizon]]:
    """加载每个会话已经占用的复盘周期。

    Args:
        db: 数据库会话。
        session_ids: 需要检查复盘事件的会话 ID。

    Returns:
        以会话 ID 为 key、已完成或已启动复盘周期集合为 value 的映射。
    """
    existing: dict[UUID, set[ReviewHorizon]] = {session_id: set() for session_id in session_ids}
    if not session_ids:
        return existing

    rows = (
        db.query(ExperienceReviewEvent)
        .filter(
            ExperienceReviewEvent.session_id.in_(session_ids),
            ExperienceReviewEvent.stage == "experience_review",
            ExperienceReviewEvent.status.in_(EXPERIENCE_REVIEW_BLOCKING_STATUSES),
        )
        .all()
    )
    for row in rows:
        horizon = _extract_review_horizon_from_event(row)
        if horizon is None:
            continue
        existing.setdefault(row.session_id, set()).add(horizon)
    return existing


def get_scheduled_tasks() -> ScheduledTaskSnapshot:
    """返回中心调度器使用的经验复盘任务定义。

    Returns:
        包含启用任务或禁用任务 ID 的调度快照。
    """
    with SessionLocal() as db:
        config = get_experience_review_scheduler_config(db)

    if not config["enabled"]:
        logger.info("Experience review scheduler is disabled")
        return ScheduledTaskSnapshot(tasks=[], disabled_job_ids=[EXPERIENCE_REVIEW_JOB_ID])

    snapshot = ScheduledTaskSnapshot(
        tasks=[
            ScheduledTask(
                task_func=run_due_reviews,
                task_name="Experience Review Auto Scan",
                trigger_type="cron",
                job_id=EXPERIENCE_REVIEW_JOB_ID,
                trigger_args={
                    "hour": config["schedule_hour"],
                    "minute": config["schedule_minute"],
                },
                misfire_grace_time=3600,
            )
        ],
        disabled_job_ids=[],
    )
    logger.info(
        "Experience review auto scan scheduled: enabled=%s time=%02d:%02d",
        config["enabled"],
        config["schedule_hour"],
        config["schedule_minute"],
    )
    return snapshot


async def run_due_reviews() -> dict[str, Any]:
    """扫描已完成的辩论会话并运行到期经验复盘。

    Returns:
        包含启动数量、跳过原因和启动项元数据的运行摘要。
    """
    with SessionLocal() as db:
        config = get_experience_review_scheduler_config(db)
        if not config["enabled"]:
            return {"launched": 0, "skipped": "disabled", "items": []}
        candidates = _load_due_sessions(
            db,
            limit=config["max_runs_per_tick"],
            candidate_lookback=config["candidate_lookback"],
        )

    if not candidates:
        return {"launched": 0, "items": []}

    launched: list[dict[str, Any]] = []
    for candidate in candidates:
        result = await _launch_review(candidate)
        if result:
            launched.append(result)

    if launched:
        logger.info("Launched %s experience review task(s): %s", len(launched), launched)
    return {"launched": len(launched), "items": launched}


def _load_due_sessions(
    db: Session,
    *,
    limit: int,
    candidate_lookback: int = EXPERIENCE_REVIEW_CANDIDATE_LOOKBACK,
) -> list[ExperienceReviewCandidate]:
    """加载已满足自动复盘条件的已完成辩论会话。

    Args:
        db: 用于扫描辩论会话和行情数据的数据库会话。
        limit: 返回候选项的最大数量。
        candidate_lookback: 扫描历史会话的最大数量。

    Returns:
        从最早到期会话到最新会话排序的复盘候选项；同一会话按 5D、20D、60D 输出缺失周期。
    """
    latest_pm_created_at = (
        db.query(func.max(DebateMessage.created_at))
        .filter(
            DebateMessage.session_id == DebateSession.session_id,
            DebateMessage.agent_role == AGENT_ROLE_PORTFOLIO_MANAGER,
        )
        .correlate(DebateSession)
        .scalar_subquery()
    )
    market_day_count = (
        db.query(func.count(KlineData.date))
        .filter(
            KlineData.stock_code == DebateSession.stock_code,
            KlineData.freq == "D",
            KlineData.date >= func.date(latest_pm_created_at),
        )
        .correlate(DebateSession)
        .scalar_subquery()
    )
    rows = (
        db.query(
            DebateSession,
            latest_pm_created_at.label("pm_created_at"),
            market_day_count.label("market_day_count"),
        )
        .filter(
            DebateSession.status == "completed",
            DebateSession.user_id.isnot(None),
            DebateSession.stock_code.isnot(None),
            latest_pm_created_at.isnot(None),
            market_day_count >= EXPERIENCE_REVIEW_MIN_MARKET_DAYS,
        )
        .order_by(DebateSession.updated_at.asc(), DebateSession.created_at.asc())
        .limit(candidate_lookback + limit)
        .all()
    )
    existing_horizons = _load_existing_review_horizons(
        db,
        session_ids=[session_obj.session_id for session_obj, _, _ in rows],
    )
    candidates: list[ExperienceReviewCandidate] = []
    for session_obj, pm_created_at, market_day_count_value in rows:
        market_days = int(market_day_count_value or 0)
        completed_or_active = existing_horizons.get(session_obj.session_id, set())
        for review_horizon in eligible_horizons(market_days):
            if review_horizon in completed_or_active:
                continue
            if len(candidates) >= limit:
                return candidates
            candidates.append(
                ExperienceReviewCandidate(
                    session_id=session_obj.session_id,
                    user_id=session_obj.user_id,
                    stock_code=session_obj.stock_code,
                    pm_created_at=pm_created_at,
                    market_day_count=market_days,
                    review_horizon=review_horizon,
                )
            )
    return candidates


async def _launch_review(candidate: ExperienceReviewCandidate) -> dict[str, Any] | None:
    """运行一个调度触发的经验复盘。

    Args:
        candidate: 调度器选中的到期复盘候选项。

    Returns:
        复盘成功时返回启动元数据，否则返回 ``None``。
    """
    try:
        with SessionLocal() as db:
            result = await experience_service.analyze(
                db,
                user_id=candidate.user_id,
                session_id=candidate.session_id,
                review_horizon=candidate.review_horizon,
            )
        return {
            "review_run_id": result.get("review_run_id"),
            "session_id": str(candidate.session_id),
            "user_id": candidate.user_id,
            "stock_code": candidate.stock_code,
            "market_day_count": candidate.market_day_count,
            "review_horizon": candidate.review_horizon,
        }
    except Exception as exc:
        logger.exception(
            "Scheduled experience review failed for session=%s stock=%s: %s",
            candidate.session_id,
            candidate.stock_code,
            exc,
        )
        return None
