from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy import desc, select

from app.ai.llm_engine.context.canonical_metrics import CanonicalMetricsProvider
from app.ai.llm_engine.context.portfolio import build_portfolio_risk_control_context
from app.ai.llm_engine.context.runtime import merge_status
from app.ai.llm_engine.context.types import AIContextLayer, AIContextPayload
from app.models.user import User
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import StockValuationHistory
from app.models.data_storage import StockRealtimeMarket
from app.models.stock_indicators import StockIndicators
from app.performance.service import get_latest_performance_summary
from app.portfolio.service import get_portfolio_overview


def _raw_number(value: Any) -> float | None:
    """转换数据库原始数值为浮点数。

    Args:
        value: 数据库原始数值。

    Returns:
        可参与计算的浮点数；无法转换时返回 None。
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _percent_change(current_value: Any, base_value: Any) -> float | None:
    """计算两个同口径数值之间的百分比变化。

    Args:
        current_value: 当前值。
        base_value: 基准值。

    Returns:
        百分比变化值；缺少数值或基准为零时返回 None。
    """
    current = _raw_number(current_value)
    base = _raw_number(base_value)
    if current is None or base in (None, 0):
        return None
    return round((current - base) / base * 100, 4)


async def _build_price_position_summary(db: Any, stock_code: str) -> AIContextPayload:
    """构建实时价格相对技术指标的位置摘要。

    Args:
        db: 数据库会话。
        stock_code: 标准股票代码。

    Returns:
        仅使用实时行情和技术指标原始数据派生的价格位置摘要。
    """
    market_result = await db.execute(
        select(StockRealtimeMarket)
        .where(StockRealtimeMarket.stock_code == stock_code)
        .order_by(
            desc(StockRealtimeMarket.timestamp),
            desc(StockRealtimeMarket.updated_at),
            desc(StockRealtimeMarket.created_at),
        )
    )
    market = market_result.scalars().first()
    indicator_result = await db.execute(
        select(StockIndicators)
        .where(StockIndicators.stock_code == stock_code)
        .order_by(desc(StockIndicators.trade_date))
    )
    indicators = indicator_result.scalars().first()

    price = _raw_number(market.current_price) if market else None
    boll_upper = _raw_number(indicators.boll_upper) if indicators else None
    boll_lower = _raw_number(indicators.boll_lower) if indicators else None
    boll_range = (
        boll_upper - boll_lower
        if boll_upper is not None and boll_lower is not None
        else None
    )
    payload: AIContextPayload = {
        "status": "available" if price is not None and indicators else "missing",
        "price_vs_ma5_pct": _percent_change(price, indicators.ma5) if indicators else None,
        "price_vs_ma20_pct": _percent_change(price, indicators.ma20) if indicators else None,
        "price_vs_ma60_pct": _percent_change(price, indicators.ma60) if indicators else None,
        "price_vs_boll_mid_pct": _percent_change(price, indicators.boll_mid) if indicators else None,
        "price_position_in_boll_pct": (
            round((price - boll_lower) / boll_range * 100, 4)
            if price is not None and boll_lower is not None and boll_range not in (None, 0)
            else None
        ),
    }
    return format_payload_values("technical.price_position_summary", payload)


def _csv_value(value: Any) -> str:
    """转换 CSV 单元格值，避免时间序列上下文重复键名。

    Args:
        value: 待写入 CSV 单元格的原始值。

    Returns:
        可安全放入紧凑 CSV 行的字符串。
    """
    if value is None:
        return ""
    text = str(value)
    if any(char in text for char in [",", "\n", '"']):
        return '"' + text.replace('"', '""') + '"'
    return text


def _compact_series_payload(
    items: Any,
    *,
    columns: list[str],
    empty_status: str = "missing",
    **extra: Any,
) -> AIContextPayload:
    """将对象数组压缩为 columns + CSV rows 结构。

    Args:
        items: 字典列表形式的时间序列数据。
        columns: 保留并输出的字段顺序。
        empty_status: 空数据时返回的状态。
        **extra: 需要附加到上下文 payload 的元数据。

    Returns:
        面向 LLM 的紧凑时间序列 payload。
    """
    rows = [
        ",".join(_csv_value(item.get(column)) for column in columns)
        for item in (items or [])
        if isinstance(item, dict)
    ]
    payload: AIContextPayload = {
        "status": "available" if rows else empty_status,
        "format": "csv_rows",
        "columns": columns,
        "rows": rows,
        "record_count": len(rows),
    }
    payload.update(extra)
    return payload


def _wrap_dict(reader: Any, raw: Any) -> Any:
    return reader.wrap_dict(raw)


def _wrap_snapshot(reader: Any, raw: Any) -> Any:
    return reader.wrap_snapshot(raw)


def _wrap_list(
    reader: Any,
    items: Any,
    *,
    empty_status: str = "missing",
    include_count: bool = False,
) -> Any:
    return reader.wrap_list(
        items,
        empty_status=empty_status,
        include_count=include_count,
    )


def _items_payload(items: Any, *, empty_status: str = "missing", **extra: Any) -> AIContextPayload:
    payload: AIContextPayload = {
        "status": "available" if items else empty_status,
        "items": items,
    }
    payload.update(extra)
    return payload


class MetadataProvider:
    name = "metadata"

    async def _latest_valuation(self, db: Any, stock_code: str) -> Any:
        """读取最新估值记录。

        Args:
            db: 数据库会话。
            stock_code: 标准股票代码。

        Returns:
            最新估值记录；不存在时返回 None。
        """
        result = await db.execute(
            select(StockValuationHistory)
            .where(StockValuationHistory.stock_code == stock_code)
            .order_by(desc(StockValuationHistory.data_date))
        )
        return result.scalars().first()

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        async with runtime.async_session() as db:
            stock = await runtime.get_stock_basic(db)
            latest_valuation = await self._latest_valuation(db, runtime.stock_code)
            total_share = latest_valuation.total_share if latest_valuation else None
            float_share = latest_valuation.float_share if latest_valuation else None
            company = {
                "industry": stock.industry if stock else None,
                "area": stock.area if stock else None,
                "list_date": str(stock.list_date) if stock and stock.list_date else None,
                "total_share": total_share,
                "float_share": float_share,
                "share_unit": "shares" if total_share or float_share else None,
                "share_source": "stock_valuation_history" if total_share or float_share else None,
            }
            payload = {
                "status": "available" if stock else "missing",
                "generated_at": runtime.generated_at.isoformat(),
                "stock_code": runtime.stock_code,
                "stock_name": await runtime.stock_name(db),
                "company": company,
            }
            return AIContextLayer(self.name, payload)


class PortfolioProvider:
    name = "portfolio"

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        """构建当前用户账户组合与绩效上下文。

        Args:
            runtime: 当前 AI 上下文构建运行时。
            sections: 已构建的上下文分层。

        Returns:
            包含组合概览和绩效摘要的上下文分层。
        """
        user_id = getattr(runtime, "user_id", None)
        if user_id is None:
            return AIContextLayer(
                self.name,
                {
                    "status": "missing",
                    "reason": "current_user_unavailable",
                    "overview": {"status": "missing"},
                    "performance": {"status": "missing"},
                },
            )

        async with runtime.async_session() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalars().first()
            if user is None:
                return AIContextLayer(
                    self.name,
                    {
                        "status": "missing",
                        "reason": "current_user_not_found",
                        "overview": {"status": "missing"},
                        "performance": {"status": "missing"},
                    },
                )

            payload = {
                "status": "available",
                "overview": format_payload_values("portfolio.overview", await get_portfolio_overview(user=user)),
                "performance": format_payload_values(
                    "portfolio.performance",
                    await get_latest_performance_summary(user_id=user_id),
                ),
                "risk_control": await build_portfolio_risk_control_context(user=user),
            }
            return AIContextLayer(self.name, payload)


class RealtimeProvider:
    name = "realtime"

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        technical = runtime.readers.technical
        capital_flow = runtime.readers.capital_flow
        async with runtime.async_session() as db:
            market = _wrap_dict(technical, await technical.realtime_market(db, runtime.stock_code))
            indicators = _wrap_dict(technical, await technical.latest_indicators(db, runtime.stock_code))
            money_flow = await capital_flow.money_flow(db, runtime.stock_code)
            index_reference = _wrap_dict(technical, await technical.index_context(db))
            payload = {
                "status": merge_status(market, indicators, money_flow, index_reference),
                "market": market,
                "indicators": indicators,
                "price_position_summary": await _build_price_position_summary(db, runtime.stock_code),
                "money_flow": money_flow,
                "index_reference": index_reference,
            }
            return AIContextLayer(self.name, payload)


class SnapshotProvider:
    name = "snapshot"

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        """构建单股静态快照上下文。

        Args:
            runtime: 当前 AI 上下文构建运行时。
            sections: 已构建的上下文分层。

        Returns:
            包含公司、财报、估值、预测、持有人和资金流快照的上下文分层。
        """
        fundamental = runtime.readers.fundamental
        financial = runtime.readers.financial
        capital_flow = runtime.readers.capital_flow
        async with runtime.async_session() as db:
            basic_info = await fundamental.basic_info(db, runtime.stock_code)
            industry_rank = await fundamental.industry_rank(db, runtime.stock_code)
            company = {
                "status": merge_status(basic_info, industry_rank),
                "basic": _wrap_dict(fundamental, basic_info),
                "industry_rank": _wrap_dict(fundamental, industry_rank),
            }

            financial_records = await financial.financial_records(db, runtime.stock_code)
            income_records = await financial.income_statement_records(db, runtime.stock_code)
            balance_records = await financial.balance_sheet_records(db, runtime.stock_code)
            cashflow_records = await financial.cashflow_statement_records(db, runtime.stock_code)
            financial_statements = {
                "status": merge_status(financial_records, income_records, balance_records, cashflow_records),
                "financial_indicator": _items_payload(
                    financial_records,
                    item_count=len(financial_records),
                ),
                "income_statement": _items_payload(
                    income_records,
                    item_count=len(income_records),
                ),
                "balance_sheet": _items_payload(
                    balance_records,
                    item_count=len(balance_records),
                ),
                "cashflow_statement": _items_payload(
                    cashflow_records,
                    item_count=len(cashflow_records),
                ),
            }
            valuation = _wrap_dict(fundamental, await fundamental.valuation(db, runtime.stock_code))
            northbound = _wrap_dict(fundamental, await fundamental.northbound_flow(db, runtime.stock_code))

            top_holders = await fundamental.top_holders(db, runtime.stock_code)
            fund_holding = await fundamental.fund_holding(db, runtime.stock_code)
            ownership = {
                "status": merge_status(top_holders, fund_holding),
                "top_holders": _wrap_dict(fundamental, top_holders),
                "fund_holding": _wrap_dict(fundamental, fund_holding),
            }

            flow_northbound = await capital_flow.northbound(db, runtime.stock_code)
            dragon_tiger = await capital_flow.dragon_tiger(db, runtime.stock_code)
            flow_snapshot = {
                "status": merge_status(flow_northbound, dragon_tiger),
                "northbound": flow_northbound,
                "dragon_tiger": dragon_tiger,
            }
            payload = {
                "status": merge_status(
                    company,
                    financial_statements,
                    valuation,
                    northbound,
                    ownership,
                    flow_snapshot,
                ),
                "company": company,
                "financial_statements": financial_statements,
                "valuation": valuation,
                "northbound": northbound,
                "ownership": ownership,
                "flow": flow_snapshot,
            }
            return AIContextLayer(self.name, payload)


class HistoryProvider:
    name = "history"

    KLINE_COLUMNS = ["date", "open", "high", "low", "close", "volume", "pct_chg"]
    MONEY_FLOW_TREND_COLUMNS = ["date", "net_inflow_main", "net_inflow_ratio_main", "pct_chg"]

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        """构建历史行情、资金流和公司事件上下文。

        Args:
            runtime: 当前 AI 上下文构建运行时。
            sections: 已构建的上下文分层。

        Returns:
            包含紧凑时间序列和历史摘要的上下文分层。
        """
        technical = runtime.readers.technical
        capital_flow = runtime.readers.capital_flow
        fundamental = runtime.readers.fundamental
        async with runtime.async_session() as db:
            kline_items = await technical.recent_klines(db, runtime.stock_code, days=30)
            money_flow_trend_items = await capital_flow.money_flow_trend(db, runtime.stock_code)
            money_flow_trend_summary = await capital_flow.money_flow_trend_summary(db, runtime.stock_code)
            northbound_trend = await capital_flow.northbound_trend(db, runtime.stock_code)
            insider_activity = await fundamental.insider_activity(db, runtime.stock_code)
            seo_history = await fundamental.seo_history(db, runtime.stock_code)
            kline = _compact_series_payload(
                kline_items,
                columns=self.KLINE_COLUMNS,
                window_days=30,
            )
            money_flow_trend = _compact_series_payload(
                money_flow_trend_items,
                columns=self.MONEY_FLOW_TREND_COLUMNS,
                summary=money_flow_trend_summary,
            )
            payload = {
                "status": merge_status(
                    kline,
                    money_flow_trend,
                    northbound_trend,
                    seo_history,
                ),
                "kline": kline,
                "money_flow_trend": money_flow_trend,
                "northbound_trend": northbound_trend,
                "insider_activity": _wrap_dict(fundamental, insider_activity),
                "seo_history": _wrap_dict(fundamental, seo_history),
            }
            return AIContextLayer(self.name, payload)


class SignalsProvider:
    name = "signals"

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        """构建风险、热度和资金流信号上下文。

        Args:
            runtime: 当前 AI 上下文构建运行时。
            sections: 已构建的上下文分层。

        Returns:
            包含情绪热度、风险预警和资金流信号的上下文分层。
        """
        sentiment = runtime.readers.sentiment
        risk = runtime.readers.risk
        capital_flow = runtime.readers.capital_flow
        fundamental = runtime.readers.fundamental
        async with runtime.async_session() as db:
            hot_rank = await sentiment.hot_rank(db, runtime.stock_code)
            hot_rank_signal = _wrap_dict(sentiment, hot_rank)

            pledge = await risk.pledge(db, runtime.stock_code)
            insider = await risk.insider(db, runtime.stock_code)
            shareholder = await risk.shareholder(db, runtime.stock_code)
            shareholder_trend = await risk.shareholder_trend(db, runtime.stock_code)
            risk_signals = {
                "status": merge_status(pledge, insider, shareholder, shareholder_trend),
                "pledge": _wrap_dict(risk, pledge),
                "insider": _wrap_list(risk, insider),
                "shareholder": _wrap_dict(risk, shareholder),
                "shareholder_trend": _wrap_dict(risk, shareholder_trend),
            }

            dragon_tiger_effect = await capital_flow.dragon_tiger_effect(db, runtime.stock_code)
            sector_flow = await capital_flow.sector_flow(db, runtime.stock_code)
            block_trade = await capital_flow.block_trade(db, runtime.stock_code)
            margin = await capital_flow.margin(db, runtime.stock_code)
            margin_analysis = await fundamental.margin_analysis(db, runtime.stock_code)
            flow_signals = {
                "status": merge_status(dragon_tiger_effect, sector_flow, block_trade, margin, margin_analysis),
                "dragon_tiger_effect": dragon_tiger_effect,
                "sector_flow": sector_flow,
                "block_trade": block_trade,
                "margin": margin,
                "margin_analysis": _wrap_dict(fundamental, margin_analysis),
            }
            payload = {
                "status": merge_status(hot_rank_signal, risk_signals, flow_signals),
                "hot_rank": hot_rank_signal,
                "risk": risk_signals,
                "flow": flow_signals,
            }
            return AIContextLayer(self.name, payload)


class EventsProvider:
    name = "events"

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        risk = runtime.readers.risk
        async with runtime.async_session() as db:
            earnings = await runtime.build_earnings_countdown(db)
            lockup_items = await risk.lockup(db, runtime.stock_code)
            lockup = _wrap_list(risk, lockup_items)
            payload = {
                "status": merge_status(earnings, lockup),
                "earnings_countdown": earnings,
                "lockup_release": lockup,
            }
            return AIContextLayer(self.name, payload)


DEFAULT_CONTEXT_PROVIDERS = (
    MetadataProvider(),
    PortfolioProvider(),
    RealtimeProvider(),
    SnapshotProvider(),
    HistoryProvider(),
    SignalsProvider(),
    EventsProvider(),
    CanonicalMetricsProvider(),
)
