from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks

from app.ai.market_watch.schemas import DebateParameters, WatchAiDecision
from app.ai.market_watch.service import (
    _maybe_launch_debate,
    _persist_event,
    trading_frequency_to_code,
    trading_strategy_to_code,
)
from app.core import database as database_module
from app.core.logger import get_logger
from app.models.stock_warehouse import StockWarehouse
from app.tasks.stock_analysis_scheduler import DEFAULT_TRADING_FREQUENCY, DEFAULT_TRADING_STRATEGY
from app.trading.discipline_settings import PositionDisciplineSettingsResponse
from app.trading.discipline_settings import get_position_discipline_settings
from app.trading.pm_rules import TRIGGER_STOP_LOSS, TRIGGER_TAKE_PROFIT, evaluate_position_disciplines

logger = get_logger(__name__)

PM_DISCIPLINE_TRIGGER_LABELS = {
    "stop_loss": "PM stop-loss trigger",
    "take_profit": "PM take-profit trigger",
    "horizon_expired": "PM holding horizon expired",
}

PM_DISCIPLINE_SESSION_SOURCES = {
    TRIGGER_STOP_LOSS: "stop_loss",
    TRIGGER_TAKE_PROFIT: "take_profit",
}


def should_skip_position_discipline_scan(settings: PositionDisciplineSettingsResponse, now: datetime) -> str | None:
    """判断当前时间是否应跳过止损止盈扫描。

    Args:
        settings: 当前用户扫描设置。
        now: 上海时区本地无时区时间。

    Returns:
        跳过原因；无需跳过时返回 None。
    """
    from app.ai.market_watch.schemas import parse_market_watch_time

    if now.weekday() >= 5 and not settings.scan_non_trading_days:
        return "non_trading_day"
    current_time = now.time().replace(second=0, microsecond=0)
    if current_time < parse_market_watch_time(settings.scan_start_time):
        return "outside_scan_time_window"
    if current_time > parse_market_watch_time(settings.scan_end_time):
        return "outside_scan_time_window"
    return None


async def scan_position_disciplines(
    user_id: int,
    *,
    settings: PositionDisciplineSettingsResponse | None = None,
    debate_launcher: Any | None = None,
    background_tasks: BackgroundTasks | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """独立扫描用户持仓的 PM 止损/止盈/持有期纪律。

    Args:
        user_id: 当前用户 ID。
        settings: 可选设置注入；为空时读取持久化设置。
        debate_launcher: 测试或调度器注入的辩论启动器。
        background_tasks: API 手动扫描时使用的后台任务。
        now: 可选当前时间，便于测试。

    Returns:
        扫描结果摘要。
    """
    current_settings = settings or get_position_discipline_settings(user_id)
    scan_now = now or datetime.now()
    if not current_settings.enabled:
        return {"scanned_at": scan_now.isoformat(), "status": "skipped", "reason": "disabled", "triggered": []}
    skip_reason = should_skip_position_discipline_scan(current_settings, scan_now)
    if skip_reason:
        return {"scanned_at": scan_now.isoformat(), "status": "skipped", "reason": skip_reason, "triggered": []}

    try:
        with database_module.SessionLocal() as db:
            triggered = evaluate_position_disciplines(db, user_id=user_id)
    except Exception as exc:
        logger.exception("Position discipline evaluation failed", extra={"user_id": user_id})
        await _persist_event(user_id=user_id, event_type="pm_discipline_error", status="failed", error_message=str(exc))
        return {"scanned_at": scan_now.isoformat(), "status": "failed", "error": str(exc), "triggered": []}

    launches: list[dict[str, Any]] = []
    for item in triggered:
        launches.append(
            await _handle_position_discipline_trigger(
                user_id=user_id,
                settings=current_settings,
                item=item,
                debate_launcher=debate_launcher,
                background_tasks=background_tasks,
            )
        )
    return {
        "scanned_at": scan_now.isoformat(),
        "status": "success",
        "triggered": triggered,
        "launched_debate_count": sum(1 for item in launches if item.get("status") == "launched"),
        "debate_launches": launches,
    }


async def _handle_position_discipline_trigger(
    *,
    user_id: int,
    settings: PositionDisciplineSettingsResponse,
    item: dict[str, Any],
    debate_launcher: Any | None,
    background_tasks: BackgroundTasks | None,
) -> dict[str, Any]:
    """处理单个持仓纪律触发事件。

    Args:
        user_id: 当前用户 ID。
        settings: 止损止盈扫描设置。
        item: `evaluate_position_disciplines` 返回的触发项。
        debate_launcher: 测试或调度器注入的辩论启动器。
        background_tasks: API 后台任务。

    Returns:
        辩论启动结果。
    """
    label = PM_DISCIPLINE_TRIGGER_LABELS.get(item["trigger"], item["trigger"])
    latest_price = item["latest_price"] or "unknown"
    pm_session_id = item["pm_session_id"] or "unknown"
    trading_frequency, trading_strategy = _resolve_stock_debate_preferences(
        user_id=user_id,
        stock_code=item["stock_code"],
    )
    decision = WatchAiDecision(
        stock_code=item["stock_code"],
        stock_name=item["stock_code"],
        action="start_debate",
        confidence=1.0,
        urgency="high",
        trigger_reason=f"{label}: threshold {item['threshold']}, latest price {latest_price}",
        evidence_summary=(
            f"PM position discipline triggered deterministically from session {pm_session_id}. "
            "This review debate must decide whether to sell or rebalance."
        ),
        debate_parameters=DebateParameters(
            trading_frequency=trading_frequency_to_code(trading_frequency),
            trading_strategy=trading_strategy_to_code(trading_strategy),
            debate_focus=[label],
            risk_notes=[f"{label} triggered; do not ignore it as advisory-only discipline"],
        ),
    )
    await _persist_event(
        user_id=user_id,
        event_type="pm_discipline_trigger",
        status="success",
        watch_ai_decision=decision.model_dump(mode="json"),
    )
    return await _maybe_launch_debate(
        user_id=user_id,
        settings=settings.model_dump(mode="json"),
        cooldown_minutes=settings.cooldown_minutes,
        auto_launch_debate=settings.auto_launch_debate,
        allowed_stock_codes={item["stock_code"]},
        decision=decision,
        debate_launcher=debate_launcher,
        background_tasks=background_tasks,
        session_source=PM_DISCIPLINE_SESSION_SOURCES.get(item["trigger"], "market_watch"),
    )


def _resolve_stock_debate_preferences(*, user_id: int, stock_code: str) -> tuple[str, str]:
    """读取股票仓库中的复议辩论偏好。

    Args:
        user_id: 当前用户 ID。
        stock_code: 触发纪律的股票代码。

    Returns:
        交易频率和交易策略；仓库缺失时返回股票自动分析默认值。
    """
    with database_module.SessionLocal() as db:
        stock = db.query(StockWarehouse).filter(
            StockWarehouse.user_id == user_id,
            StockWarehouse.stock_code == stock_code,
        ).first()
        if stock is None:
            return DEFAULT_TRADING_FREQUENCY, DEFAULT_TRADING_STRATEGY
        return (
            stock.auto_analysis_trading_frequency or DEFAULT_TRADING_FREQUENCY,
            stock.auto_analysis_trading_strategy or DEFAULT_TRADING_STRATEGY,
        )
