from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json
from statistics import mean
from typing import Any, Dict, List
from uuid import UUID
import uuid

from sqlalchemy import delete, func, select
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
from app.ai.experience.index_service import experience_index_service
from app.core import database as database_module
from app.core.i18n import i18n_service
from app.core.logger import get_logger
from app.core.utils.converters import safe_float, safe_isoformat
from app.ai.experience.workflow import create_experience_workflow
from app.models.data_storage import KlineData, StockBasic
from app.models.debate_message import DebateMessage
from app.models.experience_review_event import ExperienceReviewEvent
from app.models.order import Order
from app.models.pm_decision import PMDecisionRecord
from app.models.session import Session as DebateSession
from app.models.trade_record import TradeRecord
from app.websocket.manager import ws_manager


logger = get_logger(__name__)

VALID_STYLE_BUCKETS = {"short_term", "swing", "position", "long_term"}
VALID_ACTIONS = {"avoid", "watch", "buy", "add", "hold", "reduce", "sell"}
CORRECTNESS_BUCKETS = {"correct", "partially_correct", "incorrect", "inconclusive"}
ACTIVE_REVIEW_STATUSES = {"started", "running"}
STALE_REVIEW_TIMEOUT = timedelta(hours=2)
PM_AGENT_ROLE = "portfolio_manager"


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


