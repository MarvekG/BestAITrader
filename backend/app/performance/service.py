from __future__ import annotations

from datetime import date
from datetime import date as date_type
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import database as database_module
from app.models.account import Account
from app.models.account_equity_snapshot import AccountEquitySnapshot
from app.models.data_storage import IndexDaily
from app.models.position import Position
from app.portfolio.valuation import build_portfolio_valuation

DEFAULT_BENCHMARK_CODE = "000300.SH"
RETURN_QUANT = Decimal("0.00000001")
MONEY_QUANT = Decimal("0.0001")
BENCHMARK_QUANT = Decimal("0.000001")


def _to_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    """将数值转换为 Decimal，空值使用默认值。

    Args:
        value: 待转换的数值。
        default: 输入为空时使用的默认值。

    Returns:
        转换后的 Decimal。
    """
    if value is None:
        return default
    return Decimal(str(value))


def _quantize(value: Decimal, quant: Decimal) -> Decimal:
    """按指定精度四舍五入 Decimal。

    Args:
        value: 待处理的 Decimal。
        quant: 目标精度。

    Returns:
        按目标精度处理后的 Decimal。
    """
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _return_ratio(current: Decimal, previous: Decimal) -> Decimal | None:
    """计算两个数值之间的收益率。

    Args:
        current: 当前数值。
        previous: 对比基准数值。

    Returns:
        收益率；当基准数值小于等于 0 时返回 None。
    """
    if previous <= 0:
        return None
    return _quantize((current / previous) - Decimal("1"), RETURN_QUANT)


async def _get_position_count(db: AsyncSession, account_id: object) -> int:
    """统计账户当前有效持仓数量。

    Args:
        db: 数据库会话。
        account_id: 账户 ID。

    Returns:
        当前有效持仓数量。
    """
    result = await db.execute(
        select(func.count(Position.position_id)).where(
            Position.account_id == account_id,
            Position.total_shares > 0,
        )
    )
    return int(result.scalar_one() or 0)


