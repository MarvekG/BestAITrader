from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from statistics import stdev
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.data_storage import KlineData, StockBasic, StockRealtimeMarket
from app.models.position import Position
from app.trading.trading_engine import TradingEngine

MONEY_QUANT = Decimal("0.0001")
RATIO_QUANT = Decimal("0.00000001")
UNKNOWN_INDUSTRY = "未知行业"
UNKNOWN_STOCK_NAME = "Unknown"
TRADING_DAYS_PER_YEAR = 252

trading_engine = TradingEngine()


def _to_decimal(value: object, default: Decimal = Decimal("0")) -> Decimal:
    """将可空数值转换为 Decimal，避免浮点二进制误差进入估值计算。

    Args:
        value: 待转换的数值或空值。
        default: 空值时使用的默认 Decimal。

    Returns:
        转换后的 Decimal。
    """
    if value is None:
        return default
    return Decimal(str(value))


def _quantize(value: Decimal, quant: Decimal) -> Decimal:
    """按指定精度四舍五入数值，统一 API 金额和比例输出口径。

    Args:
        value: 待处理的 Decimal 数值。
        quant: 目标精度。

    Returns:
        按目标精度处理后的 Decimal。
    """
    return value.quantize(quant, rounding=ROUND_HALF_UP)


def _to_float(value: Decimal | None) -> float | None:
    """将 Decimal 转换为 API 友好的浮点数。

    Args:
        value: 待转换的 Decimal 或空值。

    Returns:
        浮点数；输入为空时返回 None。
    """
    if value is None:
        return None
    return float(value)


def _safe_positive_decimal(value: object) -> Decimal | None:
    """读取有效正数 Decimal，过滤空值和非正数价格。

    Args:
        value: 待解析的价格或数量。

    Returns:
        正数 Decimal；不可用时返回 None。
    """
    decimal_value = _to_decimal(value)
    if decimal_value <= 0:
        return None
    return decimal_value


def _resolve_current_price(position: Position, market_price: object) -> Decimal:
    """按实时价、持仓快照价、成本价顺序确定估值价格。

    Args:
        position: 当前持仓记录。
        market_price: 最新行情价。

    Returns:
        用于组合估值的当前价格。
    """
    for candidate in (market_price, position.current_price, position.avg_cost):
        price = _safe_positive_decimal(candidate)
        if price is not None:
            return price
    return Decimal("0")


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """计算比例并处理除零场景。

    Args:
        numerator: 分子。
        denominator: 分母。

    Returns:
        按比例精度格式化后的 Decimal。
    """
    if denominator <= 0:
        return Decimal("0.00000000")
    return _quantize(numerator / denominator, RATIO_QUANT)


def _get_position_rows(
    db: Session,
    account_id: object,
) -> list[tuple[Position, str | None, str | None, object]]:
    """读取当前账户有效持仓及其最新行情和基础信息。

    Args:
        db: 数据库会话。
        account_id: 账户 ID。

    Returns:
        持仓、股票名称、行业和最新价格的元组列表。
    """
    latest_market = (
        db.query(
            StockRealtimeMarket.stock_code,
            StockRealtimeMarket.current_price,
            func.row_number().over(
                partition_by=StockRealtimeMarket.stock_code,
                order_by=StockRealtimeMarket.timestamp.desc(),
            ).label("rn"),
        )
        .subquery()
    )

    return (
        db.query(Position, StockBasic.name, StockBasic.industry, latest_market.c.current_price)
        .outerjoin(StockBasic, Position.stock_code == StockBasic.stock_code)
        .outerjoin(
            latest_market,
            (Position.stock_code == latest_market.c.stock_code) & (latest_market.c.rn == 1),
        )
        .filter(Position.account_id == account_id, Position.total_shares > 0)
        .all()
    )


def _build_industry_allocations(
    positions: list[dict[str, Any]],
    total_assets: Decimal,
) -> list[dict[str, Any]]:
    """按行业汇总组合市值和权重。

    Args:
        positions: 已完成单票估值的持仓列表。
        total_assets: 组合动态总资产。

    Returns:
        按行业权重降序排列的行业配置列表。
    """
    grouped: dict[str, dict[str, Any]] = {}
    for item in positions:
        industry = str(item["industry"] or UNKNOWN_INDUSTRY)
        if industry not in grouped:
            grouped[industry] = {
                "industry": industry,
                "market_value_decimal": Decimal("0"),
                "position_count": 0,
                "stock_codes": [],
            }
        grouped[industry]["market_value_decimal"] += item["market_value_decimal"]
        grouped[industry]["position_count"] += 1
        grouped[industry]["stock_codes"].append(item["stock_code"])

    allocations = []
    for item in grouped.values():
        market_value = _quantize(item["market_value_decimal"], MONEY_QUANT)
        allocations.append(
            {
                "industry": item["industry"],
                "market_value": _to_float(market_value),
                "market_value_decimal": market_value,
                "weight": _to_float(_ratio(market_value, total_assets)),
                "weight_decimal": _ratio(market_value, total_assets),
                "position_count": item["position_count"],
                "stock_codes": item["stock_codes"],
            }
        )

    return sorted(allocations, key=lambda item: item["weight_decimal"], reverse=True)