def _normalize_memory_importance(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in {"low", "medium", "high"} else "medium"


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


def _weighted_buy_fill_price(trades: List[TradeRecord]) -> float | None:
    """计算买入成交记录的加权均价。

    Args:
        trades: 当前 Debate session 关联的成交记录列表。

    Returns:
        有买入成交时返回按成交股数加权的均价；没有有效买入成交时返回 None。
    """
    total_quantity = 0
    total_amount = 0.0
    for trade in trades:
        if str(trade.action or "").lower() != "buy":
            continue
        quantity = int(trade.quantity or 0)
        fill_price = safe_float(trade.fill_price)
        if quantity <= 0 or fill_price is None or fill_price <= 0:
            continue
        total_quantity += quantity
        total_amount += quantity * fill_price
    if total_quantity <= 0:
        return None
    return total_amount / total_quantity


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
            elif row.status in ACTIVE_REVIEW_STATUSES and horizon not in session_bucket["active"]:
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
                elif row.status in ACTIVE_REVIEW_STATUSES and horizon not in session_bucket["active"]:
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
            if status in ACTIVE_REVIEW_STATUSES:
                return review_run_id
        return None

    async def analyze(
        self,
        *,
        user_id: int,
        session_id: UUID,
        review_horizon: str | None = None,
    ) -> Dict[str, Any]:
        """运行单个辩论会话的经验复盘。

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

                await self._cleanup_stale_review_runs(db, user_id=user_id, session_id=session_id)
                active_review_run_id = await self._get_active_review_run_id(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                    cleanup_stale=False,
                )
                if active_review_run_id:
                    raise ValueError(
                        f"Experience review is already running for session {session_id} (run {active_review_run_id})"
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
            completed_payload = self._build_completed_event_payload(
                result=result,
                recommended_action=normalized_payload.get("recommended_action"),
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
            try:
                async with database_module.AsyncSessionLocal() as db:
                    await experience_index_service.sync_from_review_result(db, user_id=user_id, result=result)
            except Exception as index_exc:
                logger.warning(
                    "experience index sync failed",
                    extra={
                        "review_run_id": review_run_id,
                        "user_id": user_id,
                        "error": str(index_exc),
                    },
                    exc_info=True,
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
                return result

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
            if not rows:
                return False
            if rows[0].status in ACTIVE_REVIEW_STATUSES:
                raise ValueError(i18n_service.t("experience.active_review_delete_forbidden"))

            await db.execute(
                delete(ExperienceReviewEvent).where(
                    ExperienceReviewEvent.user_id == user_id,
                    ExperienceReviewEvent.review_run_id == review_run_id,
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
            rows = (
                await db.execute(
                    select(ExperienceReviewEvent.review_run_id, ExperienceReviewEvent.status)
                    .where(ExperienceReviewEvent.user_id == user_id)
                    .order_by(ExperienceReviewEvent.created_at.desc())
                )
            ).all()
            if not rows:
                return 0

            seen_run_ids: set[str] = set()
            active_run_ids: list[str] = []
            run_ids: list[str] = []
            for review_run_id, status in rows:
                if review_run_id in seen_run_ids:
                    continue
                seen_run_ids.add(review_run_id)
                run_ids.append(review_run_id)
                if status in ACTIVE_REVIEW_STATUSES:
                    active_run_ids.append(review_run_id)

            if active_run_ids:
                raise ValueError(i18n_service.t("experience.active_review_clear_forbidden"))

            await db.execute(delete(ExperienceReviewEvent).where(ExperienceReviewEvent.user_id == user_id))
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
            包含会话、PM 决策、辩论时间线、执行摘要和市场结果的复盘上下文。
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
        buy_fill_price = _weighted_buy_fill_price(trades)
        market_outcome = await self._build_market_outcome_summary(
            db=db,
            stock_code=session_obj.stock_code,
            industry=industry,
            decision_time=pm_message.created_at,
            review_horizon=review_horizon,
            market_day_count=market_day_count,
            entry_price_override=buy_fill_price,
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
            "execution_summary": self._build_execution_summary(orders, trades),
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
        industry: str | None,
        decision_time: datetime | None,
        review_horizon: ReviewHorizon = "20d",
        market_day_count: int = 0,
        entry_price_override: float | None = None,
    ) -> Dict[str, Any]:
        """构建决策后行情结果摘要。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            industry: 股票所属行业。
            decision_time: PM 决策时间。
            review_horizon: 当前选择的复盘周期。
            market_day_count: 决策后可用的日 K 样本数量。
            entry_price_override: 实际买入成交均价；存在时优先作为收益计算入口价。

        Returns:
            包含所选周期收益、各周期收益、回撤和相对收益的行情结果摘要。
        """
        if not decision_time:
            return {}

        snapshot_day = decision_time.date()
        rows = (
            await db.execute(
                select(KlineData.date, KlineData.open, KlineData.close, KlineData.high, KlineData.low)
                .where(
                    KlineData.stock_code == stock_code,
                    KlineData.freq == "D",
                    KlineData.date >= snapshot_day,
                )
                .order_by(KlineData.date.asc())
                .limit(130)
            )
        ).all()
        if not rows:
            return {}

        closes = [float(item.close) for item in rows if item.close is not None]
        highs = [float(item.high) for item in rows if item.high is not None]
        lows = [float(item.low) for item in rows if item.low is not None]
        entry_price = entry_price_override if entry_price_override and entry_price_override > 0 else (
            closes[0] if closes else None
        )
        if not entry_price:
            return {}
        entry_price_source = (
            "trade_fill_price"
            if entry_price_override and entry_price_override > 0
            else "decision_day_close"
        )

        def compute_return(index: int) -> float | None:
            if not closes or len(closes) <= index:
                return None
            target = closes[index]
            return (target / entry_price) - 1 if target is not None else None

        def compute_drawdown(limit: int) -> float | None:
            if len(closes) <= limit:
                return None
            subset = closes[: limit + 1]
            if not subset:
                return None
            peak = subset[0]
            max_dd = 0.0
            for price in subset:
                if price > peak:
                    peak = price
                if peak > 0:
                    max_dd = min(max_dd, (price / peak) - 1)
            return max_dd

        index_rows = (
            await db.execute(
                select(KlineData.close)
                .where(
                    KlineData.stock_code == "000300.SH",
                    KlineData.freq == "D",
                    KlineData.date >= snapshot_day,
                )
                .order_by(KlineData.date.asc())
                .limit(21)
            )
        ).all()
        index_closes = [float(item.close) for item in index_rows if item.close is not None]
        relative_return_vs_index = None
        if len(index_closes) > 20:
            index_return_20 = (index_closes[20] / index_closes[0]) - 1
            stock_return_20 = compute_return(20)
            if stock_return_20 is not None:
                relative_return_vs_index = stock_return_20 - index_return_20

        relative_return_vs_industry = await self._compute_relative_vs_industry(
            db=db,
            stock_code=stock_code,
            industry=industry,
            snapshot_day=snapshot_day,
            stock_return=compute_return(20),
        )

        horizon_returns = {
            "5d": {
                "available": compute_return(5) is not None,
                "close_return": compute_return(5),
                "max_drawdown": compute_drawdown(5),
            },
            "20d": {
                "available": compute_return(20) is not None,
                "close_return": compute_return(20),
                "max_drawdown": compute_drawdown(20),
                "relative_return_vs_index": relative_return_vs_index,
                "relative_return_vs_industry": relative_return_vs_industry,
            },
            "60d": {
                "available": compute_return(60) is not None,
                "close_return": compute_return(60),
                "max_drawdown": compute_drawdown(60),
            },
        }
        selected = horizon_returns[review_horizon]
        return {
            "entry_date": rows[0].date.isoformat() if rows else None,
            "entry_price": entry_price,
            "entry_price_source": entry_price_source,
            "market_day_count": market_day_count,
            "selected_horizon": review_horizon,
            "selected_horizon_outcome": {
                "horizon": review_horizon,
                "absolute_return": selected.get("close_return"),
                "max_drawdown": selected.get("max_drawdown"),
                "relative_return_vs_index": selected.get("relative_return_vs_index"),
                "relative_return_vs_industry": selected.get("relative_return_vs_industry"),
            },
            "horizon_returns": horizon_returns,
            "close_5d_return": compute_return(5),
            "close_20d_return": compute_return(20),
            "close_60d_return": compute_return(60),
            "max_drawdown_20d": compute_drawdown(20),
            "max_drawdown_60d": compute_drawdown(60),
            "relative_return_vs_index": relative_return_vs_index,
            "relative_return_vs_industry": relative_return_vs_industry,
            "sample_closes": closes[:20],
            "sample_highs": highs[:20],
            "sample_lows": lows[:20],
        }

    async def _compute_relative_vs_industry(
        self,
        db: AsyncSession,
        *,
        stock_code: str,
        industry: str | None,
        snapshot_day: datetime.date,
        stock_return: float | None,
    ) -> float | None:
        if not industry or stock_return is None:
            return None

        peer_codes = [
            code
            for code, in (
                await db.execute(
                    select(StockBasic.stock_code)
                    .where(StockBasic.industry == industry, StockBasic.stock_code != stock_code)
                    .limit(40)
                )
            ).all()
        ]
        if not peer_codes:
            return None

        rows = (
            await db.execute(
                select(KlineData.stock_code, KlineData.close)
                .where(
                    KlineData.stock_code.in_(peer_codes),
                    KlineData.freq == "D",
                    KlineData.date >= snapshot_day,
                )
                .order_by(KlineData.stock_code.asc(), KlineData.date.asc())
            )
        ).all()
        buckets: dict[str, list[float]] = defaultdict(list)
        for peer_code, close in rows:
            if close is not None:
                buckets[peer_code].append(float(close))
        peer_returns: List[float] = []
        for prices in buckets.values():
            if len(prices) > 20 and prices[0] > 0:
                peer_returns.append((prices[20] / prices[0]) - 1)
        if not peer_returns:
            return None
        return stock_return - mean(peer_returns)

    def _build_execution_summary(self, orders: List[Order], trades: List[TradeRecord]) -> Dict[str, Any]:
        filled_orders = [item for item in orders if item.status == "filled"]
        fill_prices = [value for value in (safe_float(item.fill_price) for item in trades) if value is not None]
        return {
            "order_count": len(orders),
            "filled_order_count": len(filled_orders),
            "trade_count": len(trades),
            "actions": [str(item.action or "").lower() for item in trades[-10:]],
            "avg_fill_price": mean(fill_prices) if fill_prices else None,
            "total_quantity": sum(int(item.quantity or 0) for item in trades),
            "total_fees": sum(safe_float(item.total_fees, 0.0) for item in trades),
            "realized_pnl": sum(
                safe_float(item.realized_pnl, 0.0)
                for item in orders
                if getattr(item, "realized_pnl", None) is not None
            ),
            "latest_trade_time": safe_isoformat(trades[-1].trade_time if trades else None),
            "latest_order_time": safe_isoformat(orders[-1].created_at if orders else None),
        }

    def _normalize_action(self, value: Any) -> str:
        text = str(value or "").strip().lower()
        return text if text in VALID_ACTIONS else "watch"

    def _normalize_string_list(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value in (None, ""):
            return []
        return [str(value).strip()]

    def _normalize_signal_items(self, value: Any) -> List[Dict[str, str]]:
        """规范化信号复盘条目列表。

        Args:
            value: 工作流输出的原始信号条目。

        Returns:
            仅保留有效 signal 字段后的标准化信号条目列表。
        """
        if not isinstance(value, list):
            return []
        items: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            signal = str(item.get("signal") or "").strip()
            if not signal:
                continue
            items.append(
                {
                    "signal": signal,
                    "evidence": str(item.get("evidence") or ""),
                    "impact": str(item.get("impact") or "medium"),
                    "lesson": str(item.get("lesson") or ""),
                }
            )
        return items

    def _normalize_review_triads(self, value: Any, *, original_pm: Dict[str, Any]) -> Dict[str, Any]:
        """规范化经验复盘三件套输出。

        Args:
            value: 工作流输出的原始三件套结构。
            original_pm: 原始 PM 决策信息，用于补齐缺失字段。

        Returns:
            包含原判断、信号验证和决策流程改进的标准化三件套。
        """
        raw = value if isinstance(value, dict) else {}
        original = raw.get("original_judgment") if isinstance(raw.get("original_judgment"), dict) else {}
        signals = raw.get("signal_validation") if isinstance(raw.get("signal_validation"), dict) else {}
        improvements = raw.get("decision_process_improvement") if isinstance(
            raw.get("decision_process_improvement"),
            dict,
        ) else {}
        verdict = str(original.get("verdict") or "inconclusive").strip().lower()
        if verdict not in CORRECTNESS_BUCKETS:
            verdict = "inconclusive"
        return {
            "original_judgment": {
                "verdict": verdict,
                "score": max(0.0, min(100.0, safe_float(original.get("score"), 50.0))),
                "pm_decision": str(original.get("pm_decision") or ""),
                "outcome_basis": str(original.get("outcome_basis") or ""),
                "reasoning": str(original.get("reasoning") or ""),
            },
            "signal_validation": {
                "validated_signals": self._normalize_signal_items(signals.get("validated_signals")),
                "invalidated_signals": self._normalize_signal_items(signals.get("invalidated_signals")),
                "noise_signals": [
                    {
                        "signal": str(item.get("signal") or ""),
                        "reason": str(item.get("reason") or ""),
                    }
                    for item in signals.get("noise_signals", [])
                    if isinstance(item, dict) and str(item.get("signal") or "").strip()
                ],
            },
            "decision_process_improvement": {
                "debate_changes": self._normalize_string_list(improvements.get("debate_changes")),
                "pm_changes": self._normalize_string_list(improvements.get("pm_changes")),
                "risk_control_changes": self._normalize_string_list(improvements.get("risk_control_changes")),
            },
        }

    def _normalize_experience_tags(self, value: Any) -> Dict[str, List[str]]:
        """规范化经验标签输出。

        Args:
            value: 工作流输出的原始标签结构。

        Returns:
            按股票、行业、策略、失败教训、仓位纪律、信号和市场状态分类的标签。
        """
        raw = value if isinstance(value, dict) else {}
        return {
            "stock_tags": self._normalize_string_list(raw.get("stock_tags")),
            "industry_tags": self._normalize_string_list(raw.get("industry_tags")),
            "strategy_tags": self._normalize_string_list(raw.get("strategy_tags")),
            "failure_lesson_tags": self._normalize_string_list(raw.get("failure_lesson_tags")),
            "position_discipline_tags": self._normalize_string_list(raw.get("position_discipline_tags")),
            "signal_tags": self._normalize_string_list(raw.get("signal_tags")),
            "market_regime_tags": self._normalize_string_list(raw.get("market_regime_tags")),
        }

    def _extract_written_memories_from_tool_trace(self, tool_trace: Any) -> List[Dict[str, Any]]:
        if not isinstance(tool_trace, list):
            return []

        items: List[Dict[str, Any]] = []
        for entry in tool_trace:
            if not isinstance(entry, dict) or entry.get("name") != "write_memory":
                continue
            args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
            content = str(args.get("content") or "").strip()
            if not content:
                continue
            result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
            stock_code = str(args.get("stock_code") or result.get("stock_code") or "").strip() or None
            memo_session = str(result.get("memo_session") or "").strip().lower()
            if memo_session not in {"stock", "general"}:
                memo_session = "stock" if stock_code else "general"
            item: Dict[str, Any] = {
                "content": content,
                "importance": _normalize_memory_importance(args.get("importance")),
                "memo_session": memo_session,
                "stock_code": stock_code,
            }
            for key in ("status", "observation_id", "source_id", "error"):
                value = result.get(key)
                if value not in (None, ""):
                    item[key] = value
            items.append(item)
        return items

    def _normalize_written_memories(
        self,
        value: Any,
        *,
        tool_trace: Any = None,
    ) -> List[Dict[str, Any]]:
        source_items = value if isinstance(value, list) else self._extract_written_memories_from_tool_trace(tool_trace)
        normalized_items: List[Dict[str, Any]] = []
        for item in source_items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            stock_code = str(item.get("stock_code") or "").strip() or None
            memo_session = str(item.get("memo_session") or "").strip().lower()
            if memo_session not in {"stock", "general"}:
                memo_session = "stock" if stock_code else "general"
            normalized_item: Dict[str, Any] = {
                "content": content,
                "importance": _normalize_memory_importance(item.get("importance")),
                "memo_session": memo_session,
                "stock_code": stock_code,
            }
            for key in ("status", "observation_id", "source_id", "error"):
                value = item.get(key)
                if value not in (None, ""):
                    normalized_item[key] = str(value)
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
            字段类型稳定、包含三件套、标签和记忆证据链的分析结果。
        """
        normalized = dict(payload or {})
        original_pm = debate_review_context.get("pm_decision") or {}
        normalized["review_triads"] = self._normalize_review_triads(
            normalized.get("review_triads"),
            original_pm=original_pm,
        )
        normalized["experience_tags"] = self._normalize_experience_tags(normalized.get("experience_tags"))
        normalized["recommended_action"] = self._normalize_action(normalized.get("recommended_action"))
        normalized["confidence_score"] = max(0.0, min(100.0, safe_float(normalized.get("confidence_score"), 55.0)))
        normalized["risk_flags"] = self._normalize_string_list(normalized.get("risk_flags"))
        normalized["memory_evidence_used"] = self._normalize_string_list(normalized.get("memory_evidence_used"))
        normalized["similar_success_patterns"] = self._normalize_string_list(normalized.get("similar_success_patterns"))
        normalized["similar_failure_patterns"] = self._normalize_string_list(normalized.get("similar_failure_patterns"))
        normalized["lessons_applied"] = self._normalize_string_list(normalized.get("lessons_applied"))
        normalized["dominant_drivers"] = self._normalize_string_list(normalized.get("dominant_drivers"))
        normalized["rejected_drivers"] = self._normalize_string_list(normalized.get("rejected_drivers"))
        normalized["driver_dimension_review"] = self._normalize_string_list(normalized.get("driver_dimension_review"))
        normalized["buy_sell_rules"] = self._normalize_string_list(normalized.get("buy_sell_rules"))
        normalized["internet_evidence_used"] = self._normalize_string_list(normalized.get("internet_evidence_used"))
        normalized["internet_tools_used"] = self._normalize_string_list(normalized.get("internet_tools_used"))
        normalized["debate_process_issues"] = self._normalize_string_list(normalized.get("debate_process_issues"))
        normalized["optimization_directions"] = self._normalize_string_list(normalized.get("optimization_directions"))
        normalized["improved_debate_rules"] = self._normalize_string_list(normalized.get("improved_debate_rules"))
        normalized["current_case_vs_history"] = str(normalized.get("current_case_vs_history") or "")
        normalized["why_this_is_not_blind_guess"] = str(normalized.get("why_this_is_not_blind_guess") or "")
        normalized["action_plan"] = str(normalized.get("action_plan") or "")
        normalized["entry_plan"] = str(normalized.get("entry_plan") or "")
        normalized["exit_plan"] = str(normalized.get("exit_plan") or "")
        normalized["position_management"] = str(normalized.get("position_management") or "")
        normalized["profit_hypothesis"] = str(normalized.get("profit_hypothesis") or "")
        normalized["market_experience_summary"] = str(normalized.get("market_experience_summary") or "")
        normalized["debate_correctness"] = (
            str(normalized.get("debate_correctness") or "").strip().lower()
            if str(normalized.get("debate_correctness") or "").strip().lower() in CORRECTNESS_BUCKETS
            else "inconclusive"
        )
        normalized["correctness_score"] = max(0.0, min(100.0, safe_float(normalized.get("correctness_score"), 50.0)))
        normalized["correctness_reasoning"] = str(normalized.get("correctness_reasoning") or "")
        normalized["process_improvement_summary"] = str(normalized.get("process_improvement_summary") or "")
        normalized["reviewed_pm_decision"] = self._normalize_action(
            normalized.get("reviewed_pm_decision") or normalized.get("recommended_action")
        )
        normalized["original_pm_decision"] = self._normalize_action(normalized.get("original_pm_decision"))
        revised_target_position = normalized.get("revised_target_position")
        normalized["revised_target_position"] = (
            max(0.0, min(1.0, safe_float(revised_target_position, 0.0)))
            if revised_target_position not in (None, "")
            else None
        )
        original_target_position = normalized.get("original_target_position", original_pm.get("target_position"))
        normalized["original_target_position"] = (
            max(0.0, min(1.0, safe_float(original_target_position, 0.0)))
            if original_target_position not in (None, "")
            else None
        )
        normalized["revised_stop_loss"] = str(normalized.get("revised_stop_loss") or "")
        normalized["tool_invocation_summary"] = normalized.get("tool_invocation_summary") or []
        normalized["written_memories"] = self._normalize_written_memories(
            normalized.get("written_memories"),
            tool_trace=tool_trace or normalized.get("tool_invocation_summary"),
        )
        evidence_chain = {
            "session": debate_review_context.get("session") or {},
            "pm_decision": debate_review_context.get("pm_decision") or {},
            "market_outcome_summary": debate_review_context.get("market_outcome_summary") or {},
            "review_triads": normalized["review_triads"],
        }
        for item in normalized["written_memories"]:
            item["evidence_chain"] = evidence_chain
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
            "recommended_action": self._normalize_action(completed_payload.get("recommended_action")),
            "debate_correctness": (
                str(completed_payload.get("debate_correctness") or "").strip().lower()
                if str(completed_payload.get("debate_correctness") or "").strip().lower() in CORRECTNESS_BUCKETS
                else "inconclusive"
            ),
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
