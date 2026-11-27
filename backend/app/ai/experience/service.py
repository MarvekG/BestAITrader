from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import json
from typing import Any, Dict, List
from uuid import UUID
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.experience.horizons import (
    HORIZON_REQUIRED_MARKET_DAYS,
    REVIEW_HORIZONS,
    ReviewHorizon,
    eligible_horizons,
    horizon_gap,
    normalize_review_horizon,
    review_status_for_candidate,
)
from app.core import database as database_module
from app.core.config import settings
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.core.utils.converters import safe_float, safe_isoformat
from app.ai.experience.workflow import create_experience_workflow
from app.models.data_storage import IndexDaily, KlineData, StockBasic
from app.models.debate_message import DebateMessage
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.experience_review_run import ExperienceReviewRun
from app.models.order import Order
from app.models.pm_decision import PMDecisionRecord
from app.models.session import Session as DebateSession
from app.models.trade_record import TradeRecord
from app.websocket.manager import ws_manager


logger = get_logger(__name__)


class ExperienceReviewConflict(ValueError):
    """经验复盘与现有排队/运行任务冲突。"""

    def __init__(self, message: str, *, review_run_id: str | None = None) -> None:
        super().__init__(message)
        self.review_run_id = review_run_id


VALID_STYLE_BUCKETS = {"short_term", "swing", "position", "long_term"}
VALID_ACTIONS = {"avoid", "watch", "buy", "add", "hold", "reduce", "sell"}
CORRECTNESS_BUCKETS = {"correct", "partially_correct", "incorrect", "inconclusive"}
ACTIVE_REVIEW_STATUSES = {"started", "running"}
REVIEW_ACTIVE_EVENT_STATUSES = {"queued", "started", "running"}
RUN_ACTIVE_STATUSES = {"queued", "running"}
STALE_REVIEW_TIMEOUT = timedelta(hours=2)
PM_AGENT_ROLE = "portfolio_manager"
REVIEW_DECISION_WINDOW_MAX_ROWS = 130
REVIEW_LATEST_OUTCOME_ROWS = 30
REVIEW_BENCHMARK_INDEX_CODE = "000300.SH"


def _normalize_confidence(value: Any) -> float:
    numeric = safe_float(value, 0.0)
    if 0 <= numeric <= 1:
        numeric *= 100
    return max(0.0, min(100.0, numeric))


def _style_bucket_from_frequency(trading_frequency: str | None) -> str:
    text = str(trading_frequency or "").strip().lower()
    if any(token in text for token in ("day", "日内", "短")):
        return "short_term"
    if any(token in text for token in ("swing", "波段")):
        return "swing"
    if any(token in text for token in ("long", "长线")):
        return "long_term"
    return "position"


def _extract_original_conclusion(message: DebateMessage) -> str:
    """从辩论消息中提取可用于复盘的原始结论文本。

    Args:
        message: 需要提取结论的辩论消息。

    Returns:
        优先级最高的结论文本；没有可读文本时返回空字符串。
    """
    if message.reasoning and str(message.reasoning).strip():
        return str(message.reasoning).strip()
    return ""


