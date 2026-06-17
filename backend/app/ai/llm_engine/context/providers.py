from __future__ import annotations

from typing import Any, Mapping

from app.ai.llm_engine.context.canonical_metrics import CanonicalMetricsProvider
from app.ai.llm_engine.context.portfolio import build_portfolio_risk_control_context
from app.ai.llm_engine.context.runtime import merge_status
from app.ai.llm_engine.context.types import AIContextLayer, AIContextPayload
from app.crud.account import ensure_user_account
from app.models.user import User
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import StockValuationHistory
from app.performance.service import get_latest_performance_summary
from app.portfolio.service import get_portfolio_overview
from sqlalchemy import desc


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

    def _latest_valuation(self, db: Any, stock_code: str) -> Any:
        """读取最新估值记录。

        Args:
            db: 数据库会话。
            stock_code: 标准股票代码。

        Returns:
            最新估值记录；不存在时返回 None。
        """
        return db.query(StockValuationHistory).filter(
            StockValuationHistory.stock_code == stock_code,
        ).order_by(desc(StockValuationHistory.data_date)).first()

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        with runtime.db_session() as db:
            stock = runtime.get_stock_basic(db)
            latest_valuation = self._latest_valuation(db, runtime.stock_code)
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
                "stock_name": runtime.stock_name(db),
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

        with runtime.db_session() as db:
            user = db.query(User).filter(User.id == user_id).first()
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
                "overview": format_payload_values("portfolio.overview", get_portfolio_overview(db, user=user)),
                "performance": format_payload_values(
                    "portfolio.performance",
                    get_latest_performance_summary(db, user_id=user_id),
                ),
                "risk_control": build_portfolio_risk_control_context(db, ensure_user_account(db, user)),
            }
            return AIContextLayer(self.name, payload)


class RealtimeProvider:
    name = "realtime"

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        technical = runtime.readers.technical
        capital_flow = runtime.readers.capital_flow
        with runtime.db_session() as db:
            market = _wrap_dict(technical, technical.realtime_market(db, runtime.stock_code))
            indicators = _wrap_dict(technical, technical.latest_indicators(db, runtime.stock_code))
            money_flow = capital_flow.money_flow(db, runtime.stock_code)
            index_reference = _wrap_dict(technical, technical.index_context(db))
            payload = {
                "status": merge_status(market, indicators, money_flow, index_reference),
                "market": market,
                "indicators": indicators,
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
        with runtime.db_session() as db:
            basic_info = fundamental.basic_info(db, runtime.stock_code)
            industry_rank = fundamental.industry_rank(db, runtime.stock_code)
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
            valuation = _wrap_dict(fundamental, fundamental.valuation(db, runtime.stock_code))
            northbound = _wrap_dict(fundamental, fundamental.northbound_flow(db, runtime.stock_code))

            top_holders = fundamental.top_holders(db, runtime.stock_code)
            fund_holding = fundamental.fund_holding(db, runtime.stock_code)
            ownership = {
                "status": merge_status(top_holders, fund_holding),
                "top_holders": _wrap_dict(fundamental, top_holders),
                "fund_holding": _wrap_dict(fundamental, fund_holding),
            }

            flow_northbound = capital_flow.northbound(db, runtime.stock_code)
            dragon_tiger = capital_flow.dragon_tiger(db, runtime.stock_code)
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
        sentiment = runtime.readers.sentiment
        with runtime.db_session() as db:
            kline_items = technical.recent_klines(db, runtime.stock_code, days=30)
            money_flow_trend_items = capital_flow.money_flow_trend(db, runtime.stock_code)
            northbound_trend = capital_flow.northbound_trend(db, runtime.stock_code)
            insider_activity = fundamental.insider_activity(db, runtime.stock_code)
            interactive_qa_items = sentiment.recent_interactive_qa(db, runtime.stock_code)
            seo_history = fundamental.seo_history(db, runtime.stock_code)
            kline = _compact_series_payload(
                kline_items,
                columns=self.KLINE_COLUMNS,
                window_days=30,
            )
            money_flow_trend = _compact_series_payload(
                money_flow_trend_items,
                columns=self.MONEY_FLOW_TREND_COLUMNS,
            )
            payload = {
                "status": merge_status(
                    kline,
                    money_flow_trend,
                    northbound_trend,
                    interactive_qa_items,
                    seo_history,
                ),
                "kline": kline,
                "money_flow_trend": money_flow_trend,
                "northbound_trend": northbound_trend,
                "insider_activity": _wrap_dict(fundamental, insider_activity),
                "interactive_qa": _wrap_list(
                    sentiment,
                    interactive_qa_items,
                    empty_status="available",
                    include_count=True,
                ),
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
        financial = runtime.readers.financial
        fundamental = runtime.readers.fundamental
        with runtime.db_session() as db:
            financial_records = await financial.financial_records(db, runtime.stock_code)
            balance_records = await financial.balance_sheet_records(db, runtime.stock_code)
            cashflow_records = await financial.cashflow_statement_records(
                db,
                runtime.stock_code,
                format_for_context=False,
            )
            financial_ctx = {
                "financial_indicator": financial_records,
                "balance_sheet": balance_records,
                "cashflow_statement": cashflow_records,
            }

            hot_rank = sentiment.hot_rank(db, runtime.stock_code)
            hot_rank_signal = _wrap_dict(sentiment, hot_rank)

            pledge = risk.pledge(db, runtime.stock_code)
            insider = risk.insider(db, runtime.stock_code)
            shareholder = risk.shareholder(db, runtime.stock_code)
            shareholder_trend = risk.shareholder_trend(db, runtime.stock_code)
            risk_signals = {
                "status": merge_status(pledge, insider, shareholder, shareholder_trend),
                "pledge": _wrap_dict(risk, pledge),
                "insider": _wrap_list(risk, insider),
                "shareholder": _wrap_dict(risk, shareholder),
                "shareholder_trend": _wrap_dict(risk, shareholder_trend),
                "financial_warning": risk.analyze_financial_risks(financial_ctx),
            }

            dragon_tiger_effect = capital_flow.dragon_tiger_effect(db, runtime.stock_code)
            sector_flow = capital_flow.sector_flow(db, runtime.stock_code)
            block_trade = capital_flow.block_trade(db, runtime.stock_code)
            margin = capital_flow.margin(db, runtime.stock_code)
            margin_analysis = fundamental.margin_analysis(db, runtime.stock_code)
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
        with runtime.db_session() as db:
            earnings = runtime.build_earnings_countdown(db)
            lockup_items = risk.lockup(db, runtime.stock_code)
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