async def _get_benchmark_close(
    db: AsyncSession,
    *,
    benchmark_code: str,
    snapshot_date: date,
) -> Decimal | None:
    """读取快照日期之前最近一个可用基准收盘价。

    Args:
        db: 数据库会话。
        benchmark_code: 基准指数代码。
        snapshot_date: 快照日期。

    Returns:
        最近可用基准收盘价；没有数据时返回 None。
    """
    result = await db.execute(
        select(IndexDaily)
        .where(IndexDaily.index_code == benchmark_code, IndexDaily.trade_date <= snapshot_date)
        .order_by(IndexDaily.trade_date.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None or row.close is None:
        return None
    return _quantize(Decimal(str(row.close)), BENCHMARK_QUANT)


async def _get_latest_snapshot(
    db: AsyncSession,
    *,
    account_id: object,
    benchmark_code: str,
    before_date: date,
) -> AccountEquitySnapshot | None:
    """读取指定日期之前最近一个账户快照。

    Args:
        db: 数据库会话。
        account_id: 账户 ID。
        benchmark_code: 基准指数代码。
        before_date: 截止日期，不包含该日期。

    Returns:
        最近一个账户快照；没有快照时返回 None。
    """
    result = await db.execute(
        select(AccountEquitySnapshot)
        .where(
            AccountEquitySnapshot.account_id == account_id,
            AccountEquitySnapshot.benchmark_code == benchmark_code,
            AccountEquitySnapshot.snapshot_date < before_date,
        )
        .order_by(AccountEquitySnapshot.snapshot_date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_first_snapshot(
    db: AsyncSession,
    *,
    account_id: object,
    benchmark_code: str,
) -> AccountEquitySnapshot | None:
    """读取账户最早的快照。

    Args:
        db: 数据库会话。
        account_id: 账户 ID。
        benchmark_code: 基准指数代码。

    Returns:
        最早的账户快照；没有快照时返回 None。
    """
    result = await db.execute(
        select(AccountEquitySnapshot)
        .where(
            AccountEquitySnapshot.account_id == account_id,
            AccountEquitySnapshot.benchmark_code == benchmark_code,
        )
        .order_by(AccountEquitySnapshot.snapshot_date.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_existing_snapshot(
    db: AsyncSession,
    *,
    account_id: object,
    benchmark_code: str,
    snapshot_date: date,
) -> AccountEquitySnapshot | None:
    """读取同一账户、日期和基准的既有快照。

    Args:
        db: 数据库会话。
        account_id: 账户 ID。
        benchmark_code: 基准指数代码。
        snapshot_date: 快照日期。

    Returns:
        既有快照；不存在时返回 None。
    """
    result = await db.execute(
        select(AccountEquitySnapshot)
        .where(
            AccountEquitySnapshot.account_id == account_id,
            AccountEquitySnapshot.benchmark_code == benchmark_code,
            AccountEquitySnapshot.snapshot_date == snapshot_date,
        )
    )
    return result.scalar_one_or_none()


async def _get_historical_peak_assets(
    db: AsyncSession,
    *,
    account_id: object,
    benchmark_code: str,
    before_date: date,
) -> Decimal:
    """读取账户历史快照最高总资产。

    Args:
        db: 数据库会话。
        account_id: 账户 ID。
        benchmark_code: 基准指数代码。
        before_date: 只统计该日期之前的历史快照，避免同日重跑时旧值影响结果。

    Returns:
        历史最高总资产；没有快照时返回 0。
    """
    result = await db.execute(
        select(func.max(AccountEquitySnapshot.total_assets)).where(
            AccountEquitySnapshot.account_id == account_id,
            AccountEquitySnapshot.benchmark_code == benchmark_code,
            AccountEquitySnapshot.snapshot_date < before_date,
        )
    )
    return _to_decimal(result.scalar_one())


async def create_account_equity_snapshot(
    db: AsyncSession,
    *,
    account: Account,
    snapshot_date: date,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
) -> AccountEquitySnapshot:
    """创建或更新账户每日净值快照。

    Args:
        db: 数据库会话。
        account: 需要生成快照的模拟账户。
        snapshot_date: 快照日期。
        benchmark_code: 基准指数代码。

    Returns:
        已持久化的账户净值快照。
    """
    valuation = await build_portfolio_valuation(account)
    summary = valuation["summary"]
    total_assets = _quantize(_to_decimal(summary["total_assets_decimal"]), MONEY_QUANT)
    available_cash = _quantize(_to_decimal(summary["available_cash_decimal"]), MONEY_QUANT)
    market_value = _quantize(_to_decimal(summary["market_value_decimal"]), MONEY_QUANT)
    position_count = await _get_position_count(db, account.account_id)
    previous = await _get_latest_snapshot(
        db,
        account_id=account.account_id,
        benchmark_code=benchmark_code,
        before_date=snapshot_date,
    )
    first = await _get_first_snapshot(db, account_id=account.account_id, benchmark_code=benchmark_code)
    benchmark_close = await _get_benchmark_close(db, benchmark_code=benchmark_code, snapshot_date=snapshot_date)
    missing_benchmark_reason = None if benchmark_close is not None else "benchmark_close_missing"

    if previous is None:
        daily_return = Decimal("0.00000000")
        cumulative_return = Decimal("0.00000000")
        benchmark_daily_return = Decimal("0.00000000") if benchmark_close is not None else None
        benchmark_cumulative_return = Decimal("0.00000000") if benchmark_close is not None else None
        max_drawdown = Decimal("0.00000000")
        excess_return = Decimal("0.00000000") if benchmark_close is not None else None
    else:
        daily_return = _return_ratio(total_assets, _to_decimal(previous.total_assets))
        base_assets = _to_decimal(first.total_assets if first else previous.total_assets)
        cumulative_return = _return_ratio(total_assets, base_assets)
        previous_benchmark = _to_decimal(previous.benchmark_close) if previous.benchmark_close is not None else None
        first_benchmark = _to_decimal(first.benchmark_close) if first and first.benchmark_close is not None else None
        benchmark_daily_return = (
            _return_ratio(benchmark_close, previous_benchmark)
            if benchmark_close is not None and previous_benchmark is not None
            else None
        )
        benchmark_cumulative_return = (
            _return_ratio(benchmark_close, first_benchmark)
            if benchmark_close is not None and first_benchmark is not None
            else None
        )
        excess_return = (
            _quantize(cumulative_return - benchmark_cumulative_return, RETURN_QUANT)
            if cumulative_return is not None and benchmark_cumulative_return is not None
            else None
        )
        previous_drawdown = _to_decimal(previous.max_drawdown)
        historical_peak = await _get_historical_peak_assets(
            db,
            account_id=account.account_id,
            benchmark_code=benchmark_code,
            before_date=snapshot_date,
        )
        current_drawdown = _return_ratio(total_assets, max(historical_peak, total_assets)) or Decimal("0.00000000")
        max_drawdown = min(previous_drawdown, current_drawdown)

    snapshot = await _get_existing_snapshot(
        db,
        account_id=account.account_id,
        benchmark_code=benchmark_code,
        snapshot_date=snapshot_date,
    )
    if snapshot is None:
        snapshot = AccountEquitySnapshot(
            user_id=account.user_id,
            account_id=account.account_id,
            snapshot_date=snapshot_date,
            benchmark_code=benchmark_code,
        )

    snapshot.total_assets = total_assets
    snapshot.available_cash = available_cash
    snapshot.market_value = market_value
    snapshot.position_count = position_count
    snapshot.daily_return = daily_return
    snapshot.cumulative_return = cumulative_return
    snapshot.benchmark_close = benchmark_close
    snapshot.benchmark_daily_return = benchmark_daily_return
    snapshot.benchmark_cumulative_return = benchmark_cumulative_return
    snapshot.excess_return = excess_return
    snapshot.max_drawdown = max_drawdown
    snapshot.missing_benchmark_reason = missing_benchmark_reason

    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


def _to_float(value: object) -> float | None:
    """将可空数值转换为 float。

    Args:
        value: 待转换的数值。

    Returns:
        转换后的浮点数；空值返回 None。
    """
    if value is None:
        return None
    return float(value)


async def get_latest_performance_summary(
    *,
    user_id: int,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
) -> dict[str, object]:
    """查询当前用户最新模拟盘绩效摘要。

    Args:
        user_id: 当前用户 ID。
        benchmark_code: 基准指数代码。

    Returns:
        最新绩效摘要。没有快照时返回空指标。
    """
    async with database_module.AsyncSessionLocal() as db:
        account_result = await db.execute(select(Account).where(Account.user_id == user_id))
        account = account_result.scalar_one_or_none()
        if account is None:
            return {
                "snapshot_date": None,
                "benchmark_code": benchmark_code,
                "available_cash": None,
                "market_value": None,
                "position_count": 0,
                "cumulative_return": None,
                "benchmark_cumulative_return": None,
                "excess_return": None,
                "max_drawdown": None,
                "total_trades": 0,
            }

        snapshot_result = await db.execute(
            select(AccountEquitySnapshot)
            .where(
                AccountEquitySnapshot.account_id == account.account_id,
                AccountEquitySnapshot.benchmark_code == benchmark_code,
            )
            .order_by(AccountEquitySnapshot.snapshot_date.desc())
            .limit(1)
        )
        snapshot = snapshot_result.scalar_one_or_none()
        if snapshot is None:
            count_result = await db.execute(
                select(func.count(Position.position_id)).where(
                    Position.account_id == account.account_id,
                    Position.total_shares > 0,
                )
            )
            return {
                "snapshot_date": None,
                "benchmark_code": benchmark_code,
                "available_cash": _to_float(account.available_cash),
                "market_value": _to_float(account.market_value),
                "position_count": int(count_result.scalar_one() or 0),
                "cumulative_return": None,
                "benchmark_cumulative_return": None,
                "excess_return": None,
                "max_drawdown": None,
                "total_trades": int(account.total_trades or 0),
            }

        return {
            "snapshot_date": snapshot.snapshot_date.isoformat(),
            "benchmark_code": snapshot.benchmark_code,
            "available_cash": _to_float(snapshot.available_cash),
            "market_value": _to_float(snapshot.market_value),
            "position_count": int(snapshot.position_count or 0),
            "cumulative_return": _to_float(snapshot.cumulative_return),
            "benchmark_cumulative_return": _to_float(snapshot.benchmark_cumulative_return),
            "excess_return": _to_float(snapshot.excess_return),
            "max_drawdown": _to_float(snapshot.max_drawdown),
            "total_trades": int(account.total_trades or 0),
        }


async def get_equity_curve(
    *,
    user_id: int,
    benchmark_code: str = DEFAULT_BENCHMARK_CODE,
    start_date: date_type | None = None,
    end_date: date_type | None = None,
) -> dict[str, object]:
    """查询当前用户模拟账户净值曲线。

    Args:
        user_id: 当前用户 ID。
        benchmark_code: 基准指数代码。
        start_date: 查询开始日期。
        end_date: 查询结束日期。

    Returns:
        净值曲线和基准曲线数据。
    """
    async with database_module.AsyncSessionLocal() as db:
        account_result = await db.execute(select(Account).where(Account.user_id == user_id))
        account = account_result.scalar_one_or_none()
        if account is None:
            return {"benchmark_code": benchmark_code, "items": []}

        stmt = select(AccountEquitySnapshot).where(
            AccountEquitySnapshot.account_id == account.account_id,
            AccountEquitySnapshot.benchmark_code == benchmark_code,
        )
        if start_date is not None:
            stmt = stmt.where(AccountEquitySnapshot.snapshot_date >= start_date)
        if end_date is not None:
            stmt = stmt.where(AccountEquitySnapshot.snapshot_date <= end_date)

        result = await db.execute(stmt.order_by(AccountEquitySnapshot.snapshot_date.asc()))
        snapshots = result.scalars().all()
        return {
            "benchmark_code": benchmark_code,
            "items": [
                {
                    "snapshot_date": snapshot.snapshot_date.isoformat(),
                    "daily_return": _to_float(snapshot.daily_return),
                    "cumulative_return": _to_float(snapshot.cumulative_return),
                    "benchmark_close": _to_float(snapshot.benchmark_close),
                    "benchmark_daily_return": _to_float(snapshot.benchmark_daily_return),
                    "benchmark_cumulative_return": _to_float(snapshot.benchmark_cumulative_return),
                    "excess_return": _to_float(snapshot.excess_return),
                    "max_drawdown": _to_float(snapshot.max_drawdown),
                }
                for snapshot in snapshots
            ],
        }
