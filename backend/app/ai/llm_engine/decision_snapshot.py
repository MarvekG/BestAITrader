"""Debate 工作流内存决策快照。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import desc, select, text

from app.core import database as database_module
from app.models.account import Account
from app.models.data_storage import KlineData, StockBasic, StockRealtimeMarket
from app.models.order import Order
from app.models.position import Position
from app.models.session import Session as DebateSession
from app.portfolio.valuation import UNKNOWN_INDUSTRY
from app.trading.trading_engine import TradingEngine


_TRADING_ENGINE = TradingEngine()


class DecisionSnapshotError(ValueError):
    """内存决策快照构建失败。"""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """目标股票在快照时刻的持仓数据。"""

    total_shares: int
    available_shares: int
    frozen_shares: int
    avg_cost: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    unrealized_pnl_pct: Decimal
    weight: Decimal
    stock_code: str = ""
    stock_name: str = "Unknown"
    industry: str = "Unknown"
    current_price: Decimal = Decimal("0")
    stop_loss: Decimal | None = None
    has_stop_loss: bool = False
    position_id: UUID | None = None
    session_id: UUID | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RealtimeMarketSnapshot:
    """快照时刻的目标标的实时行情字段。"""

    price: Decimal
    timestamp: datetime | None
    pct_chg: Decimal | None
    turnover_rate: Decimal | None
    volume_ratio: Decimal | None
    amplitude: Decimal | None
    pb: Decimal | None
    pe: Decimal | None
    amount: Decimal | None
    volume: Decimal | None
    turnover: Decimal | None
    total_market_cap: Decimal | None
    circulating_market_cap: Decimal | None


@dataclass(frozen=True, slots=True)
class PendingOrderSnapshot:
    """快照时刻待成交订单的 PM 上下文数据。"""

    order_id: UUID
    session_id: UUID | None
    stock_code: str | None
    action: str | None
    order_type: str | None
    status: str | None
    price: Decimal | None
    shares: int
    filled_shares: int
    created_at: datetime | None
    source: str | None


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """一次 Debate 工作流内不可变的决策输入。"""

    snapshot_id: UUID
    session_id: UUID
    user_id: int
    stock_code: str
    generated_at: datetime
    reference_price: Decimal
    price_source: str
    price_as_of: datetime | date | None
    account_total_assets: Decimal
    available_cash: Decimal
    portfolio_market_value: Decimal
    position: PositionSnapshot
    pending_buy_shares: int
    pending_sell_shares: int
    frozen_cash: Decimal = Decimal("0")
    portfolio_positions: tuple[PositionSnapshot, ...] = ()
    realtime_market: RealtimeMarketSnapshot | None = None
    pending_orders: tuple[PendingOrderSnapshot, ...] = ()


async def build_decision_snapshot(*, session_id: UUID) -> DecisionSnapshot:
    """在一次一致性读取中构建本轮 Debate 快照。

    Args:
        session_id: 当前 Debate 会话 ID。

    Returns:
        当前工作流使用的不可变内存快照。

    Raises:
        DecisionSnapshotError: 会话、账户、行情或动态资产无效时抛出。
    """
    normalized_session_id = _coerce_uuid(session_id)
    async with database_module.AsyncSessionLocal() as db:
        async with db.begin():
            await _configure_read_transaction(db)
            return await _build_snapshot_from_database(db, normalized_session_id)


async def _build_snapshot_from_database(db: Any, session_id: UUID) -> DecisionSnapshot:
    session = (
        await db.execute(select(DebateSession).where(DebateSession.session_id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise DecisionSnapshotError("snapshot_session_not_found", "The debate session was not found")
    if session.user_id is None:
        raise DecisionSnapshotError("snapshot_user_not_found", "The debate session user is missing")
    if not session.stock_code:
        raise DecisionSnapshotError("snapshot_stock_code_missing", "The debate session stock code is missing")

    account = (
        await db.execute(select(Account).where(Account.user_id == session.user_id))
    ).scalar_one_or_none()
    if account is None:
        raise DecisionSnapshotError("snapshot_account_not_found", "The associated account was not found")

    reference_price, price_source, price_as_of, realtime_market = await _load_reference_price(
        db,
        session.stock_code,
    )
    if reference_price is None:
        raise DecisionSnapshotError("snapshot_price_unavailable", "A valid reference price was not found")

    position_rows = (
        await db.execute(
            select(Position, StockBasic.name, StockBasic.industry)
            .outerjoin(StockBasic, Position.stock_code == StockBasic.stock_code)
            .where(Position.account_id == account.account_id, Position.total_shares > 0)
        )
    ).all()
    unweighted_positions: list[PositionSnapshot] = []
    for position, stock_name, industry in position_rows:
        if position.stock_code == session.stock_code:
            position_price = reference_price
        else:
            position_price = await _load_other_position_price(db, position)
        unweighted_positions.append(
            _build_position_snapshot(
                position,
                position_price,
                stock_name=stock_name,
                industry=industry,
            )
        )

    portfolio_market_value = sum(
        (position.market_value for position in unweighted_positions),
        Decimal("0"),
    )

    available_cash = _decimal(account.available_cash)
    frozen_cash = _decimal(account.frozen_cash)
    account_total_assets = available_cash + frozen_cash + portfolio_market_value
    if account_total_assets <= 0:
        raise DecisionSnapshotError("snapshot_assets_invalid", "Dynamic account assets must be positive")

    portfolio_positions = tuple(
        replace(
            position,
            weight=position.market_value / account_total_assets,
        )
        for position in unweighted_positions
    )
    position_snapshot = next(
        (
            position
            for position in portfolio_positions
            if position.stock_code == session.stock_code
        ),
        _empty_position_snapshot(session.stock_code),
    )
    pending_buy_shares, pending_sell_shares = await _load_pending_order_shares(
        db,
        account.account_id,
        session.stock_code,
    )
    pending_orders = await _load_pending_orders_for_pm(db, account.account_id)

    return DecisionSnapshot(
        snapshot_id=uuid4(),
        session_id=session_id,
        user_id=int(session.user_id),
        stock_code=session.stock_code,
        generated_at=datetime.now(timezone.utc),
        reference_price=reference_price,
        price_source=price_source,
        price_as_of=price_as_of,
        account_total_assets=account_total_assets,
        available_cash=available_cash,
        portfolio_market_value=portfolio_market_value,
        position=position_snapshot,
        pending_buy_shares=pending_buy_shares,
        pending_sell_shares=pending_sell_shares,
        frozen_cash=frozen_cash,
        portfolio_positions=portfolio_positions,
        realtime_market=realtime_market,
        pending_orders=pending_orders,
    )


def _build_position_snapshot(
    position: Position | None,
    current_price: Decimal,
    *,
    stock_name: str | None = None,
    industry: str | None = None,
) -> PositionSnapshot:
    if position is None:
        return _empty_position_snapshot()

    total_shares = max(int(position.total_shares or 0), 0)
    share_fields = _TRADING_ENGINE.derive_share_fields(
        total_shares,
        position.purchase_details,
        position.available_shares,
    )
    avg_cost = _decimal(position.avg_cost)
    market_value = current_price * Decimal(total_shares)
    cost_value = avg_cost * Decimal(total_shares)
    unrealized_pnl = market_value - cost_value
    unrealized_pnl_pct = unrealized_pnl / cost_value if cost_value > 0 else Decimal("0")
    stop_loss = _legacy_stop_loss(position.purchase_details)
    return PositionSnapshot(
        total_shares=total_shares,
        available_shares=share_fields["available_shares"],
        frozen_shares=share_fields["frozen_shares"],
        avg_cost=avg_cost,
        market_value=market_value,
        unrealized_pnl=unrealized_pnl,
        unrealized_pnl_pct=unrealized_pnl_pct,
        weight=Decimal("0"),
        stock_code=position.stock_code or "",
        stock_name=stock_name or "Unknown",
        industry=industry or UNKNOWN_INDUSTRY,
        current_price=current_price,
        stop_loss=stop_loss,
        has_stop_loss=stop_loss is not None,
        position_id=position.position_id,
        session_id=position.session_id,
        updated_at=position.updated_at,
    )


def _empty_position_snapshot(stock_code: str = "") -> PositionSnapshot:
    return PositionSnapshot(
        0,
        0,
        0,
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        stock_code=stock_code,
    )


async def _load_pending_order_shares(db: Any, account_id: UUID, stock_code: str) -> tuple[int, int]:
    orders = (
        await db.execute(
            select(Order).where(
                Order.account_id == account_id,
                Order.stock_code == stock_code,
                Order.order_type == "limit",
                Order.status == "pending",
                Order.action.in_(("buy", "sell")),
            )
        )
    ).scalars().all()
    pending_buy_shares = sum(_remaining_shares(order) for order in orders if order.action == "buy")
    pending_sell_shares = sum(_remaining_shares(order) for order in orders if order.action == "sell")
    return pending_buy_shares, pending_sell_shares


async def _load_pending_orders_for_pm(
    db: Any,
    account_id: UUID,
    *,
    limit: int = 20,
) -> tuple[PendingOrderSnapshot, ...]:
    """读取快照时刻供 PM 参考的待成交订单。"""
    orders = (
        await db.execute(
            select(Order)
            .where(
                Order.account_id == account_id,
                Order.status == "pending",
            )
            .order_by(Order.created_at.desc(), Order.order_id.desc())
            .limit(limit)
        )
    ).scalars().all()
    return tuple(
        PendingOrderSnapshot(
            order_id=order.order_id,
            session_id=order.session_id,
            stock_code=order.stock_code,
            action=order.action,
            order_type=order.order_type,
            status=order.status,
            price=_decimal_or_none(order.price),
            shares=int(order.shares or 0),
            filled_shares=int(order.filled_shares or 0),
            created_at=order.created_at,
            source=order.source,
        )
        for order in orders
    )


async def _load_reference_price(
    db: Any,
    stock_code: str,
) -> tuple[Decimal | None, str, datetime | date | None, RealtimeMarketSnapshot | None]:
    latest_market = (
        await db.execute(
            select(StockRealtimeMarket)
            .where(
                StockRealtimeMarket.stock_code == stock_code,
                StockRealtimeMarket.current_price > 0,
            )
            .order_by(
                desc(StockRealtimeMarket.timestamp),
                desc(StockRealtimeMarket.updated_at),
                desc(StockRealtimeMarket.created_at),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_market is not None:
        price = _positive_decimal(latest_market.current_price)
        if price is not None:
            return (
                price,
                "realtime",
                latest_market.timestamp,
                _build_realtime_market_snapshot(latest_market, price),
            )

    latest_kline = (
        await db.execute(
            select(KlineData)
            .where(KlineData.stock_code == stock_code, KlineData.freq == "D")
            .order_by(desc(KlineData.date))
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_kline is not None:
        price = _positive_decimal(latest_kline.close)
        if price is not None and latest_kline.date is not None:
            return price, "daily_close", latest_kline.date, None
    return None, "", None, None


async def _load_other_position_price(db: Any, position: Position) -> Decimal:
    price, _, _, _ = await _load_reference_price(db, position.stock_code)
    if price is not None:
        return price
    shares = Decimal(int(position.total_shares or 0))
    market_value = _decimal(position.market_value)
    if shares > 0 and market_value > 0:
        return market_value / shares
    return Decimal("0")


async def _configure_read_transaction(db: Any) -> None:
    """配置 PostgreSQL 的一致性只读事务。"""
    try:
        dialect_name = db.get_bind().dialect.name
    except (AttributeError, RuntimeError):
        dialect_name = None
    if dialect_name == "postgresql":
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


def _remaining_shares(order: Order) -> int:
    return max(int(order.shares or 0) - int(order.filled_shares or 0), 0)


def _build_realtime_market_snapshot(
    market: StockRealtimeMarket,
    price: Decimal,
) -> RealtimeMarketSnapshot:
    return RealtimeMarketSnapshot(
        price=price,
        timestamp=market.timestamp,
        pct_chg=_decimal_or_none(market.change_percent),
        turnover_rate=_decimal_or_none(market.turnover_rate),
        volume_ratio=_decimal_or_none(market.volume_ratio),
        amplitude=_decimal_or_none(market.amplitude),
        pb=_decimal_or_none(market.pb_ratio),
        pe=_decimal_or_none(market.pe_dynamic),
        amount=_decimal_or_none(market.turnover),
        volume=_decimal_or_none(market.volume),
        turnover=_decimal_or_none(market.turnover),
        total_market_cap=_decimal_or_none(market.total_market_cap),
        circulating_market_cap=_decimal_or_none(market.circulating_market_cap),
    )


def _legacy_stop_loss(purchase_details: Any) -> Decimal | None:
    if not isinstance(purchase_details, dict):
        return None
    return _positive_decimal(purchase_details.get("stop_loss"))


def _coerce_uuid(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise DecisionSnapshotError("snapshot_invalid_session_id", "session_id must be a valid UUID") from exc


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None else Decimal("0")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _positive_decimal(value: Any) -> Decimal | None:
    parsed = _decimal_or_none(value)
    return parsed if parsed is not None and parsed > 0 else None