def _strip_internal_decimals(item: dict[str, Any]) -> dict[str, Any]:
    """移除仅用于服务内部排序和汇总的 Decimal 字段。

    Args:
        item: 内部估值字典。

    Returns:
        可直接序列化给 API 的字典。
    """
    return {key: value for key, value in item.items() if not key.endswith("_decimal")}


def _position_stop_loss(position: Position) -> Decimal | None:
    """读取持仓配置的有效止损价。

    Args:
        position: 当前持仓记录。

    Returns:
        有效止损价；未配置或无效时返回 None。
    """
    purchase_details = position.purchase_details if isinstance(position.purchase_details, dict) else {}
    stop_loss = purchase_details.get("stop_loss")
    if stop_loss in (None, ""):
        return None
    return _safe_positive_decimal(stop_loss)


def _get_recent_closes(db: Session, stock_code: str, limit: int) -> list[float]:
    """读取指定股票最近的日线收盘价。

    Args:
        db: 数据库会话。
        stock_code: 股票代码。
        limit: 需要的收益率天数。

    Returns:
        按时间升序排列的有效收盘价列表。
    """
    rows = (
        db.query(KlineData.close)
        .filter(KlineData.stock_code == stock_code, KlineData.freq == "D", KlineData.close.isnot(None))
        .order_by(KlineData.date.desc())
        .limit(limit + 1)
        .all()
    )
    closes = [float(row[0]) for row in reversed(rows) if row[0] is not None and float(row[0]) > 0]
    return closes


def _annualized_volatility(closes: list[float]) -> Decimal | None:
    """根据收盘价计算年化波动率。

    Args:
        closes: 按时间升序排列的收盘价。

    Returns:
        年化波动率；样本不足时返回 None。
    """
    if len(closes) < 3:
        return None
    returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    if len(returns) < 2:
        return None
    return _quantize(Decimal(str(stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR))), RATIO_QUANT)


def _estimate_portfolio_volatility(
    db: Session,
    positions: list[dict[str, Any]],
    days: int,
) -> Decimal | None:
    """用持仓权重和个股波动估算组合波动率。

    Args:
        db: 数据库会话。
        positions: 已完成单票估值的持仓列表。
        days: 波动率观察窗口天数。

    Returns:
        估算组合年化波动率；没有可用样本时返回 None。
    """
    weighted_variance = Decimal("0")
    has_volatility = False
    for item in positions:
        volatility = _annualized_volatility(_get_recent_closes(db, item["stock_code"], days))
        if volatility is None:
            continue
        has_volatility = True
        weighted_variance += (item["weight_decimal"] * volatility) ** 2

    if not has_volatility:
        return None
    return _quantize(Decimal(str(math.sqrt(float(weighted_variance)))), RATIO_QUANT)


def _build_risk_metrics(
    db: Session,
    positions: list[dict[str, Any]],
    industry_allocations: list[dict[str, Any]],
) -> dict[str, Any]:
    """计算组合层集中度、止损覆盖率和波动率风险指标。

    Args:
        db: 数据库会话。
        positions: 已完成单票估值的持仓列表。
        industry_allocations: 已完成汇总的行业配置列表。

    Returns:
        组合风险指标字典。
    """
    position_hhi = _quantize(
        sum((item["weight_decimal"] ** 2 for item in positions), Decimal("0")),
        RATIO_QUANT,
    )
    industry_hhi = _quantize(
        sum((item["weight_decimal"] ** 2 for item in industry_allocations), Decimal("0")),
        RATIO_QUANT,
    )
    top_position = positions[0] if positions else None
    top_industry = industry_allocations[0] if industry_allocations else None
    loss_positions = [item for item in positions if item["unrealized_pnl_pct_decimal"] < 0]
    max_loss = min(loss_positions, key=lambda item: item["unrealized_pnl_pct_decimal"]) if loss_positions else None
    covered_count = sum(1 for item in positions if item["has_stop_loss"])
    stop_loss_coverage = _ratio(Decimal(covered_count), Decimal(len(positions))) if positions else Decimal("0")

    return {
        "top_single_position_pct": _to_float(top_position["weight_decimal"] if top_position else Decimal("0")),
        "top_single_position_stock_code": top_position["stock_code"] if top_position else None,
        "top_industry_position_pct": _to_float(top_industry["weight_decimal"] if top_industry else Decimal("0")),
        "top_industry": top_industry["industry"] if top_industry else None,
        "position_hhi": _to_float(position_hhi),
        "industry_hhi": _to_float(industry_hhi),
        "max_unrealized_loss_pct": _to_float(max_loss["unrealized_pnl_pct_decimal"] if max_loss else Decimal("0")),
        "max_unrealized_loss_stock_code": max_loss["stock_code"] if max_loss else None,
        "stop_loss_coverage_pct": _to_float(stop_loss_coverage),
        "estimated_volatility_20d": _to_float(_estimate_portfolio_volatility(db, positions, 20)),
        "estimated_volatility_60d": _to_float(_estimate_portfolio_volatility(db, positions, 60)),
    }


