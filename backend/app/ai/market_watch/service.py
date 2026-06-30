from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks

from app.ai.llm_engine.debate_concurrency import (
    DebateConcurrencyLimitReached,
    ensure_debate_concurrency_available,
)
from app.ai.llm_engine.runner import run_analysis_task
from app.crud.session import crud_session
from app.core import database as database_module
from app.core.config import settings as app_settings
from app.core.logger import get_logger
from app.ai.market_watch.ai_gate import WatchAiGate, should_launch_debate
from app.ai.market_watch.audit import is_in_cooldown as audit_is_in_cooldown
from app.ai.market_watch.audit import publish_market_watch_event_payload
from app.ai.market_watch.audit import publish_market_watch_documents_payload
from app.ai.market_watch.schemas import (
    MarketWatchMarkdownDocument,
    MarketWatchSettingsResponse,
    MarketWatchSourceConfig,
    MarketWatchSourceType,
    parse_market_watch_time,
    trading_frequency_label,
    trading_frequency_to_code,
    trading_strategy_label,
    trading_strategy_to_code,
)
from app.ai.market_watch.settings import get_market_watch_settings
from app.ai.market_watch.web_sources import fetch_market_watch_documents
from app.models.account import Account
from app.models.async_task import AsyncTask
from app.models.data_storage import StockBasic
from app.models.market_watch import MarketWatchEvent
from app.models.position import Position
from app.models.session import Session as AnalysisSession
from app.models.stock_warehouse import StockWarehouse
from app.schemas.session import SessionCreate
from app.tasks.task_manager import task_manager


logger = get_logger(__name__)
WATCH_AI_MAX_TOKENS = 16384
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _shanghai_now() -> datetime:
    """Return current Shanghai-local time as a timezone-naive datetime."""
    return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)


def _normalize_shanghai_time(value: datetime) -> datetime:
    """Normalize a caller-supplied datetime into Shanghai-local naive time."""
    if value.tzinfo is None:
        return value
    return value.astimezone(SHANGHAI_TZ).replace(tzinfo=None)


def _scan_skip_reason(settings: MarketWatchSettingsResponse, now: datetime) -> str | None:
    """Return the reason a market watch scan should be skipped, if any."""
    if now.weekday() >= 5 and not settings.scan_non_trading_days:
        return "non_trading_day"

    current_time = now.time().replace(second=0, microsecond=0)
    start_time = parse_market_watch_time(settings.scan_start_time)
    end_time = parse_market_watch_time(settings.scan_end_time)
    if current_time < start_time or current_time > end_time:
        return "outside_scan_time_window"
    if not settings.data_sources or not settings.news_sources:
        return "missing_source_urls"
    return None


class WatchAiGateLike(Protocol):
    """Protocol for the Watch AI decision dependency."""

    async def decide(self, payload: dict[str, Any]) -> Any:
        """Return a Watch AI decision for the scan payload."""


class SourceDocumentFetcher(Protocol):
    """Protocol for configured source Markdown rendering."""

    def __call__(
        self,
        sources: list[str | MarketWatchSourceConfig],
        source_type: MarketWatchSourceType,
    ) -> Awaitable[list[MarketWatchMarkdownDocument]]:
        """Render configured source URLs as Markdown documents."""


class _DefaultLlmClient:
    """Adapter from the existing LLM endpoint helper to WatchAiGate."""

    async def complete_json(self, messages: list[dict[str, str]]) -> Any:
        """Return parsed JSON content from the configured LLM provider."""
        from app.api.endpoints.llm import _request_llm_completion

        response = await _request_llm_completion(
            messages=messages,
            model=app_settings.LLM_MODEL,
            temperature=0.1,
            max_tokens=WATCH_AI_MAX_TOKENS,
            response_format=None,
            role="market_watch",
        )
        content = response.get("content")
        if not isinstance(content, str):
            raise ValueError("LLM response content must be a JSON string")
        return content


class LaunchSchedulingError(RuntimeError):
    """Error raised after a persisted launch record cannot be scheduled."""

    def __init__(self, message: str, *, session_id: str, task_id: str) -> None:
        super().__init__(message)
        self.session_id = session_id
        self.task_id = task_id