class ExperienceService:
    def _extract_review_horizon_from_event(self, row: ExperienceReviewEvent) -> ReviewHorizon | None:
        """从复盘事件中提取复盘周期。

        Args:
            row: 经验复盘事件记录。

        Returns:
            事件中记录的复盘周期；旧 completed 事件默认视为 ``20d``。
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
        if row.status == "completed":
            return "20d"
        return None

    async def _get_market_day_count(self, db: AsyncSession, *, stock_code: str, decision_time: datetime | None) -> int:
        """统计 PM 决策后的日 K 样本数量。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            decision_time: PM 决策时间。

        Returns:
            决策日及之后可用的日 K 样本数量。
        """
        if not stock_code or decision_time is None:
            return 0
        return int(
            (
                await db.execute(
                    select(func.count(KlineData.date)).where(
                KlineData.stock_code == stock_code,
                KlineData.freq == "D",
                KlineData.date >= decision_time.date(),
                    )
                )
            ).scalar()
            or 0
        )

    def _next_missing_horizon(self, market_day_count: int) -> tuple[ReviewHorizon | None, int | None]:
        """计算下一个尚未满足数据量要求的复盘周期。

        Args:
            market_day_count: 当前可用的决策后日 K 样本数量。

        Returns:
            二元组，包含下一个未满足周期和仍需补足的样本数量。
        """
        for horizon in REVIEW_HORIZONS:
            gap = horizon_gap(market_day_count, horizon)
            if gap > 0:
                return horizon, gap
        return None, None

    def _default_review_horizon(self, available_horizons: list[ReviewHorizon]) -> ReviewHorizon | None:
        """按产品默认优先级选择复盘周期。

        Args:
            available_horizons: 当前数据量已经满足的复盘周期。

        Returns:
            默认复盘周期；没有可用周期时返回 ``None``。
        """
        for horizon in ("20d", "60d", "5d"):
            if horizon in available_horizons:
                return horizon
        return None

    async def _review_horizon_buckets(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        session_ids: list[UUID],
    ) -> dict[UUID, dict[str, list[ReviewHorizon]]]:
        """按会话聚合各复盘周期的事件状态。

        Args:
            db: 数据库会话。
            user_id: 用户 ID。
            session_ids: 需要聚合复盘状态的会话 ID 列表。

        Returns:
            以会话 ID 为 key 的 completed、active、failed 周期分桶。
        """
        buckets: dict[UUID, dict[str, list[ReviewHorizon]]] = {
            session_id: {"completed": [], "active": [], "failed": []}
            for session_id in session_ids
        }
        if not session_ids:
            return buckets
        rows = (
            await db.execute(
                select(ExperienceReviewEvent)
                .where(
                    ExperienceReviewEvent.user_id == user_id,
                    ExperienceReviewEvent.session_id.in_(session_ids),
                    ExperienceReviewEvent.stage == "experience_review",
                )
                .order_by(ExperienceReviewEvent.created_at.desc())
            )
        ).scalars().all()
        for row in rows:
            horizon = self._extract_review_horizon_from_event(row)
            if horizon is None:
                continue
            session_bucket = buckets.setdefault(row.session_id, {"completed": [], "active": [], "failed": []})
            if row.status == "completed" and horizon not in session_bucket["completed"]:
                session_bucket["completed"].append(horizon)
            elif row.status in REVIEW_ACTIVE_EVENT_STATUSES and horizon not in session_bucket["active"]:
                session_bucket["active"].append(horizon)
            elif row.status == "failed" and horizon not in session_bucket["failed"]:
                session_bucket["failed"].append(horizon)
        return buckets

    async def list_review_candidates(
        self,
        *,
        user_id: int,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """列出当前用户可复盘的会话候选项。

        Args:
            user_id: 用户 ID。
            limit: 最多扫描的已完成会话数量。

        Returns:
            候选项列表和按展示状态统计的摘要。
        """
        async with database_module.AsyncSessionLocal() as db:
            sessions = (
                await db.execute(
                    select(DebateSession)
                    .where(
                        DebateSession.user_id == user_id,
                        DebateSession.status == "completed",
                        DebateSession.stock_code.isnot(None),
                    )
                    .order_by(DebateSession.updated_at.desc(), DebateSession.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            if not sessions:
                return {"items": [], "summary": {}}

            session_ids = [item.session_id for item in sessions]
            stock_codes = list({item.stock_code for item in sessions})
            stock_rows = (
                await db.execute(select(StockBasic).where(StockBasic.stock_code.in_(stock_codes)))
            ).scalars().all()
            stock_map = {item.stock_code: item for item in stock_rows}

            pm_rows = (
                await db.execute(select(PMDecisionRecord).where(PMDecisionRecord.session_id.in_(session_ids)))
            ).scalars().all()
            latest_pm_by_session: dict[UUID, PMDecisionRecord] = {}
            for row in pm_rows:
                latest_pm_by_session.setdefault(row.session_id, row)

            horizon_buckets: dict[UUID, dict[str, list[ReviewHorizon]]] = {
                session_id: {"completed": [], "active": [], "failed": []}
                for session_id in session_ids
            }
            event_rows = (
                await db.execute(
                    select(ExperienceReviewEvent)
                    .where(
                        ExperienceReviewEvent.user_id == user_id,
                        ExperienceReviewEvent.session_id.in_(session_ids),
                        ExperienceReviewEvent.stage == "experience_review",
                    )
                    .order_by(ExperienceReviewEvent.created_at.desc())
                )
            ).scalars().all()
            for row in event_rows:
                horizon = self._extract_review_horizon_from_event(row)
                if horizon is None:
                    continue
                session_bucket = horizon_buckets.setdefault(row.session_id, {"completed": [], "active": [], "failed": []})
                if row.status == "completed" and horizon not in session_bucket["completed"]:
                    session_bucket["completed"].append(horizon)
                elif row.status in REVIEW_ACTIVE_EVENT_STATUSES and horizon not in session_bucket["active"]:
                    session_bucket["active"].append(horizon)
                elif row.status == "failed" and horizon not in session_bucket["failed"]:
                    session_bucket["failed"].append(horizon)

            market_day_counts: dict[tuple[str, datetime], int] = {}
            for session_obj in sessions:
                pm_row = latest_pm_by_session.get(session_obj.session_id)
                if not pm_row or not session_obj.stock_code or pm_row.created_at is None:
                    continue
                key = (session_obj.stock_code, pm_row.created_at)
                market_day_counts[key] = int(
                    (
                        await db.execute(
                            select(func.count(KlineData.date)).where(
                                KlineData.stock_code == session_obj.stock_code,
                                KlineData.freq == "D",
                                KlineData.date >= pm_row.created_at.date(),
                            )
                        )
                    ).scalar()
                    or 0
                )
            items: list[dict[str, Any]] = []
            summary: dict[str, int] = defaultdict(int)
            for session_obj in sessions:
                pm_row = latest_pm_by_session.get(session_obj.session_id)
                if not pm_row:
                    continue
                stock = stock_map.get(session_obj.stock_code)
                market_day_count = market_day_counts.get((session_obj.stock_code, pm_row.created_at), 0)
                eligible = eligible_horizons(market_day_count)
                bucket = horizon_buckets.get(session_obj.session_id, {"completed": [], "active": [], "failed": []})
                review_status = review_status_for_candidate(
                    eligible=eligible,
                    completed=bucket["completed"],
                    active=bucket["active"],
                    failed=bucket["failed"],
                )
                next_horizon, days_until_next_horizon = self._next_missing_horizon(market_day_count)
                item = {
                    "session_id": session_obj.session_id,
                    "stock_code": session_obj.stock_code,
                    "stock_name": stock.name if stock else session_obj.stock_code,
                    "industry": stock.industry if stock else None,
                    "status": session_obj.status,
                    "trading_frequency": session_obj.trading_frequency,
                    "trading_strategy": session_obj.trading_strategy,
                    "pm_confidence": _normalize_confidence(pm_row.confidence_score),
                    "pm_created_at": pm_row.created_at,
                    "market_day_count": market_day_count,
                    "eligible_horizons": eligible,
                    "latest_completed_horizons": bucket["completed"],
                    "active_horizons": bucket["active"],
                    "failed_horizons": bucket["failed"],
                    "review_status": review_status,
                    "next_horizon": next_horizon,
                    "days_until_next_horizon": days_until_next_horizon,
                }
                items.append(item)
                summary[review_status] += 1
            return {"items": items, "summary": dict(summary)}

    async def _mark_review_runs_failed(
        self,
        db: AsyncSession,
        *,
        failed_runs: list[tuple[str, UUID, int, str | None]],
        failure_message: str,
        reason: str,
    ) -> int:
        for review_run_id, session_id, user_id, stage in failed_runs:
            await self._persist_review_event(
                db,
                review_run_id=review_run_id,
                session_id=session_id,
                user_id=user_id,
                stage=stage or "experience_review",
                status="failed",
                message_key="experience.live_messages.failed",
                message_params={"error": failure_message},
                payload={"error": failure_message, "reason": reason},
            )
        run_ids = [item[0] for item in failed_runs]
        if run_ids:
            await db.execute(
                update(ExperienceReviewRun)
                .where(
                    ExperienceReviewRun.review_run_id.in_(run_ids),
                    ExperienceReviewRun.status == "running",
                )
                .values(
                    status="failed",
                    finished_at=datetime.now(),
                    error_message=failure_message,
                )
            )
            await db.commit()
        return len(failed_runs)

    async def _cleanup_interrupted_review_runs_in_db(self, db: AsyncSession) -> int:
        rows = (
            await db.execute(
                select(
                    ExperienceReviewEvent.review_run_id,
                    ExperienceReviewEvent.session_id,
                    ExperienceReviewEvent.user_id,
                    ExperienceReviewEvent.stage,
                    ExperienceReviewEvent.status,
                ).order_by(ExperienceReviewEvent.created_at.desc())
            )
        ).all()
        seen_run_ids: set[str] = set()
        interrupted_runs: list[tuple[str, UUID, int, str | None]] = []
        for review_run_id, session_id, user_id, stage, status in rows:
            if review_run_id in seen_run_ids:
                continue
            seen_run_ids.add(review_run_id)
            if status in ACTIVE_REVIEW_STATUSES:
                interrupted_runs.append((review_run_id, session_id, user_id, stage))

        if not interrupted_runs:
            logger.info("experience restart cleanup: no interrupted review runs found")
            return 0

        failure_message = i18n_service.t("experience.review_interrupted_by_restart")
        logger.warning("experience restart cleanup: interrupted_runs=%s", len(interrupted_runs))
        for review_run_id, session_id, _user_id, stage in interrupted_runs:
            logger.warning(
                "experience restart cleanup: review_run_id=%s session_id=%s stage=%s -> failed",
                review_run_id,
                session_id,
                stage,
            )

        for review_run_id, session_id, user_id, stage in interrupted_runs:
            db.add(
                ExperienceReviewEvent(
                    review_run_id=review_run_id,
                    session_id=session_id,
                    user_id=user_id,
                    event_type="experience_review_update",
                    stage=stage or "experience_review",
                    status="failed",
                    message_key="experience.live_messages.failed",
                    message_params={"error": failure_message},
                    payload={"error": failure_message, "reason": "restart_recovery"},
                )
            )
        run_ids = [item[0] for item in interrupted_runs]
        if run_ids:
            await db.execute(
                update(ExperienceReviewRun)
                .where(
                    ExperienceReviewRun.review_run_id.in_(run_ids),
                    ExperienceReviewRun.status == "running",
                )
                .values(
                    status="failed",
                    finished_at=datetime.now(),
                    error_message=failure_message,
                )
            )
        await db.commit()
        return len(interrupted_runs)

    async def cleanup_interrupted_review_runs(self) -> int:
        async with database_module.AsyncSessionLocal() as db:
            return await self._cleanup_interrupted_review_runs_in_db(db)

    async def _cleanup_stale_review_runs(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        session_id: UUID | None = None,
    ) -> None:
        query = select(
            ExperienceReviewEvent.review_run_id,
            ExperienceReviewEvent.session_id,
            ExperienceReviewEvent.user_id,
            ExperienceReviewEvent.stage,
            ExperienceReviewEvent.status,
            ExperienceReviewEvent.created_at,
        ).where(ExperienceReviewEvent.user_id == user_id)
        if session_id is not None:
            query = query.where(ExperienceReviewEvent.session_id == session_id)

        rows = (await db.execute(query.order_by(ExperienceReviewEvent.created_at.desc()))).all()
        seen_run_ids: set[str] = set()
        stale_runs: list[tuple[str, UUID, int, str | None]] = []
        now = datetime.now()
        for review_run_id, row_session_id, row_user_id, stage, status, created_at in rows:
            if review_run_id in seen_run_ids:
                continue
            seen_run_ids.add(review_run_id)
            if status not in ACTIVE_REVIEW_STATUSES:
                continue
            if created_at and now - created_at <= STALE_REVIEW_TIMEOUT:
                continue
            stale_runs.append((review_run_id, row_session_id, row_user_id, stage))

        stale_error = i18n_service.t("experience.stale_review_recovered")
        await self._mark_review_runs_failed(
            db,
            failed_runs=stale_runs,
            failure_message=stale_error,
            reason="stale_review_recovery",
        )

    async def _get_active_review_run_id(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        session_id: UUID,
        cleanup_stale: bool = True,
    ) -> str | None:
        if cleanup_stale:
            await self._cleanup_stale_review_runs(db, user_id=user_id, session_id=session_id)
        rows = (
            await db.execute(
                select(ExperienceReviewEvent.review_run_id, ExperienceReviewEvent.status)
                .where(
                    ExperienceReviewEvent.user_id == user_id,
                    ExperienceReviewEvent.session_id == session_id,
                )
                .order_by(ExperienceReviewEvent.created_at.desc())
            )
        ).all()
        seen_run_ids: set[str] = set()
        for review_run_id, status in rows:
            if review_run_id in seen_run_ids:
                continue
            seen_run_ids.add(review_run_id)
            if status in REVIEW_ACTIVE_EVENT_STATUSES:
                return review_run_id
        return None

    async def _find_active_run(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        session_id: UUID | None = None,
        stock_code: str | None = None,
    ) -> ExperienceReviewRun | None:
        query = select(ExperienceReviewRun).where(
            ExperienceReviewRun.user_id == user_id,
            ExperienceReviewRun.status.in_(RUN_ACTIVE_STATUSES),
        )
        if session_id is not None:
            query = query.where(ExperienceReviewRun.session_id == session_id)
        if stock_code is not None:
            query = query.where(ExperienceReviewRun.stock_code == stock_code)
        return (
            await db.execute(query.order_by(ExperienceReviewRun.created_at.asc()).limit(1))
        ).scalars().first()

    async def _update_run_status(
        self,
        *,
        review_run_id: str,
        status: str,
        error_message: str | None = None,
    ) -> None:
        async with database_module.AsyncSessionLocal() as db:
            values: Dict[str, Any] = {
                "status": status,
                "finished_at": datetime.now() if status in {"completed", "failed", "cancelled"} else None,
            }
            if error_message is not None:
                values["error_message"] = error_message
            await db.execute(
                update(ExperienceReviewRun)
                .where(ExperienceReviewRun.review_run_id == review_run_id)
                .values(**values)
            )
            await db.commit()

    async def enqueue_review(
        self,
        *,
        user_id: int,
        session_id: UUID,
        review_horizon: str | None = None,
    ) -> Dict[str, Any]:
        """校验并创建排队中的经验复盘运行。

        Args:
            user_id: 用户 ID。
            session_id: 需要复盘的辩论会话 ID。
            review_horizon: 可选复盘周期；未提供时使用当前最高可用周期。

        Returns:
            包含 review_run_id、队列状态和队列位置的字典。

        Raises:
            ExperienceReviewConflict: 同会话或同用户同股票已有排队/运行任务。
            ValueError: 会话不存在、周期不可用或行情数据不足。
        """
        review_run_id = str(uuid.uuid4())
        async with database_module.AsyncSessionLocal() as db:
            session_obj = (
                await db.execute(
                    select(DebateSession).where(
                        DebateSession.session_id == session_id,
                        DebateSession.user_id == user_id,
                    )
                )
            ).scalars().first()
            if not session_obj:
                raise ValueError(f"Session {session_id} not found")
            if not session_obj.stock_code:
                raise ValueError(f"Session {session_id} has no stock code")

            active_session_run = await self._find_active_run(
                db,
                user_id=user_id,
                session_id=session_id,
            )
            if active_session_run is not None:
                raise ExperienceReviewConflict(
                    f"Experience review is already queued or running for session {session_id} "
                    f"(run {active_session_run.review_run_id})",
                    review_run_id=active_session_run.review_run_id,
                )

            active_stock_run = await self._find_active_run(
                db,
                user_id=user_id,
                stock_code=session_obj.stock_code,
            )
            if active_stock_run is not None:
                raise ExperienceReviewConflict(
                    f"Experience review is already queued or running for stock {session_obj.stock_code} "
                    f"(run {active_stock_run.review_run_id})",
                    review_run_id=active_stock_run.review_run_id,
                )

            stock = (
                await db.execute(select(StockBasic).where(StockBasic.stock_code == session_obj.stock_code))
            ).scalars().first()
            stock_name = stock.name if stock else session_obj.stock_code
            debate_messages = (
                await db.execute(
                    select(DebateMessage)
                    .where(DebateMessage.session_id == session_id)
                    .order_by(DebateMessage.created_at.asc())
                )
            ).scalars().all()
            if not debate_messages:
                raise ValueError(f"Session {session_id} has no debate messages")
            pm_message = self._get_latest_pm_message(debate_messages)
            if not pm_message:
                raise ValueError(f"Session {session_id} has no PM decision")

            market_day_count = await self._get_market_day_count(
                db,
                stock_code=session_obj.stock_code,
                decision_time=pm_message.created_at,
            )
            available_horizons = eligible_horizons(market_day_count)
            selected_review_horizon = normalize_review_horizon(review_horizon)
            if selected_review_horizon is None:
                selected_review_horizon = self._default_review_horizon(available_horizons)
            if selected_review_horizon is None:
                raise ValueError(
                    "Market outcome summary is unavailable for this session; "
                    "experience analysis requires post-decision market data."
                )
            required_days = HORIZON_REQUIRED_MARKET_DAYS[selected_review_horizon]
            if market_day_count < required_days:
                raise ValueError(
                    f"Review horizon {selected_review_horizon} requires {required_days} market days; "
                    f"only {market_day_count} are available."
                )

            queued_session_id = session_obj.session_id
            queued_stock_code = session_obj.stock_code
            queued_stock_name = stock_name
            queued_market_day_count = market_day_count
            queued_created_at = datetime.now()
            run = ExperienceReviewRun(
                review_run_id=review_run_id,
                session_id=queued_session_id,
                user_id=user_id,
                stock_code=queued_stock_code,
                review_horizon=selected_review_horizon,
                status="queued",
                attempt_count=0,
                created_at=queued_created_at,
            )
            db.add(run)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                conflict = await self._find_active_run(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                ) or await self._find_active_run(
                    db,
                    user_id=user_id,
                    stock_code=queued_stock_code,
                )
                raise ExperienceReviewConflict(
                    f"Experience review is already queued or running for session {session_id}",
                    review_run_id=conflict.review_run_id if conflict else None,
                )

            await self._persist_review_event(
                db,
                review_run_id=review_run_id,
                session_id=queued_session_id,
                user_id=user_id,
                stage="experience_review",
                status="queued",
                message_key="experience.live_messages.queued",
                payload={
                    "stock_code": queued_stock_code,
                    "stock_name": queued_stock_name,
                    "review_horizon": selected_review_horizon,
                    "market_day_count": queued_market_day_count,
                },
            )
            queue_position = int(
                await db.scalar(
                    select(func.count())
                    .select_from(ExperienceReviewRun)
                    .where(
                        ExperienceReviewRun.status == "queued",
                        ExperienceReviewRun.created_at <= queued_created_at,
                    )
                )
                or 0
            )

        await self._push_review_update(
            debate_session_id=str(queued_session_id),
            review_run_id=review_run_id,
            stage="experience_review",
            status="queued",
            message_key="experience.live_messages.queued",
            payload={
                "stock_code": queued_stock_code,
                "stock_name": queued_stock_name,
                "review_horizon": selected_review_horizon,
                "market_day_count": queued_market_day_count,
            },
        )
        from app.ai.experience.queue import experience_review_queue

        experience_review_queue.notify()
        return {
            "review_run_id": review_run_id,
            "status": "queued",
            "queue_position": queue_position,
            "session_id": queued_session_id,
            "stock_code": queued_stock_code,
            "stock_name": queued_stock_name,
            "review_horizon": selected_review_horizon,
            "market_day_count": queued_market_day_count,
            "created_at": queued_created_at,
        }

    async def execute_queued_review(self, review_run_id: str) -> Dict[str, Any]:
        """执行已由 worker 领取的经验复盘任务。

        Args:
            review_run_id: 排队运行 ID。

        Returns:
            复盘结果字典。
        """
        async with database_module.AsyncSessionLocal() as db:
            run = (
                await db.execute(
                    select(ExperienceReviewRun).where(
                        ExperienceReviewRun.review_run_id == review_run_id
                    )
                )
            ).scalars().first()
            if run is None:
                raise ValueError(f"Experience review run {review_run_id} not found")
            user_id = run.user_id
            session_id = run.session_id
            review_horizon = run.review_horizon
            if run.status not in RUN_ACTIVE_STATUSES:
                raise ValueError(
                    f"Experience review run {review_run_id} is not active: {run.status}"
                )

        try:
            result = await self._analyze(
                user_id=user_id,
                session_id=session_id,
                review_horizon=review_horizon,
                review_run_id=review_run_id,
                skip_active_check=True,
            )
        except Exception as exc:
            await self._update_run_status(
                review_run_id=review_run_id,
                status="failed",
                error_message=str(exc),
            )
            raise

        await self._update_run_status(review_run_id=review_run_id, status="completed")
        return result

    async def analyze(
        self,
        *,
        user_id: int,
        session_id: UUID,
        review_horizon: str | None = None,
    ) -> Dict[str, Any]:
        """同步运行单个辩论会话的经验复盘（内部/测试用）。

        生产 API 和调度器使用 ``enqueue_review`` 进入全局队列；本方法保留为
        同步执行入口，便于测试和内部工具直接等待结果。

        Args:
            user_id: 用户 ID。
            session_id: 需要复盘的辩论会话 ID。
            review_horizon: 可选复盘周期；未提供时使用当前最高可用周期。

        Returns:
            经验复盘结果、写入记忆信息和事件追踪信息。

        Raises:
            ValueError: 当会话不存在、复盘正在运行、周期不可用或复盘上下文不足时抛出。
        """
        review_run_id = str(uuid.uuid4())
        selected_review_horizon: ReviewHorizon | None = None
        market_day_count = 0
        return await self._analyze(
            user_id=user_id,
            session_id=session_id,
            review_horizon=review_horizon,
            review_run_id=review_run_id,
        )

    async def _analyze(
        self,
        *,
        user_id: int,
        session_id: UUID,
        review_horizon: str | None,
        review_run_id: str,
        skip_active_check: bool = False,
    ) -> Dict[str, Any]:
        selected_review_horizon: ReviewHorizon | None = None
        market_day_count = 0
        try:
            async with database_module.AsyncSessionLocal() as db:
                session_obj = (
                    await db.execute(
                        select(DebateSession).where(
                            DebateSession.session_id == session_id,
                            DebateSession.user_id == user_id,
                        )
                    )
                ).scalars().first()
                if not session_obj:
                    raise ValueError(f"Session {session_id} not found")

                if not skip_active_check:
                    await self._cleanup_stale_review_runs(db, user_id=user_id, session_id=session_id)
                    active_review_run_id = await self._get_active_review_run_id(
                        db,
                        user_id=user_id,
                        session_id=session_id,
                        cleanup_stale=False,
                    )
                    if active_review_run_id:
                        raise ValueError(
                            f"Experience review is already running for session {session_id} "
                            f"(run {active_review_run_id})"
                        )

                stock = (
                    await db.execute(select(StockBasic).where(StockBasic.stock_code == session_obj.stock_code))
                ).scalars().first()
                stock_name = stock.name if stock else session_obj.stock_code
                industry = stock.industry if stock else None
                style_bucket = _style_bucket_from_frequency(session_obj.trading_frequency)
                if style_bucket not in VALID_STYLE_BUCKETS:
                    raise ValueError(
                        f"Unsupported style bucket derived from trading_frequency: {session_obj.trading_frequency}"
                    )

                debate_messages = (
                    await db.execute(
                        select(DebateMessage)
                        .where(DebateMessage.session_id == session_id)
                        .order_by(DebateMessage.created_at.asc())
                    )
                ).scalars().all()
                if not debate_messages:
                    raise ValueError(f"Session {session_id} has no debate messages")

                pm_message = self._get_latest_pm_message(debate_messages)
                if not pm_message:
                    raise ValueError(f"Session {session_id} has no PM decision")

                market_day_count = await self._get_market_day_count(
                    db,
                    stock_code=session_obj.stock_code,
                    decision_time=pm_message.created_at,
                )
                available_horizons = eligible_horizons(market_day_count)
                selected_review_horizon = normalize_review_horizon(review_horizon)
                if selected_review_horizon is None:
                    selected_review_horizon = self._default_review_horizon(available_horizons)
                if selected_review_horizon is None:
                    raise ValueError(
                        "Market outcome summary is unavailable for this session; "
                        "experience analysis requires post-decision market data."
                    )
                required_days = HORIZON_REQUIRED_MARKET_DAYS[selected_review_horizon]
                if market_day_count < required_days:
                    raise ValueError(
                        f"Review horizon {selected_review_horizon} requires {required_days} market days; "
                        f"only {market_day_count} are available."
                    )

                debate_review_context = await self._build_debate_review_context(
                    db=db,
                    session_obj=session_obj,
                    stock_name=stock_name,
                    industry=industry,
                    debate_messages=debate_messages,
                    pm_message=pm_message,
                    review_horizon=selected_review_horizon,
                    market_day_count=market_day_count,
                )
                if not (debate_review_context.get("market_outcome_summary") or {}):
                    raise ValueError(
                        "Market outcome summary is unavailable for this session; "
                        "experience analysis requires post-decision market data."
                    )

                await self._persist_review_event(
                    db,
                    review_run_id=review_run_id,
                    session_id=session_obj.session_id,
                    user_id=user_id,
                    stage="experience_review",
                    status="started",
                    message_key="experience.live_messages.started",
                    payload={
                        "stock_code": session_obj.stock_code,
                        "stock_name": stock_name,
                        "review_horizon": selected_review_horizon,
                        "market_day_count": market_day_count,
                    },
                )

            async def persist_live_event(
                *,
                stage: str,
                status: str,
                message_key: str | None,
                message_params: Dict[str, Any] | None = None,
                payload: Dict[str, Any] | None = None,
            ) -> None:
                async with database_module.AsyncSessionLocal() as event_db:
                    await self._persist_review_event(
                        event_db,
                        review_run_id=review_run_id,
                        session_id=session_obj.session_id,
                        user_id=user_id,
                        stage=stage,
                        status=status,
                        message_key=message_key,
                        message_params=message_params,
                        payload=payload,
                    )

            await self._push_review_update(
                debate_session_id=str(session_obj.session_id),
                review_run_id=review_run_id,
                stage="experience_review",
                status="started",
                message_key="experience.live_messages.started",
                payload={
                    "stock_code": session_obj.stock_code,
                    "stock_name": stock_name,
                    "review_horizon": selected_review_horizon,
                    "market_day_count": market_day_count,
                },
            )
            workflow = create_experience_workflow()
            result_state = await workflow.ainvoke(
                {
                    "user_id": user_id,
                    "session_id": str(session_obj.session_id),
                    "review_run_id": review_run_id,
                    "stock_code": session_obj.stock_code,
                    "stock_name": stock_name,
                    "industry": industry,
                    "style_bucket": style_bucket,
                    "trading_frequency": session_obj.trading_frequency,
                    "trading_strategy": session_obj.trading_strategy,
                    "review_horizon": selected_review_horizon,
                    "market_day_count": market_day_count,
                    "debate_review_context": debate_review_context,
                    "event_callback": persist_live_event,
                    "errors": [],
                }
            )

            if result_state.get("errors"):
                raise RuntimeError("; ".join(result_state["errors"]))

            full_context = result_state.get("full_context") or {}
            analysis_payload = result_state.get("analysis_payload") or {}
            if not analysis_payload:
                raise RuntimeError("Experience workflow returned empty analysis payload")

            normalized_payload = self._normalize_analysis_payload(
                analysis_payload,
                debate_review_context=debate_review_context,
                tool_trace=result_state.get("tool_trace") or [],
            )
            reviewed_at = datetime.now()
            result = {
                "review_run_id": review_run_id,
                "review_horizon": selected_review_horizon,
                "market_day_count": market_day_count,
                "session_id": session_obj.session_id,
                "stock_code": session_obj.stock_code,
                "stock_name": stock_name,
                "industry": industry,
                "style_bucket": style_bucket,
                "trading_frequency": session_obj.trading_frequency,
                "trading_strategy": session_obj.trading_strategy,
                "analysis_date": pm_message.created_at or reviewed_at,
                "reviewed_at": reviewed_at,
                "debate_review_context": debate_review_context,
                "full_context": full_context,
                "analysis_payload": normalized_payload,
                "tool_trace": result_state.get("tool_trace") or [],
            }
            pm_decision_context = debate_review_context.get("pm_decision") or {}
            original_pm_action = self._normalize_action(
                normalized_payload.get("original_pm_decision")
                or pm_decision_context.get("decision")
            )
            completed_payload = self._build_completed_event_payload(
                result=result,
                recommended_action=original_pm_action,
                debate_correctness=normalized_payload.get("debate_correctness"),
            )
            async with database_module.AsyncSessionLocal() as db:
                await self._persist_review_event(
                    db,
                    review_run_id=review_run_id,
                    session_id=session_obj.session_id,
                    user_id=user_id,
                    stage="experience_review",
                    status="completed",
                    message_key="experience.live_messages.completed",
                    payload=completed_payload,
                )
            await self._push_review_update(
                debate_session_id=str(session_obj.session_id),
                review_run_id=review_run_id,
                stage="experience_review",
                status="completed",
                message_key="experience.live_messages.completed",
                payload=completed_payload,
            )
            return result
        except Exception as exc:
            await self._push_review_update(
                debate_session_id=str(session_id),
                review_run_id=review_run_id,
                stage="experience_review",
                status="failed",
                message_key="experience.live_messages.failed",
                message_params={"error": str(exc)},
                payload={
                    "error": str(exc),
                    "review_horizon": selected_review_horizon,
                    "market_day_count": market_day_count,
                },
            )
            async with database_module.AsyncSessionLocal() as db:
                await self._persist_review_event(
                    db,
                    review_run_id=review_run_id,
                    session_id=session_id,
                    user_id=user_id,
                    stage="experience_review",
                    status="failed",
                    message_key="experience.live_messages.failed",
                    message_params={"error": str(exc)},
                    payload={
                        "error": str(exc),
                        "review_horizon": selected_review_horizon,
                        "market_day_count": market_day_count,
                    },
                )
            raise

    async def list_debate_sessions(self, *, user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        async with database_module.AsyncSessionLocal() as db:
            sessions = (
                await db.execute(
                    select(DebateSession)
                    .where(DebateSession.user_id == user_id)
                    .order_by(DebateSession.updated_at.desc(), DebateSession.created_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            if not sessions:
                return []

            stock_codes = list({item.stock_code for item in sessions})
            basics = (
                await db.execute(select(StockBasic.stock_code, StockBasic.name).where(StockBasic.stock_code.in_(stock_codes)))
            ).all()
            stock_name_map = {code: name for code, name in basics}

            session_ids = [item.session_id for item in sessions]
            pm_rows = (
                await db.execute(select(PMDecisionRecord).where(PMDecisionRecord.session_id.in_(session_ids)))
            ).scalars().all()
            latest_pm_by_session: Dict[UUID, PMDecisionRecord] = {}
            for row in pm_rows:
                latest_pm_by_session.setdefault(row.session_id, row)

            reviewed_session_ids = {
                session_id
                for session_id, in (
                    await db.execute(
                        select(ExperienceReviewEvent.session_id)
                        .where(
                            ExperienceReviewEvent.user_id == user_id,
                            ExperienceReviewEvent.session_id.in_(session_ids),
                        )
                        .distinct()
                    )
                ).all()
            }

            items: List[Dict[str, Any]] = []
            for session_obj in sessions:
                pm_row = latest_pm_by_session.get(session_obj.session_id)
                if not pm_row:
                    continue
                items.append(
                    {
                        "session_id": session_obj.session_id,
                        "stock_code": session_obj.stock_code,
                        "stock_name": stock_name_map.get(session_obj.stock_code, session_obj.stock_code),
                        "status": session_obj.status,
                        "trading_frequency": session_obj.trading_frequency,
                        "trading_strategy": session_obj.trading_strategy,
                        "created_at": session_obj.created_at,
                        "updated_at": session_obj.updated_at,
                        "pm_confidence": _normalize_confidence(pm_row.confidence_score),
                        "has_experience_review": session_obj.session_id in reviewed_session_ids,
                    }
                )
            return items

    async def list_review_events(
        self,
        *,
        user_id: int,
        session_id: UUID,
    ) -> List[Dict[str, Any]]:
        async with database_module.AsyncSessionLocal() as db:
            latest_run_id = (
                await db.execute(
                    select(ExperienceReviewEvent.review_run_id)
                    .where(
                        ExperienceReviewEvent.user_id == user_id,
                        ExperienceReviewEvent.session_id == session_id,
                    )
                    .order_by(ExperienceReviewEvent.created_at.desc())
                    .limit(1)
                )
            ).scalar()
            if not latest_run_id:
                return []

            rows = (
                await db.execute(
                    select(ExperienceReviewEvent)
                    .where(
                        ExperienceReviewEvent.user_id == user_id,
                        ExperienceReviewEvent.session_id == session_id,
                        ExperienceReviewEvent.review_run_id == latest_run_id,
                    )
                    .order_by(ExperienceReviewEvent.created_at.asc())
                )
            ).scalars().all()
        return [
            {
                "event_id": str(row.event_id),
                "review_run_id": row.review_run_id,
                "event_type": row.event_type,
                "stage": row.stage,
                "status": row.status,
                "message_key": row.message_key,
                "message_params": row.message_params or {},
                "payload": row.payload or {},
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def list_review_runs(
        self,
        *,
        user_id: int,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """列出当前用户最近的经验复盘运行摘要。

        Args:
            user_id: 用户 ID。
            limit: 返回运行摘要的最大数量。

        Returns:
            按更新时间倒序排列的复盘运行摘要列表。
        """
        async with database_module.AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(ExperienceReviewEvent)
                    .where(ExperienceReviewEvent.user_id == user_id)
                    .order_by(ExperienceReviewEvent.created_at.desc())
                )
            ).scalars().all()
            if not rows:
                return []

            latest_by_run: Dict[str, ExperienceReviewEvent] = {}
            earliest_by_run: Dict[str, datetime] = {}
            session_ids: set[UUID] = set()
            for row in rows:
                if row.review_run_id not in latest_by_run:
                    latest_by_run[row.review_run_id] = row
                earliest_by_run[row.review_run_id] = min(
                    row.created_at,
                    earliest_by_run.get(row.review_run_id, row.created_at),
                )
                session_ids.add(row.session_id)
                if len(latest_by_run) >= limit and row.review_run_id in latest_by_run:
                    continue

            sessions = (
                await db.execute(
                    select(DebateSession).where(
                        DebateSession.user_id == user_id,
                        DebateSession.session_id.in_(list(session_ids)),
                    )
                )
            ).scalars().all()
            session_map = {item.session_id: item for item in sessions}

            stock_codes = list({item.stock_code for item in sessions})
            basics = (
                await db.execute(select(StockBasic.stock_code, StockBasic.name).where(StockBasic.stock_code.in_(stock_codes)))
            ).all()
            stock_name_map = {code: name for code, name in basics}

            items: List[Dict[str, Any]] = []
            for review_run_id, latest in list(latest_by_run.items())[:limit]:
                session_obj = session_map.get(latest.session_id)
                if not session_obj:
                    continue
                payload = latest.payload or {}
                items.append(
                    {
                        "review_run_id": review_run_id,
                        "review_horizon": payload.get("review_horizon") or "20d",
                        "market_day_count": payload.get("market_day_count"),
                        "session_id": latest.session_id,
                        "stock_code": session_obj.stock_code,
                        "stock_name": stock_name_map.get(session_obj.stock_code, session_obj.stock_code),
                        "trading_frequency": session_obj.trading_frequency,
                        "trading_strategy": session_obj.trading_strategy,
                        "status": latest.status,
                        "stage": latest.stage,
                        "message_key": latest.message_key,
                        "message_params": latest.message_params or {},
                        "recommended_action": payload.get("recommended_action"),
                        "debate_correctness": payload.get("debate_correctness"),
                        "created_at": earliest_by_run.get(review_run_id, latest.created_at),
                        "updated_at": latest.created_at,
                    }
                )
            items.sort(key=lambda item: item["updated_at"], reverse=True)
            return items[:limit]

    async def list_review_events_by_run(
        self,
        *,
        user_id: int,
        review_run_id: str,
    ) -> List[Dict[str, Any]]:
        async with database_module.AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(ExperienceReviewEvent)
                    .where(
                        ExperienceReviewEvent.user_id == user_id,
                        ExperienceReviewEvent.review_run_id == review_run_id,
                    )
                    .order_by(ExperienceReviewEvent.created_at.asc())
                )
            ).scalars().all()
        return [
            {
                "event_id": str(row.event_id),
                "review_run_id": row.review_run_id,
                "session_id": str(row.session_id),
                "event_type": row.event_type,
                "stage": row.stage,
                "status": row.status,
                "message_key": row.message_key,
                "message_params": row.message_params or {},
                "payload": row.payload or {},
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def get_review_run_result(
        self,
        *,
        user_id: int,
        review_run_id: str,
    ) -> Dict[str, Any] | None:
        async with database_module.AsyncSessionLocal() as db:
            rows = (
                await db.execute(
                    select(ExperienceReviewEvent)
                    .where(
                        ExperienceReviewEvent.user_id == user_id,
                        ExperienceReviewEvent.review_run_id == review_run_id,
                    )
                    .order_by(ExperienceReviewEvent.created_at.asc())
                )
            ).scalars().all()
            if not rows:
                return None

            completed_event = next(
                (
                    row
                    for row in reversed(rows)
                    if row.stage == "experience_review" and row.status == "completed"
                ),
                None,
            )
            if not completed_event:
                return None

            completed_payload = completed_event.payload or {}
            result = completed_payload.get("result")
            if isinstance(result, dict) and result.get("analysis_payload"):
                normalized_result = dict(result)
                persisted_tool_trace = result.get("tool_trace") or completed_payload.get("tool_trace") or []
                normalized_result["analysis_payload"] = self._normalize_analysis_payload(
                    result.get("analysis_payload") or {},
                    debate_review_context={
                        "pm_decision": {
                            "decision": completed_payload.get("original_pm_decision")
                            or completed_payload.get("recommended_action")
                        }
                    },
                    tool_trace=persisted_tool_trace,
                )
                normalized_result["tool_trace"] = persisted_tool_trace
                return normalized_result

            return await self._build_review_run_result_fallback(
                db,
                review_run_id=review_run_id,
                session_id=completed_event.session_id,
                user_id=user_id,
                completed_at=completed_event.created_at,
                completed_payload=completed_payload,
                events=rows,
            )

    async def delete_review_run(
        self,
        *,
        user_id: int,
        review_run_id: str,
    ) -> bool:
        async with database_module.AsyncSessionLocal() as db:
            run = (
                await db.execute(
                    select(ExperienceReviewRun).where(
                        ExperienceReviewRun.user_id == user_id,
                        ExperienceReviewRun.review_run_id == review_run_id,
                    )
                )
            ).scalars().first()
            rows = (
                await db.execute(
                    select(ExperienceReviewEvent)
                    .where(
                        ExperienceReviewEvent.user_id == user_id,
                        ExperienceReviewEvent.review_run_id == review_run_id,
                    )
                    .order_by(ExperienceReviewEvent.created_at.desc())
                )
            ).scalars().all()
            if run is None and not rows:
                return False
            if run is not None and run.status in RUN_ACTIVE_STATUSES:
                raise ValueError(i18n_service.t("experience.active_review_delete_forbidden"))
            if run is None and rows and rows[0].status in REVIEW_ACTIVE_EVENT_STATUSES:
                raise ValueError(i18n_service.t("experience.active_review_delete_forbidden"))

            await db.execute(
                delete(ExperienceReviewEvent).where(
                    ExperienceReviewEvent.user_id == user_id,
                    ExperienceReviewEvent.review_run_id == review_run_id,
                )
            )
            await db.execute(
                delete(ExperienceReviewRun).where(
                    ExperienceReviewRun.user_id == user_id,
                    ExperienceReviewRun.review_run_id == review_run_id,
                )
            )
            await db.commit()
            return True

    async def delete_all_review_runs(
        self,
        *,
        user_id: int,
    ) -> int:
        async with database_module.AsyncSessionLocal() as db:
            runs = (
                await db.execute(
                    select(ExperienceReviewRun.review_run_id, ExperienceReviewRun.status)
                    .where(ExperienceReviewRun.user_id == user_id)
                )
            ).all()
            rows = (
                await db.execute(
                    select(ExperienceReviewEvent.review_run_id, ExperienceReviewEvent.status)
                    .where(ExperienceReviewEvent.user_id == user_id)
                    .order_by(ExperienceReviewEvent.created_at.desc())
                )
            ).all()
            if not runs and not rows:
                return 0

            run_ids: set[str] = {review_run_id for review_run_id, _ in runs}
            active_run_ids: set[str] = {
                review_run_id for review_run_id, status in runs if status in RUN_ACTIVE_STATUSES
            }
            seen_event_run_ids: set[str] = set()
            for review_run_id, status in rows:
                if review_run_id in seen_event_run_ids:
                    continue
                seen_event_run_ids.add(review_run_id)
                run_ids.add(review_run_id)
                if status in REVIEW_ACTIVE_EVENT_STATUSES:
                    active_run_ids.add(review_run_id)

            if active_run_ids:
                raise ValueError(i18n_service.t("experience.active_review_clear_forbidden"))

            await db.execute(delete(ExperienceReviewEvent).where(ExperienceReviewEvent.user_id == user_id))
            await db.execute(delete(ExperienceReviewRun).where(ExperienceReviewRun.user_id == user_id))
            await db.commit()
            return len(run_ids)

    def _get_latest_pm_message(self, debate_messages: List[DebateMessage]) -> DebateMessage | None:
        for message in reversed(debate_messages):
            if message.agent_role == PM_AGENT_ROLE:
                return message
        return None

    async def _build_debate_review_context(
        self,
        db: AsyncSession,
        *,
        session_obj: DebateSession,
        stock_name: str,
        industry: str | None,
        debate_messages: List[DebateMessage],
        pm_message: DebateMessage,
        review_horizon: ReviewHorizon,
        market_day_count: int,
    ) -> Dict[str, Any]:
        """构建经验复盘工作流所需的完整上下文。

        Args:
            db: 数据库会话。
            session_obj: 被复盘的辩论会话。
            stock_name: 股票名称。
            industry: 股票所属行业。
            debate_messages: 会话中的辩论消息列表。
            pm_message: 最新 PM 决策消息。
            review_horizon: 当前选择的复盘周期。
            market_day_count: 决策后可用的日 K 样本数量。

        Returns:
            包含会话、PM 决策、辩论时间线、原始订单/成交和原始行情数据的复盘上下文。
        """
        pm_record = (
            await db.execute(select(PMDecisionRecord).where(PMDecisionRecord.session_id == session_obj.session_id))
        ).scalars().first()
        orders = (
            await db.execute(
                select(Order)
                .where(Order.session_id == session_obj.session_id)
                .order_by(Order.created_at.asc())
            )
        ).scalars().all()
        trades = (
            await db.execute(
                select(TradeRecord)
                .where(TradeRecord.session_id == session_obj.session_id)
                .order_by(TradeRecord.trade_time.asc(), TradeRecord.created_at.asc())
            )
        ).scalars().all()

        grouped_positions: dict[str, list[str]] = defaultdict(list)
        debate_timeline: List[Dict[str, Any]] = []
        for message in debate_messages:
            debate_timeline.append(
                {
                    "message_id": str(message.message_id),
                    "stage": message.stage,
                    "agent_role": message.agent_role,
                    "conclusion_text": _extract_original_conclusion(message),
                    "created_at": safe_isoformat(message.created_at),
                }
            )

        target_position = safe_float(pm_record.target_position if pm_record else 0.0, 0.0)
        market_outcome = await self._build_market_outcome_summary(
            db=db,
            stock_code=session_obj.stock_code,
            decision_time=pm_message.created_at,
            review_horizon=review_horizon,
            holding_horizon_days=pm_record.holding_horizon_days if pm_record else None,
        )

        return {
            "session": {
                "session_id": str(session_obj.session_id),
                "stock_code": session_obj.stock_code,
                "stock_name": stock_name,
                "industry": industry,
                "status": session_obj.status,
                "trading_frequency": session_obj.trading_frequency,
                "trading_strategy": session_obj.trading_strategy,
                "created_at": safe_isoformat(session_obj.created_at),
                "updated_at": safe_isoformat(session_obj.updated_at),
            },
            "pm_decision": {
                "message_id": str(pm_message.message_id),
                "confidence_score": _normalize_confidence(pm_record.confidence_score if pm_record else 0.0),
                "target_position": target_position,
                "stop_loss": pm_record.stop_loss if pm_record else None,
                "take_profit": pm_record.take_profit if pm_record else None,
                "holding_horizon_days": pm_record.holding_horizon_days if pm_record else None,
                "created_at": safe_isoformat(pm_message.created_at),
            },
            "debate_timeline": debate_timeline,
            "agent_position_summary": {
                role: decisions[-3:]
                for role, decisions in grouped_positions.items()
            },
            "orders": self._serialize_orders(orders),
            "trades": self._serialize_trades(trades),
            "market_outcome_summary": market_outcome,
        }

    def _collect_debate_state_from_db(
        self,
        debate_messages: List[DebateMessage],
        pm_context_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        state: Dict[str, Any] = {
            "sentiment_message": {},
            "news_message": {},
            "policy_message": {},
            "vertical_reports": {},
            "strategic_reports": {},
            "portfolio_info": pm_context_payload.get("portfolio_info") or {},
            "market_context": pm_context_payload or {},
            "pm_context_from_db": pm_context_payload or {},
        }

        for message in debate_messages:
            payload = {
                "message_id": str(message.message_id),
                "stage": message.stage,
                "round_number": message.round_number,
                "agent_name": message.agent_name,
                "agent_role": message.agent_role,
                "reasoning": message.reasoning or "",
                "prompt_input": message.prompt_input or "",
                "created_at": safe_isoformat(message.created_at),
            }

            if message.agent_role == "sentiment" and not state["sentiment_message"]:
                state["sentiment_message"] = payload
            elif message.agent_role == "news_analyst" and not state["news_message"]:
                state["news_message"] = payload
            elif message.agent_role == "policy_analyst" and not state["policy_message"]:
                state["policy_message"] = payload
            elif message.agent_role in {"fundamental", "technical", "capital_flow", "risk"}:
                state["vertical_reports"][message.agent_role] = payload
            elif message.agent_role in {"bull", "bear", "aggressive", "conservative", "neutral"}:
                state["strategic_reports"][message.agent_role] = payload

        return state

    def _extract_pm_input_from_db(
        self,
        pm_message: DebateMessage,
        debate_state: Dict[str, Any],
        pm_context_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "source": "debate_messages.prompt_input",
            "message_id": str(pm_message.message_id),
            "agent_role": pm_message.agent_role,
            "stage": pm_message.stage,
            "created_at": safe_isoformat(pm_message.created_at),
            "prompt_input": pm_message.prompt_input or "",
            "context_from_db": pm_context_payload or {},
            "supporting_messages_from_db": {
                "sentiment_message": debate_state.get("sentiment_message") or {},
                "news_message": debate_state.get("news_message") or {},
                "policy_message": debate_state.get("policy_message") or {},
                "vertical_reports": debate_state.get("vertical_reports") or {},
                "strategic_reports": debate_state.get("strategic_reports") or {},
                "portfolio_info": debate_state.get("portfolio_info") or {},
                "market_context": debate_state.get("market_context") or {},
            },
        }

    def _extract_context_json_from_prompt(self, prompt_input: str) -> Dict[str, Any]:
        text = str(prompt_input or "")
        marker = "User: Context:"
        marker_index = text.find(marker)
        if marker_index < 0:
            marker = "Context:"
            marker_index = text.find(marker)
        if marker_index < 0:
            return {}

        candidate = text[marker_index + len(marker):].lstrip()
        first_brace = candidate.find("{")
        if first_brace < 0:
            return {}

        candidate = candidate[first_brace:]
        try:
            parsed, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def _build_market_outcome_summary(
        self,
        db: AsyncSession,
        *,
        stock_code: str,
        decision_time: datetime | None,
        review_horizon: ReviewHorizon = "20d",
        holding_horizon_days: int | None = None,
    ) -> Dict[str, Any]:
        """构建决策后的原始行情数据摘要。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            decision_time: PM 决策时间。
            review_horizon: 当前选择的复盘周期。
            holding_horizon_days: PM 声明的持有或复议天数，按自然日换算到期日。

        Returns:
            包含复盘窗口、最新窗口、持有窗口和沪深300原始日线的字典；不包含计算指标。
        """
        if not decision_time:
            return {}

        decision_date = decision_time.date()
        required_rows = HORIZON_REQUIRED_MARKET_DAYS[review_horizon]
        review_rows = (
            await db.execute(
                select(
                    KlineData.date,
                    KlineData.open,
                    KlineData.high,
                    KlineData.low,
                    KlineData.close,
                    KlineData.volume,
                    KlineData.turnover,
                )
                .where(
                    KlineData.stock_code == stock_code,
                    KlineData.freq == "D",
                    KlineData.date >= decision_date,
                )
                .order_by(KlineData.date.asc())
                .limit(required_rows)
            )
        ).all()
        if len(review_rows) < required_rows:
            return {}

        latest_rows = list(
            reversed(
                (
                    await db.execute(
                        select(
                            KlineData.date,
                            KlineData.open,
                            KlineData.high,
                            KlineData.low,
                            KlineData.close,
                            KlineData.volume,
                            KlineData.turnover,
                        )
                        .where(
                            KlineData.stock_code == stock_code,
                            KlineData.freq == "D",
                            KlineData.date >= decision_date,
                        )
                        .order_by(KlineData.date.desc())
                        .limit(REVIEW_LATEST_OUTCOME_ROWS)
                    )
                ).all()
            )
        )
        if not latest_rows:
            return {}

        holding_rows: List[Any] = []
        holding_deadline: date | None = None
        if holding_horizon_days and holding_horizon_days > 0:
            holding_deadline = decision_date + timedelta(days=holding_horizon_days)
            estimated_holding_rows = int(holding_horizon_days * 5 / 7) + 10
            holding_fetch_limit = max(
                required_rows,
                min(REVIEW_DECISION_WINDOW_MAX_ROWS, estimated_holding_rows),
            )
            raw_holding_rows = (
                await db.execute(
                    select(
                        KlineData.date,
                        KlineData.open,
                        KlineData.high,
                        KlineData.low,
                        KlineData.close,
                        KlineData.volume,
                        KlineData.turnover,
                    )
                    .where(
                        KlineData.stock_code == stock_code,
                        KlineData.freq == "D",
                        KlineData.date >= decision_date,
                    )
                    .order_by(KlineData.date.asc())
                    .limit(holding_fetch_limit)
                )
            ).all()
            for row in raw_holding_rows:
                holding_rows.append(row)
                if row.date >= holding_deadline:
                    break

        all_dates = sorted(
            {
                row.date
                for row in [*review_rows, *latest_rows, *holding_rows]
            }
        )
        benchmark_rows = []
        if all_dates:
            benchmark_rows = (
                await db.execute(
                    select(
                        IndexDaily.trade_date,
                        IndexDaily.open,
                        IndexDaily.high,
                        IndexDaily.low,
                        IndexDaily.close,
                        IndexDaily.volume,
                    )
                    .where(
                        IndexDaily.index_code == REVIEW_BENCHMARK_INDEX_CODE,
                        IndexDaily.trade_date.in_(all_dates),
                    )
                    .order_by(IndexDaily.trade_date.asc())
                )
            ).all()

        def serialize_stock_rows(rows: List[Any]) -> List[Dict[str, Any]]:
            return [
                {
                    "date": row.date.isoformat(),
                    "open": float(row.open) if row.open is not None else None,
                    "high": float(row.high) if row.high is not None else None,
                    "low": float(row.low) if row.low is not None else None,
                    "close": float(row.close) if row.close is not None else None,
                    "volume": float(row.volume) if row.volume is not None else None,
                    "turnover": float(row.turnover) if row.turnover is not None else None,
                }
                for row in rows
            ]

        def serialize_index_rows(rows: List[Any]) -> List[Dict[str, Any]]:
            return [
                {
                    "date": row.trade_date.isoformat(),
                    "open": float(row.open) if row.open is not None else None,
                    "high": float(row.high) if row.high is not None else None,
                    "low": float(row.low) if row.low is not None else None,
                    "close": float(row.close) if row.close is not None else None,
                    "volume": float(row.volume) if row.volume is not None else None,
                }
                for row in rows
            ]

        review_window = {
            "horizon": review_horizon,
            "decision_date": decision_date.isoformat(),
            "start_date": review_rows[0].date.isoformat(),
            "end_date": review_rows[-1].date.isoformat(),
            "trading_days": len(review_rows),
            "rows": serialize_stock_rows(review_rows),
        }
        latest_window = {
            "start_date": latest_rows[0].date.isoformat(),
            "end_date": latest_rows[-1].date.isoformat(),
            "trading_days": len(latest_rows),
            "rows": serialize_stock_rows(latest_rows),
        }
        holding_window = None
        if holding_deadline is not None:
            reached = bool(holding_rows and holding_rows[-1].date >= holding_deadline)
            holding_window = {
                "deadline_date": holding_deadline.isoformat(),
                "start_date": holding_rows[0].date.isoformat() if holding_rows else None,
                "end_date": holding_rows[-1].date.isoformat() if holding_rows else None,
                "trading_days": len(holding_rows),
                "reached": reached,
                "rows": serialize_stock_rows(holding_rows),
            }
        benchmark_window = {
            "index_code": REVIEW_BENCHMARK_INDEX_CODE,
            "start_date": (
                benchmark_rows[0].trade_date.isoformat() if benchmark_rows else None
            ),
            "end_date": (
                benchmark_rows[-1].trade_date.isoformat() if benchmark_rows else None
            ),
            "trading_days": len(benchmark_rows),
            "rows": serialize_index_rows(benchmark_rows),
        }
        data_range = {
            "review": {
                "start_date": review_window["start_date"],
                "end_date": review_window["end_date"],
                "trading_days": review_window["trading_days"],
            },
            "latest": {
                "start_date": latest_window["start_date"],
                "end_date": latest_window["end_date"],
                "trading_days": latest_window["trading_days"],
            },
            "holding": (
                {
                    "start_date": holding_window["start_date"],
                    "end_date": holding_window["end_date"],
                    "trading_days": holding_window["trading_days"],
                }
                if holding_window is not None
                else None
            ),
            "benchmark": {
                "index_code": benchmark_window["index_code"],
                "start_date": benchmark_window["start_date"],
                "end_date": benchmark_window["end_date"],
                "trading_days": benchmark_window["trading_days"],
            },
        }
        time_range = {
            "start_date": all_dates[0].isoformat(),
            "end_date": all_dates[-1].isoformat(),
        }
        return {
            "stock_code": stock_code,
            "decision_date": decision_date.isoformat(),
            "selected_horizon": review_horizon,
            "data_range": data_range,
            "time_range": time_range,
            "review_window": review_window,
            "latest_window": latest_window,
            "holding_window": holding_window,
            "benchmark_window": benchmark_window,
        }

    def _serialize_orders(self, orders: List[Order]) -> List[Dict[str, Any]]:
        """序列化原始订单记录。

        Args:
            orders: 当前 Debate session 关联的订单列表。

        Returns:
            仅包含数据库原始字段的订单字典列表。
        """
        return [
            {
                "order_id": str(order.order_id),
                "action": order.action,
                "order_type": order.order_type,
                "price": float(order.price) if order.price is not None else None,
                "shares": int(order.shares) if order.shares is not None else None,
                "status": order.status,
                "filled_shares": (
                    int(order.filled_shares) if order.filled_shares is not None else None
                ),
                "avg_fill_price": (
                    float(order.avg_fill_price) if order.avg_fill_price is not None else None
                ),
                "realized_pnl": (
                    float(order.realized_pnl) if order.realized_pnl is not None else None
                ),
                "created_at": safe_isoformat(order.created_at),
                "filled_at": safe_isoformat(order.filled_at),
                "source": order.source,
            }
            for order in orders
        ]

    def _serialize_trades(self, trades: List[TradeRecord]) -> List[Dict[str, Any]]:
        """序列化原始成交记录。

        Args:
            trades: 当前 Debate session 关联的成交列表。

        Returns:
            仅包含数据库原始字段的成交字典列表。
        """
        return [
            {
                "trade_id": str(trade.trade_id),
                "order_id": str(trade.order_id) if trade.order_id else None,
                "action": trade.action,
                "quantity": int(trade.quantity) if trade.quantity is not None else None,
                "fill_price": (
                    float(trade.fill_price) if trade.fill_price is not None else None
                ),
                "commission": (
                    float(trade.commission) if trade.commission is not None else None
                ),
                "stamp_duty": (
                    float(trade.stamp_duty) if trade.stamp_duty is not None else None
                ),
                "transfer_fee": (
                    float(trade.transfer_fee) if trade.transfer_fee is not None else None
                ),
                "total_fees": (
                    float(trade.total_fees) if trade.total_fees is not None else None
                ),
                "net_amount": (
                    float(trade.net_amount) if trade.net_amount is not None else None
                ),
                "trade_time": safe_isoformat(trade.trade_time),
            }
            for trade in trades
        ]

    def _normalize_action(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text in VALID_ACTIONS else "watch"

    def _extract_written_memories_from_tool_trace(self, tool_trace: Any) -> List[Dict[str, Any]]:
        if not isinstance(tool_trace, list):
            return []

        items: List[Dict[str, Any]] = []
        for entry in tool_trace:
            if not isinstance(entry, dict) or entry.get("name") != "write_memory":
                continue
            args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
            content = str(args.get("content") or "").strip()
            result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
            stock_code = str(args.get("stock_code") or result.get("stock_code") or "").strip() or None
            status = str(
                result.get("status")
                or (
                    "success"
                    if result.get("success") is True
                    else "failed"
                    if result.get("success") is False or result.get("error")
                    else "unknown"
                )
            )
            item: Dict[str, Any] = {
                "status": status,
                "stock_code": stock_code,
                "max_chars": result.get("max_chars") or settings.MEMORY_DOC_MAX_CHARS,
            }
            size_chars = result.get("size_chars") or (len(content) if content else None)
            if size_chars not in (None, ""):
                item["size_chars"] = size_chars
            for key in ("error", "version"):
                value = result.get(key)
                if value not in (None, ""):
                    item[key] = value
            if not content and not result and not stock_code:
                continue
            items.append(item)
        return items

    def _normalize_written_memories(
        self,
        value: Any,
        *,
        tool_trace: Any = None,
    ) -> List[Dict[str, Any]]:
        source_items = value if isinstance(value, list) else self._extract_written_memories_from_tool_trace(tool_trace)
        if not source_items and tool_trace:
            source_items = self._extract_written_memories_from_tool_trace(tool_trace)
        normalized_items: List[Dict[str, Any]] = []
        for item in source_items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            status = str(item.get("status") or ("failed" if item.get("error") else "success" if content else "unknown"))
            stock_code = str(item.get("stock_code") or "").strip() or None
            if not content and not item.get("size_chars") and not item.get("error") and not stock_code:
                continue
            normalized_item: Dict[str, Any] = {
                "status": status,
                "stock_code": stock_code,
                "max_chars": item.get("max_chars") or settings.MEMORY_DOC_MAX_CHARS,
            }
            size_chars = item.get("size_chars") or (len(content) if content else None)
            if size_chars not in (None, ""):
                normalized_item["size_chars"] = size_chars
            for key in ("error", "version"):
                value = item.get(key)
                if value not in (None, ""):
                    normalized_item[key] = str(value) if key == "error" else value
            normalized_items.append(normalized_item)
        return normalized_items

    def _normalize_analysis_payload(
        self,
        payload: Dict[str, Any],
        *,
        debate_review_context: Dict[str, Any],
        tool_trace: Any = None,
    ) -> Dict[str, Any]:
        """规范化经验复盘分析结果 payload。

        Args:
            payload: 工作流输出的原始分析结果。
            debate_review_context: 复盘上下文，用于补齐 PM 决策和证据链。
            tool_trace: 工具调用轨迹，用于回填写入记忆信息。

        Returns:
            字段类型稳定的精简分析结果；完整工具轨迹由顶层结果单独保留。
        """
        raw_payload = payload if isinstance(payload, dict) else {}
        original_pm = debate_review_context.get("pm_decision") or {}
        original_decision = str(
            raw_payload.get("original_pm_decision")
            or original_pm.get("decision")
            or raw_payload.get("recommended_action")
            or ""
        ).strip()
        correctness_reasoning = str(
            raw_payload.get("correctness_reasoning")
            or raw_payload.get("thesis_summary")
            or raw_payload.get("market_experience_summary")
            or ""
        ).strip()
        debate_correctness = (
            str(raw_payload.get("debate_correctness") or "").strip().lower()
            if str(raw_payload.get("debate_correctness") or "").strip().lower() in CORRECTNESS_BUCKETS
            else "inconclusive"
        )
        normalized = {
            "original_pm_decision": original_decision,
            "debate_correctness": debate_correctness,
            "correctness_reasoning": correctness_reasoning,
            "written_memories": self._normalize_written_memories(
                raw_payload.get("written_memories"),
                tool_trace=tool_trace,
            ),
        }
        return normalized

    def _build_completed_event_payload(
        self,
        *,
        result: Dict[str, Any],
        recommended_action: str | None,
        debate_correctness: str | None,
    ) -> Dict[str, Any]:
        """构建复盘完成事件的 payload。

        Args:
            result: 已完成的经验复盘结果。
            recommended_action: 复盘建议动作。
            debate_correctness: 原始辩论结论正确性。

        Returns:
            可持久化到复盘事件中的完成 payload。
        """
        return {
            "tool_trace": result.get("tool_trace") or [],
            "review_horizon": result.get("review_horizon"),
            "market_day_count": result.get("market_day_count"),
            "recommended_action": recommended_action,
            "debate_correctness": debate_correctness,
            "original_pm_decision": (result.get("analysis_payload") or {}).get("original_pm_decision"),
            "correctness_reasoning": (result.get("analysis_payload") or {}).get("correctness_reasoning"),
            "result": self._serialize_review_result(result),
        }

    def _serialize_review_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """序列化经验复盘结果用于事件持久化。

        Args:
            result: 内部经验复盘结果。

        Returns:
            仅包含可 JSON 序列化字段的复盘结果。
        """
        return {
            "review_run_id": result.get("review_run_id"),
            "review_horizon": result.get("review_horizon"),
            "market_day_count": result.get("market_day_count"),
            "session_id": str(result.get("session_id")) if result.get("session_id") else None,
            "stock_code": result.get("stock_code"),
            "stock_name": result.get("stock_name"),
            "industry": result.get("industry"),
            "style_bucket": result.get("style_bucket"),
            "trading_frequency": result.get("trading_frequency"),
            "trading_strategy": result.get("trading_strategy"),
            "analysis_date": safe_isoformat(result.get("analysis_date")),
            "reviewed_at": safe_isoformat(result.get("reviewed_at")),
            "analysis_payload": result.get("analysis_payload") or {},
            "tool_trace": result.get("tool_trace") or [],
        }

    async def _build_review_run_result_fallback(
        self,
        db,
        *,
        review_run_id: str,
        session_id: UUID,
        user_id: int,
        completed_at: datetime,
        completed_payload: Dict[str, Any],
        events: List[ExperienceReviewEvent],
    ) -> Dict[str, Any] | None:
        """从事件记录回退构建复盘运行结果。

        Args:
            db: 数据库会话。
            review_run_id: 经验复盘运行 ID。
            session_id: 辩论会话 ID。
            user_id: 用户 ID。
            completed_at: 完成事件时间。
            completed_payload: 完成事件中保存的 payload。
            events: 同一复盘运行下的事件列表。

        Returns:
            可供 API 返回的复盘结果；会话不存在时返回 ``None``。
        """
        session_obj = (
            await db.execute(
                select(DebateSession).where(
                    DebateSession.user_id == user_id,
                    DebateSession.session_id == session_id,
                )
            )
        ).scalars().first()
        if not session_obj:
            return None

        stock = (
            await db.execute(select(StockBasic).where(StockBasic.stock_code == session_obj.stock_code))
        ).scalars().first()
        stock_name = stock.name if stock else session_obj.stock_code
        industry = stock.industry if stock else None
        style_bucket = _style_bucket_from_frequency(session_obj.trading_frequency)
        pm_message = (
            await db.execute(
                select(DebateMessage)
                .where(
                    DebateMessage.session_id == session_id,
                    DebateMessage.agent_role == PM_AGENT_ROLE,
                )
                .order_by(DebateMessage.created_at.desc())
            )
        ).scalars().first()
        tool_trace = completed_payload.get("tool_trace")
        if not isinstance(tool_trace, list) or not tool_trace:
            tool_trace = [
                {
                    "name": item.payload.get("tool_name"),
                    "args": item.payload.get("args") or {},
                }
                for item in events
                if item.stage == "tool_call" and (item.payload or {}).get("tool_name")
            ]

        analysis_payload = {
            "original_pm_decision": str(
                completed_payload.get("original_pm_decision")
                or getattr(pm_message, "decision", None)
                or completed_payload.get("recommended_action")
                or ""
            ).strip(),
            "debate_correctness": (
                str(completed_payload.get("debate_correctness") or "").strip().lower()
                if str(completed_payload.get("debate_correctness") or "").strip().lower() in CORRECTNESS_BUCKETS
                else "inconclusive"
            ),
            "correctness_reasoning": str(completed_payload.get("correctness_reasoning") or "").strip(),
            "written_memories": self._extract_written_memories_from_tool_trace(tool_trace),
        }
        return {
            "review_run_id": review_run_id,
            "review_horizon": completed_payload.get("review_horizon") or "20d",
            "market_day_count": completed_payload.get("market_day_count"),
            "session_id": str(session_obj.session_id),
            "stock_code": session_obj.stock_code,
            "stock_name": stock_name,
            "industry": industry,
            "style_bucket": style_bucket,
            "trading_frequency": session_obj.trading_frequency,
            "trading_strategy": session_obj.trading_strategy,
            "analysis_date": safe_isoformat(pm_message.created_at if pm_message else completed_at),
            "reviewed_at": safe_isoformat(completed_at),
            "analysis_payload": analysis_payload,
            "tool_trace": tool_trace,
        }

    async def _push_review_update(
        self,
        *,
        debate_session_id: str,
        review_run_id: str | None,
        stage: str,
        status: str,
        message: str = "",
        message_key: str | None = None,
        message_params: Dict[str, Any] | None = None,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        try:
            await ws_manager.send_experience_review_update(
                debate_session_id=debate_session_id,
                review_run_id=review_run_id,
                stage=stage,
                status=status,
                message=message,
                message_key=message_key,
                message_params=message_params,
                payload=payload,
            )
        except Exception:
            pass

    async def _persist_review_event(
        self,
        db: AsyncSession,
        *,
        review_run_id: str,
        session_id: UUID,
        user_id: int,
        stage: str,
        status: str,
        message_key: str | None,
        message_params: Dict[str, Any] | None = None,
        payload: Dict[str, Any] | None = None,
        event_type: str = "experience_review_update",
    ) -> None:
        row = ExperienceReviewEvent(
            review_run_id=review_run_id,
            session_id=session_id,
            user_id=user_id,
            event_type=event_type,
            stage=stage,
            status=status,
            message_key=message_key,
            message_params=message_params or {},
            payload=payload or {},
        )
        db.add(row)
        await db.commit()

    async def _persist_review_event_batch(
        self,
        db: AsyncSession,
        *,
        review_run_id: str,
        session_id: UUID,
        user_id: int,
        events: List[Dict[str, Any]],
    ) -> None:
        rows = [
            ExperienceReviewEvent(
                review_run_id=review_run_id,
                session_id=session_id,
                user_id=user_id,
                event_type=str(item.get("event_type") or "experience_review_update"),
                stage=str(item.get("stage") or "experience_review"),
                status=str(item.get("status") or "running"),
                message_key=item.get("message_key"),
                message_params=item.get("message_params") or {},
                payload=item.get("payload") or {},
            )
            for item in events
        ]
        if not rows:
            return
        db.add_all(rows)
        await db.commit()


experience_service = ExperienceService()