def build_portfolio_valuation(db: Session, account: Account) -> dict[str, Any]:
    """按统一动态估值口径聚合账户组合数据。

    Args:
        db: 数据库会话。
        account: 当前用户账户。

    Returns:
        包含动态资产、持仓、行业分布和风险指标的内部估值结构。
    """
    available_cash = _quantize(_to_decimal(account.available_cash), MONEY_QUANT)
    frozen_cash = _quantize(_to_decimal(account.frozen_cash), MONEY_QUANT)
    position_items = []

    for position, stock_name, industry, market_price in _get_position_rows(db, account.account_id):
        shares = int(position.total_shares or 0)
        share_fields = trading_engine.derive_share_fields(
            shares,
            position.purchase_details,
            position.available_shares,
        )
        current_price = _quantize(_resolve_current_price(position, market_price), MONEY_QUANT)
        avg_cost = _quantize(_to_decimal(position.avg_cost), MONEY_QUANT)
        market_value = _quantize(current_price * Decimal(shares), MONEY_QUANT)
        cost_value = avg_cost * Decimal(shares)
        unrealized_pnl = _quantize(market_value - cost_value, MONEY_QUANT)
        unrealized_pnl_pct = _ratio(unrealized_pnl, cost_value)

        stop_loss = _position_stop_loss(position)
        position_items.append(
            {
                "position_id": str(position.position_id),
                "session_id": str(position.session_id) if position.session_id else None,
                "stock_code": position.stock_code,
                "stock_name": stock_name or UNKNOWN_STOCK_NAME,
                "industry": industry or UNKNOWN_INDUSTRY,
                "total_shares": shares,
                "available_shares": share_fields["available_shares"],
                "frozen_shares": share_fields["frozen_shares"],
                "avg_cost": _to_float(avg_cost),
                "avg_cost_decimal": avg_cost,
                "current_price": _to_float(current_price),
                "current_price_decimal": current_price,
                "market_value": _to_float(market_value),
                "market_value_decimal": market_value,
                "weight_decimal": Decimal("0"),
                "weight": 0.0,
                "unrealized_pnl": _to_float(unrealized_pnl),
                "unrealized_pnl_decimal": unrealized_pnl,
                "unrealized_pnl_pct": _to_float(unrealized_pnl_pct),
                "unrealized_pnl_pct_decimal": unrealized_pnl_pct,
                "stop_loss": _to_float(stop_loss),
                "stop_loss_decimal": stop_loss,
                "has_stop_loss": stop_loss is not None,
                "updated_at": position.updated_at.isoformat() if position.updated_at else None,
            }
        )

    market_value = _quantize(
        sum((item["market_value_decimal"] for item in position_items), Decimal("0")),
        MONEY_QUANT,
    )
    total_assets = _quantize(available_cash + frozen_cash + market_value, MONEY_QUANT)

    for item in position_items:
        item["weight_decimal"] = _ratio(item["market_value_decimal"], total_assets)
        item["weight"] = _to_float(item["weight_decimal"])

    sorted_positions = sorted(position_items, key=lambda item: item["weight_decimal"], reverse=True)
    industry_allocations = _build_industry_allocations(sorted_positions, total_assets)

    return {
        "summary": {
            "total_assets": _to_float(total_assets),
            "total_assets_decimal": total_assets,
            "available_cash": _to_float(available_cash),
            "available_cash_decimal": available_cash,
            "frozen_cash": _to_float(frozen_cash),
            "frozen_cash_decimal": frozen_cash,
            "market_value": _to_float(market_value),
            "market_value_decimal": market_value,
            "cash_ratio": _to_float(_ratio(available_cash + frozen_cash, total_assets)),
            "position_ratio": _to_float(_ratio(market_value, total_assets)),
            "position_count": len(position_items),
        },
        "positions": sorted_positions,
        "industry_allocations": industry_allocations,
        "risk_metrics": _build_risk_metrics(db, sorted_positions, industry_allocations),
    }


def build_portfolio_overview_payload(valuation: dict[str, Any]) -> dict[str, Any]:
    """将内部估值结构转换为组合概览 API 响应。

    Args:
        valuation: `build_portfolio_valuation` 返回的内部估值结构。

    Returns:
        可直接返回给前端的组合概览数据。
    """
    positions = valuation["positions"]
    top_gainers = sorted(positions, key=lambda item: item["unrealized_pnl_decimal"], reverse=True)[:5]
    top_losers = sorted(positions, key=lambda item: item["unrealized_pnl_decimal"])[:5]

    return {
        "summary": _strip_internal_decimals(valuation["summary"]),
        "positions": [_strip_internal_decimals(item) for item in positions],
        "industry_allocations": [_strip_internal_decimals(item) for item in valuation["industry_allocations"]],
        "risk_metrics": valuation["risk_metrics"],
        "top_weights": [_strip_internal_decimals(item) for item in positions[:5]],
        "top_gainers": [_strip_internal_decimals(item) for item in top_gainers],
        "top_losers": [_strip_internal_decimals(item) for item in top_losers],
    }