async def scan_market_watch(
    user_id: int,
    *,
    watch_ai_gate: WatchAiGateLike | None = None,
    source_document_fetcher: SourceDocumentFetcher | None = None,
    debate_launcher: Any | None = None,
    background_tasks: BackgroundTasks | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """
    Run one market watch scan and optionally launch an AI debate.

    Args:
        user_id: Current authenticated user id.
        watch_ai_gate: Optional Watch AI dependency for tests or custom providers.
        source_document_fetcher: Optional configured source document fetcher for tests.
        debate_launcher: Optional callable used by tests to schedule a debate task.
        background_tasks: FastAPI background task manager for API-triggered scans.
        now: Optional Shanghai-local datetime override for deterministic tests.

    Returns:
        Structured scan result with stock, news, AI decision, and launch status.
    """
    settings = get_market_watch_settings(user_id)
    scan_now = _normalize_shanghai_time(now) if now else _shanghai_now()
    logger.info(
        "Market watch scan started",
        extra={
            "user_id": user_id,
            "scan_now": scan_now.isoformat(timespec="seconds"),
            "auto_scan_enabled": settings.auto_scan_enabled,
            "scan_start_time": settings.scan_start_time,
            "scan_end_time": settings.scan_end_time,
            "scan_non_trading_days": settings.scan_non_trading_days,
        },
    )
    skip_reason = _scan_skip_reason(settings, scan_now)
    if skip_reason:
        logger.info(
            "Market watch scan skipped",
            extra={
                "user_id": user_id,
                "reason": skip_reason,
                "scan_now": scan_now.isoformat(timespec="seconds"),
                "scan_start_time": settings.scan_start_time,
                "scan_end_time": settings.scan_end_time,
                "scan_non_trading_days": settings.scan_non_trading_days,
            },
        )
        return _build_scan_response(
            settings=settings.model_dump(mode="json"),
            items=[],
            news_documents=[],
            data_documents=[],
            ai_evaluated=False,
            ai_decision=None,
            debate_launch={"status": "skipped", "reason": skip_reason},
        )

    scan_context = _load_scan_context(user_id=user_id, settings=settings)

    items = scan_context["items"]
    data_documents, news_documents = await asyncio.gather(
        _load_source_documents(
            settings.data_sources,
            "data",
            source_document_fetcher,
        ),
        _load_source_documents(
            settings.news_sources,
            "news",
            source_document_fetcher,
        ),
    )
    await _publish_source_documents(user_id=user_id, documents=data_documents + news_documents)

    data_document_items = [item.model_dump(mode="json") for item in data_documents]
    news_document_items = [item.model_dump(mode="json") for item in news_documents]
    logger.info(
        "Market watch scan context loaded",
        extra={
            "user_id": user_id,
            "stock_count": len(items),
            "news_count": len(news_document_items),
            "data_document_count": len(data_document_items),
            "position_count": len(scan_context["positions"]),
        },
    )

    await _persist_event(
        user_id=user_id,
        event_type="scan",
        status="success",
    )

    ai_evaluated = False
    ai_decision = None
    debate_launches: list[dict[str, Any]] = []
    debate_launch = {"status": "not_started", "reason": "no_trigger_input"}

    if _should_evaluate_watch_ai(
        has_quote_context=any(item.get("quote") for item in items),
        has_data_documents=_has_markdown_document(data_document_items),
        has_news=_has_markdown_document(news_document_items),
    ):
        logger.info(
            "Market watch Watch AI evaluation started",
            extra={
                "user_id": user_id,
                "news_count": len(news_document_items),
                "data_document_count": len(data_document_items),
            },
        )
        gate = watch_ai_gate or WatchAiGate(_DefaultLlmClient())
        ai_payload = _build_watch_ai_payload(
            user_id=user_id,
            settings=settings.model_dump(mode="json"),
            items=items,
            data_documents=data_document_items,
            news_documents=news_document_items,
            account_summary=scan_context["account_summary"],
            positions=scan_context["positions"],
            recent_debate_launches=_load_recent_debate_launches(
                user_id=user_id,
                now=scan_now,
                lookback_hours=settings.recent_debate_lookback_hours,
            ),
        )
        logger.debug(
            "Market watch Watch AI full input",
            extra={
                "user_id": user_id,
                "ai_input": ai_payload,
            },
        )
        ai_evaluated = True
        try:
            ai_decision = await gate.decide(ai_payload)
        except Exception as exc:
            logger.exception(
                "Market watch Watch AI evaluation failed",
                extra={
                    "user_id": user_id,
                    "news_count": len(news_document_items),
                },
            )
            debate_launch = {
                "status": "failed",
                "reason": "watch_ai_failed",
                "error": str(exc),
            }
            await _persist_event(
                user_id=user_id,
                event_type="error",
                status="failed",
                error_message=str(exc),
            )
            return _build_scan_response(
                settings=settings.model_dump(mode="json"),
                items=items,
                news_documents=news_document_items,
                data_documents=data_document_items,
                ai_evaluated=ai_evaluated,
                ai_decision=ai_decision,
                debate_launch=debate_launch,
            )

        ai_decision = _filter_allowed_watch_ai_decisions(
            ai_decision,
            allowed_stock_codes=scan_context["allowed_stock_codes"],
            user_id=user_id,
        )
        _apply_stock_debate_preferences(ai_decision, scan_context["items"])
        logger.info(
            "Market watch Watch AI decision received",
            extra={
                "user_id": user_id,
                "decision_count": len(ai_decision),
                "start_debate_count": sum(1 for decision in ai_decision if decision.action == "start_debate"),
            },
        )
        await _persist_event(
            user_id=user_id,
            event_type="ai_decision",
            status="success",
            watch_ai_decision=[decision.model_dump(mode="json") for decision in ai_decision],
        )
        for decision in ai_decision:
            debate_launches.append(
                await _maybe_launch_debate(
                    user_id=user_id,
                    settings=settings.model_dump(mode="json"),
                    cooldown_minutes=settings.cooldown_minutes,
                    auto_launch_debate=settings.auto_launch_debate,
                    allowed_stock_codes=scan_context["allowed_stock_codes"],
                    decision=decision,
                    debate_launcher=debate_launcher,
                    background_tasks=background_tasks,
                )
            )
        debate_launch = _primary_debate_launch(debate_launches)
    else:
        logger.info(
            "Market watch Watch AI evaluation skipped",
            extra={
                "user_id": user_id,
                "reason": "no_trigger_input",
                "news_count": len(news_document_items),
                "data_document_count": len(data_document_items),
            },
        )

    response = _build_scan_response(
        settings=settings.model_dump(mode="json"),
        items=items,
        news_documents=news_document_items,
        data_documents=data_document_items,
        ai_evaluated=ai_evaluated,
        ai_decision=ai_decision,
        debate_launch=debate_launch,
        debate_launches=debate_launches,
    )
    logger.info(
        "Market watch scan completed",
        extra={
            "user_id": user_id,
            "stock_count": response["stock_count"],
            "news_count": response["news_count"],
            "ai_evaluated": response["ai_evaluated"],
            "debate_status": response["debate_launch"].get("status"),
            "debate_reason": response["debate_launch"].get("reason"),
        },
    )
    return response


def _build_scan_response(
    *,
    settings: dict[str, Any],
    items: list[dict[str, Any]],
    news_documents: list[dict[str, Any]],
    data_documents: list[dict[str, Any]],
    ai_evaluated: bool,
    ai_decision: Any | None,
    debate_launch: dict[str, Any],
    debate_launches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_launches = debate_launches or []
    return {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings,
        "stock_count": len(items),
        "data_document_count": len(data_documents),
        "news_count": len(news_documents),
        "ai_evaluated": ai_evaluated,
        "launched_debate_count": sum(1 for launch in normalized_launches if launch["status"] == "launched"),
        "debate_launch": debate_launch,
        "debate_launches": normalized_launches,
        "watch_ai_decision": [decision.model_dump(mode="json") for decision in ai_decision] if ai_decision else None,
        "data_documents": data_documents,
        "news_documents": news_documents,
        "items": items,
    }


def _primary_debate_launch(launches: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the compatibility single launch result for a multi-stock scan."""
    for status in ("launched", "failed", "skipped"):
        for launch in launches:
            if launch.get("status") == status:
                return launch
    if launches:
        return launches[0]
    return {"status": "not_started", "reason": "watch_ai_decision"}


def _should_evaluate_watch_ai(
    *,
    has_quote_context: bool,
    has_data_documents: bool,
    has_news: bool,
) -> bool:
    return has_quote_context or has_data_documents or has_news


def _load_scan_context(user_id: int, settings: Any) -> dict[str, Any]:
    with database_module.SessionLocal() as db:
        stocks = (
            db.query(StockWarehouse)
            .filter(StockWarehouse.user_id == user_id, StockWarehouse.is_active.is_(True))
            .order_by(StockWarehouse.added_at.asc(), StockWarehouse.id.asc())
            .all()
        )
        stock_codes = [stock.stock_code for stock in stocks]
        basics = {}
        if stock_codes:
            basic_rows = db.query(StockBasic).filter(StockBasic.stock_code.in_(stock_codes)).all()
            basics = {row.stock_code: row for row in basic_rows}

        account = db.query(Account).filter(Account.user_id == user_id).first()
        positions = {}
        if account is not None:
            rows = db.query(Position).filter(Position.account_id == account.account_id).all()
            positions = {row.stock_code: row for row in rows}

        items = [
            _build_stock_item(
                stock=stock,
                basic=basics.get(stock.stock_code),
                position=positions.get(stock.stock_code),
                account=account,
            )
            for stock in stocks
        ]
        return {
            "items": items,
            "allowed_stock_codes": set(stock_codes) | set(positions),
            "account_summary": _account_summary(user_id, account),
            "positions": [_position_context(stock_code, position) for stock_code, position in positions.items()],
        }


async def _load_source_documents(
    sources: list[str | MarketWatchSourceConfig],
    source_type: MarketWatchSourceType,
    source_document_fetcher: SourceDocumentFetcher | None,
) -> list[MarketWatchMarkdownDocument]:
    if not sources:
        return []

    fetcher = source_document_fetcher or fetch_market_watch_documents
    try:
        return await fetcher(
            sources,
            source_type,
        )
    except Exception as exc:
        logger.exception(
            "Market watch configured source fetch failed",
            extra={
                "source_type": source_type,
                "source_count": len(sources),
                "error": str(exc),
            },
        )
        captured_at = datetime.now(timezone.utc)
        return [
            MarketWatchMarkdownDocument(
                id=f"{source_type}:{index}:failed",
                source_type=source_type,
                url=url,
                final_url=url,
                title=None,
                markdown="",
                status=None,
                error=f"{type(exc).__name__}: {exc}",
                captured_at=captured_at,
            )
            for index, source in enumerate(sources)
            for url in [getattr(source, "url", source)]
        ]


def _has_markdown_document(documents: list[dict[str, Any]]) -> bool:
    return any(str(document.get("markdown") or "").strip() for document in documents)


async def _publish_source_documents(
    *,
    user_id: int,
    documents: list[MarketWatchMarkdownDocument],
) -> None:
    if not documents:
        return
    payload = {
        "user_id": user_id,
        "documents": [document.model_dump(mode="json") for document in documents],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await publish_market_watch_documents_payload(payload)
    except Exception as exc:
        logger.exception(
            "Market watch source document publish failed",
            extra={
                "user_id": user_id,
                "document_count": len(documents),
                "error": str(exc),
            },
        )


def _build_stock_item(
    *,
    stock: StockWarehouse,
    basic: StockBasic | None,
    position: Position | None,
    account: Account | None,
) -> dict[str, Any]:
    return {
        "stock_code": stock.stock_code,
        "stock_name": basic.name if basic else None,
        "industry": basic.industry if basic else None,
        "market": basic.market if basic else None,
        "trading_frequency_code": trading_frequency_to_code(stock.auto_analysis_trading_frequency),
        "trading_strategy_code": trading_strategy_to_code(stock.auto_analysis_trading_strategy),
        "quote_status": "missing",
        "quote": None,
        "has_position": position is not None,
        "position_ratio": _position_ratio(account, position),
        "position": _position_to_payload(position),
    }


def _position_to_payload(position: Position | None) -> dict[str, Any] | None:
    if position is None:
        return None
    return {
        "total_shares": position.total_shares,
        "current_price": _decimal_to_float(position.current_price),
        "market_value": _decimal_to_float(position.market_value),
        "profit_loss": _decimal_to_float(position.profit_loss),
        "profit_loss_pct": _decimal_to_float(position.profit_loss_pct),
    }


def _position_ratio(account: Account | None, position: Position | None) -> float | None:
    if account is None or position is None:
        return None
    total_assets = _decimal_to_float(account.total_assets)
    market_value = _decimal_to_float(position.market_value)
    if not total_assets or market_value is None:
        return None
    return round(market_value / total_assets, 6)


def _decimal_to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _build_watch_ai_payload(
    *,
    user_id: int,
    settings: dict[str, Any],
    items: list[dict[str, Any]],
    data_documents: list[dict[str, Any]],
    news_documents: list[dict[str, Any]],
    account_summary: dict[str, Any],
    positions: list[dict[str, Any]],
    recent_debate_launches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """构建盯盘 AI 的输入上下文。

    Args:
        user_id: 当前用户 ID。
        settings: 当前盯盘设置。
        items: 仓库股票上下文。
        data_documents: 数据源 Markdown 文档。
        news_documents: 新闻源 Markdown 文档。
        account_summary: 当前账户摘要。
        positions: 当前持仓上下文。
        recent_debate_launches: 近期已启动的辩论记录，供盯盘 AI 自行判重。

    Returns:
        可直接传给 Watch AI 的结构化上下文。
    """
    return {
        "user_id": user_id,
        "settings": settings,
        "warehouse_stocks": _sort_watch_ai_stock_items(items),
        "data_documents": data_documents,
        "news_documents": news_documents,
        "account_summary": account_summary,
        "positions": _sort_watch_ai_stock_items(positions),
        "recent_debate_launches": recent_debate_launches or [],
    }


def _load_recent_debate_launches(*, user_id: int, now: datetime, lookback_hours: int) -> list[dict[str, Any]]:
    """读取近期成功启动过的辩论记录，作为盯盘 AI 判重输入。

    Args:
        user_id: 当前用户 ID。
        now: 本轮扫描时间，用于计算近期窗口。
        lookback_hours: 往前查询已启动辩论记录的小时数。

    Returns:
        按时间倒序排列的近期启动记录，包含股票、触发原因、证据摘要和会话 ID。
    """
    cutoff = now - timedelta(hours=lookback_hours)
    with database_module.SessionLocal() as db:
        events = (
            db.query(MarketWatchEvent)
            .filter(
                MarketWatchEvent.user_id == user_id,
                MarketWatchEvent.event_type == "debate_launched",
                MarketWatchEvent.status == "success",
                MarketWatchEvent.created_at >= cutoff,
            )
            .order_by(MarketWatchEvent.created_at.desc())
            .all()
        )
        failed_session_ids = {
            str(session_id)
            for session_id, in db.query(AnalysisSession.session_id).filter(
                AnalysisSession.user_id == user_id,
                AnalysisSession.status == "failed",
                AnalysisSession.created_at >= cutoff,
            )
        }

    launches: list[dict[str, Any]] = []
    for event in events:
        if event.debate_session_id in failed_session_ids:
            continue
        decision = event.watch_ai_decision if isinstance(event.watch_ai_decision, dict) else {}
        if not decision:
            continue
        launches.append(
            {
                "stock_code": str(decision.get("stock_code") or ""),
                "stock_name": str(decision.get("stock_name") or ""),
                "trigger_reason": str(decision.get("trigger_reason") or ""),
                "evidence_summary": str(decision.get("evidence_summary") or ""),
                "debate_session_id": event.debate_session_id,
                "created_at": event.created_at.isoformat() if event.created_at else None,
            }
        )
    return launches


def _sort_watch_ai_stock_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            _stock_code_sort_key(item.get("stock_code")),
            str(item.get("stock_name") or "").strip(),
        ),
    )


def _stock_code_sort_key(value: Any) -> str:
    return str(value or "").strip().upper()


def _account_summary(user_id: int, account: Account | None) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "account_id": str(account.account_id) if account else None,
        "total_assets": _decimal_to_float(account.total_assets) if account else None,
        "market_value": _decimal_to_float(account.market_value) if account else None,
    }


def _position_context(stock_code: str, position: Position) -> dict[str, Any]:
    payload = _position_to_payload(position) or {}
    payload["stock_code"] = stock_code
    return payload


async def _maybe_launch_debate(
    *,
    user_id: int,
    settings: dict[str, Any],
    cooldown_minutes: int,
    auto_launch_debate: bool,
    allowed_stock_codes: set[str],
    decision: Any,
    debate_launcher: Any | None,
    background_tasks: BackgroundTasks | None,
    session_source: str = "market_watch",
) -> dict[str, Any]:
    if not should_launch_debate(decision):
        logger.info(
            "Market watch debate launch not started",
            extra={
                "user_id": user_id,
                "reason": "watch_ai_decision",
                "action": decision.action,
                "confidence": decision.confidence,
            },
        )
        return {"status": "not_started", "reason": "watch_ai_decision", "stock_code": decision.stock_code}

    stock_code = decision.stock_code
    logger.info(
        "Market watch debate launch evaluation started",
        extra={
            "user_id": user_id,
            "stock_code": stock_code,
            "confidence": decision.confidence,
            "auto_launch_debate": auto_launch_debate,
            "cooldown_minutes": cooldown_minutes,
        },
    )
    if stock_code not in allowed_stock_codes:
        logger.info(
            "Market watch debate launch skipped",
            extra={
                "user_id": user_id,
                "stock_code": stock_code,
                "reason": "invalid_target_stock",
            },
        )
        await _audit_debate_skip(
            user_id=user_id,
            decision=decision,
            reason="invalid_target_stock",
        )
        return {"status": "skipped", "reason": "invalid_target_stock", "stock_code": stock_code}

    if _find_existing_stock_task(stock_code):
        logger.info(
            "Market watch debate launch skipped",
            extra={
                "user_id": user_id,
                "stock_code": stock_code,
                "reason": "existing_task",
            },
        )
        await _audit_debate_skip(
            user_id=user_id,
            decision=decision,
            reason="existing_task",
        )
        return {"status": "skipped", "reason": "existing_task", "stock_code": stock_code}

    if not auto_launch_debate:
        logger.info(
            "Market watch debate launch skipped",
            extra={
                "user_id": user_id,
                "stock_code": stock_code,
                "reason": "auto_launch_disabled",
            },
        )
        await _audit_debate_skip(
            user_id=user_id,
            decision=decision,
            reason="auto_launch_disabled",
        )
        return {"status": "skipped", "reason": "auto_launch_disabled", "stock_code": stock_code}

    cooldown_broken = False
    cooldown_active = _is_in_cooldown(user_id, stock_code, cooldown_minutes)
    if cooldown_active:
        cooldown_broken = _can_break_cooldown(decision, settings)
        if not cooldown_broken:
            logger.info(
                "Market watch debate launch skipped",
                extra={
                    "user_id": user_id,
                    "stock_code": stock_code,
                    "reason": "cooldown",
                    "confidence": decision.confidence,
                },
            )
            await _audit_debate_skip(
                user_id=user_id,
                decision=decision,
                reason="cooldown",
            )
            return {"status": "skipped", "reason": "cooldown", "stock_code": stock_code}

    try:
        launch = await _create_and_schedule_debate(
            user_id=user_id,
            decision=decision,
            debate_launcher=debate_launcher,
            background_tasks=background_tasks,
            session_source=session_source,
        )
    except DebateConcurrencyLimitReached as exc:
        logger.info(
            "Market watch debate launch skipped",
            extra={
                "user_id": user_id,
                "stock_code": stock_code,
                "reason": "concurrency_limit",
                "running_count": exc.running_count,
                "max_concurrent": exc.max_concurrent,
            },
        )
        await _audit_debate_skip(
            user_id=user_id,
            decision=decision,
            reason="concurrency_limit",
        )
        return {
            "status": "skipped",
            "reason": "concurrency_limit",
            "stock_code": stock_code,
            "error": str(exc),
        }
    except LaunchSchedulingError as exc:
        logger.exception(
            "Market watch debate launch scheduling failed",
            extra={
                "user_id": user_id,
                "stock_code": stock_code,
                "session_id": exc.session_id,
                "task_id": exc.task_id,
            },
        )
        await _persist_event(
            user_id=user_id,
            event_type="error",
            status="failed",
            watch_ai_decision=decision.model_dump(mode="json"),
            debate_session_id=exc.session_id,
            task_id=exc.task_id,
            error_message=str(exc),
        )
        return {"status": "failed", "reason": "launch_failed", "stock_code": stock_code, "error": str(exc)}
    except Exception as exc:
        logger.exception(
            "Market watch debate launch failed",
            extra={
                "user_id": user_id,
                "stock_code": stock_code,
            },
        )
        await _persist_event(
            user_id=user_id,
            event_type="error",
            status="failed",
            watch_ai_decision=decision.model_dump(mode="json"),
            error_message=str(exc),
        )
        return {"status": "failed", "reason": "launch_failed", "stock_code": stock_code, "error": str(exc)}

    await _persist_event(
        user_id=user_id,
        event_type="debate_launched",
        status="success",
        watch_ai_decision=decision.model_dump(mode="json"),
        debate_parameters=decision.debate_parameters.model_dump(mode="json"),
        debate_session_id=launch["session_id"],
        task_id=launch["task_id"],
    )
    logger.info(
        "Market watch debate launched",
        extra={
            "user_id": user_id,
            "stock_code": stock_code,
            "session_id": launch["session_id"],
            "task_id": launch["task_id"],
            "cooldown_broken": cooldown_broken,
        },
    )
    return {"status": "launched", "stock_code": stock_code, "cooldown_broken": cooldown_broken, **launch}


def _is_in_cooldown(user_id: int, stock_code: str, cooldown_minutes: int) -> bool:
    return audit_is_in_cooldown(user_id=user_id, stock_code=stock_code, cooldown_minutes=cooldown_minutes)


def _can_break_cooldown(decision: Any, settings: dict[str, Any]) -> bool:
    confidence_threshold = float(settings.get("cooldown_break_confidence") or 1.0)
    return decision.confidence >= confidence_threshold


def _find_existing_stock_task(stock_code: str) -> bool:
    with database_module.SessionLocal() as db:
        tasks = (
            db.query(AsyncTask)
            .filter(AsyncTask.task_type == "ai_analysis", AsyncTask.status.in_(["pending", "running"]))
            .all()
        )
        for task in tasks:
            parameters = task.parameters or {}
            if task.task_name == f"AI Analysis - {stock_code}" or parameters.get("stock_code") == stock_code:
                return True
    return False


def _apply_stock_debate_preferences(
    decision: Any,
    stock_items: list[dict[str, Any]],
) -> None:
    """将盯盘 AI 决策中的辩论偏好对齐到股票自动分析配置。

    Args:
        decision: 盯盘 AI 返回的决策列表。
        stock_items: 本轮传给盯盘 AI 的股票上下文，包含自动分析偏好。
    """
    stock_preferences = {str(item.get("stock_code")): item for item in stock_items}
    for item in decision:
        if item.debate_parameters is None:
            continue
        preferences = stock_preferences.get(item.stock_code, {})
        item.debate_parameters.trading_frequency = preferences.get("trading_frequency_code") or "position"
        item.debate_parameters.trading_strategy = preferences.get("trading_strategy_code") or "value"


def _filter_allowed_watch_ai_decisions(
    decisions: list[Any],
    *,
    allowed_stock_codes: set[str],
    user_id: int,
) -> list[Any]:
    """过滤盯盘 AI 返回的仓库外股票，避免外部网页噪声进入展示和审计。

    Args:
        decisions: 盯盘 AI 返回的结构化决策列表。
        allowed_stock_codes: 本轮允许处理的股票代码集合，来自股票仓库和持仓。
        user_id: 当前用户 ID，用于结构化日志。

    Returns:
        仅包含允许股票代码的决策列表。
    """
    filtered_decisions = []
    for decision in decisions:
        if decision.stock_code in allowed_stock_codes:
            filtered_decisions.append(decision)
            continue
        logger.info(
            "Market watch Watch AI decision filtered",
            extra={
                "user_id": user_id,
                "stock_code": decision.stock_code,
                "reason": "invalid_target_stock",
            },
        )
    return filtered_decisions


async def _audit_debate_skip(
    *,
    user_id: int,
    decision: Any,
    reason: str,
) -> None:
    await _persist_event(
        user_id=user_id,
        event_type="debate_skipped",
        status="skipped",
        reason=reason,
        watch_ai_decision=decision.model_dump(mode="json"),
        debate_parameters=decision.debate_parameters.model_dump(mode="json") if decision.debate_parameters else None,
    )


async def _create_and_schedule_debate(
    *,
    user_id: int,
    decision: Any,
    debate_launcher: Any | None,
    background_tasks: BackgroundTasks | None,
    session_source: str,
) -> dict[str, str]:
    parameters = decision.debate_parameters
    stock_code = decision.stock_code
    trading_frequency = trading_frequency_label(parameters.trading_frequency)
    trading_strategy = trading_strategy_label(parameters.trading_strategy)
    with database_module.SessionLocal() as db:
        ensure_debate_concurrency_available(db)
        session = crud_session.create(
            db,
            obj_in=SessionCreate(
                user_id=user_id,
                stock_code=stock_code,
                trading_frequency=trading_frequency,
                trading_strategy=trading_strategy,
                source=session_source,
            ),
        )
        session_id = str(session.session_id)
        task_parameters = {
            "session_id": session_id,
            "stock_code": stock_code,
            "trading_frequency": trading_frequency,
            "trading_strategy": trading_strategy,
        }
        task_info = task_manager.submit_task(
            db=db,
            task_name=f"AI Analysis - {stock_code}",
            task_type="ai_analysis",
            parameters=task_parameters,
            allow_concurrent=False,
            user_id=user_id,
        )
        task_id = task_info["task_id"]

    launch_kwargs = {
        "task_id": task_id,
        "session_id": session_id,
        "stock_code": stock_code,
        "trading_frequency": trading_frequency,
        "trading_strategy": trading_strategy,
        "trigger_reason": decision.trigger_reason,
        "evidence_summary": decision.evidence_summary,
    }
    try:
        await _schedule_debate_task(
            launch_kwargs=launch_kwargs,
            debate_launcher=debate_launcher,
            background_tasks=background_tasks,
        )
    except LaunchSchedulingError as exc:
        _mark_launch_records_failed(session_id=session_id, task_id=task_id, error_message=str(exc))
        raise
    return {"session_id": session_id, "task_id": task_id}


def _mark_launch_records_failed(*, session_id: str, task_id: str, error_message: str) -> None:
    with database_module.SessionLocal() as db:
        task = db.query(AsyncTask).filter(AsyncTask.task_id == task_id).first()
        if task is not None:
            task.status = "failed"
            task.error_message = error_message
            task.completed_at = datetime.now(timezone.utc)

        session = db.query(AnalysisSession).filter(AnalysisSession.session_id == UUID(session_id)).first()
        if session is not None:
            session.status = "failed"
        db.commit()


async def _schedule_debate_task(
    *,
    launch_kwargs: dict[str, Any],
    debate_launcher: Any | None,
    background_tasks: BackgroundTasks | None,
) -> None:
    if debate_launcher is not None:
        try:
            result = debate_launcher(**launch_kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            raise LaunchSchedulingError(
                str(exc),
                session_id=launch_kwargs["session_id"],
                task_id=launch_kwargs["task_id"],
            ) from exc
        return
    try:
        if background_tasks is not None:
            background_tasks.add_task(run_analysis_task, **launch_kwargs)
            return
        raise RuntimeError("market watch debate scheduler is unavailable")
    except Exception as exc:
        raise LaunchSchedulingError(
            str(exc),
            session_id=launch_kwargs["session_id"],
            task_id=launch_kwargs["task_id"],
        ) from exc


async def _persist_event(
    *,
    user_id: int,
    event_type: str,
    status: str,
    reason: str | None = None,
    watch_ai_decision: dict[str, Any] | list[dict[str, Any]] | None = None,
    debate_parameters: dict[str, Any] | None = None,
    debate_session_id: str | None = None,
    task_id: str | None = None,
    error_message: str | None = None,
) -> None:
    with database_module.SessionLocal() as db:
        event = MarketWatchEvent(
            user_id=user_id,
            event_type=event_type,
            status=status,
            reason=reason,
            watch_ai_decision=watch_ai_decision,
            debate_parameters=debate_parameters,
            debate_session_id=debate_session_id,
            task_id=task_id,
            error_message=error_message,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        payload = {
            "event_id": event.event_id,
            "user_id": event.user_id,
            "event_type": event.event_type,
            "status": event.status,
            "reason": event.reason,
            "watch_ai_decision": event.watch_ai_decision,
            "debate_parameters": event.debate_parameters,
            "debate_session_id": event.debate_session_id,
            "task_id": event.task_id,
            "error_message": event.error_message,
            "created_at": event.created_at.isoformat() if event.created_at else None,
        }
    try:
        await publish_market_watch_event_payload(payload)
    except Exception:
        logger.exception(
            "Failed to publish market watch event payload",
            extra={
                "user_id": user_id,
                "event_type": event_type,
                "status": status,
            },
        )
