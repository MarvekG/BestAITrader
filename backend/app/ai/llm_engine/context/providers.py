from __future__ import annotations

from typing import Any, Mapping

from app.ai.llm_engine.context.portfolio import build_portfolio_risk_control_context
from app.ai.llm_engine.context.runtime import merge_status
from app.ai.llm_engine.context.types import AIContextLayer, AIContextPayload
from app.crud.account import ensure_user_account
from app.models.user import User
from app.performance.service import get_latest_performance_summary
from app.portfolio.service import get_portfolio_overview


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

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        with runtime.db_session() as db:
            stock = runtime.get_stock_basic(db)
            payload = {
                "status": "available" if stock else "missing",
                "generated_at": runtime.generated_at.isoformat(),
                "stock_code": runtime.stock_code,
                "stock_name": runtime.stock_name(db),
                "company": {
                    "industry": stock.industry if stock else None,
                    "area": stock.area if stock else None,
                    "list_date": str(stock.list_date) if stock and stock.list_date else None,
                    "total_share": stock.total_share if stock else None,
                    "float_share": stock.float_share if stock else None,
                },
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
                "overview": get_portfolio_overview(db, user=user),
                "performance": get_latest_performance_summary(db, user_id=user_id),
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

            latest_financials = financial.latest_financials(db, runtime.stock_code)
            latest_financials_for_context = financial.source._format_latest_financials_for_context(latest_financials)
            financial_statements = {
                "status": merge_status(latest_financials),
                "financial_indicator_latest": _wrap_snapshot(
                    financial,
                    financial.localize_raw_data(latest_financials_for_context, "data.financial_indicator")
                ),
            }
            valuation = _wrap_dict(fundamental, fundamental.valuation(db, runtime.stock_code))
            forecast = _wrap_dict(fundamental, fundamental.forecast(db, runtime.stock_code))
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
                    forecast,
                    northbound,
                    ownership,
                    flow_snapshot,
                ),
                "company": company,
                "financial_statements": financial_statements,
                "valuation": valuation,
                "forecast": forecast,
                "northbound": northbound,
                "ownership": ownership,
                "flow": flow_snapshot,
            }
            return AIContextLayer(self.name, payload)


class HistoryProvider:
    name = "history"

    async def build(self, runtime: Any, sections: Mapping[str, AIContextPayload]) -> AIContextLayer:
        technical = runtime.readers.technical
        capital_flow = runtime.readers.capital_flow
        fundamental = runtime.readers.fundamental
        sentiment = runtime.readers.sentiment
        with runtime.db_session() as db:
            kline_items = technical.recent_klines(db, runtime.stock_code, days=30)
            money_flow_trend_items = capital_flow.money_flow_trend(db, runtime.stock_code)
            northbound_trend = capital_flow.northbound_trend(db, runtime.stock_code)
            financial_trend = fundamental.financial_trend(db, runtime.stock_code)
            insider_activity = fundamental.insider_activity(db, runtime.stock_code)
            interactive_qa_items = sentiment.recent_interactive_qa(db, runtime.stock_code)
            seo_history = fundamental.seo_history(db, runtime.stock_code)
            kline = _items_payload(kline_items, window_days=30)
            payload = {
                "status": merge_status(
                    kline,
                    money_flow_trend_items,
                    northbound_trend,
                    financial_trend,
                    interactive_qa_items,
                    seo_history,
                ),
                "kline": kline,
                "money_flow_trend": _items_payload(money_flow_trend_items),
                "northbound_trend": northbound_trend,
                "financial_trend": _wrap_dict(fundamental, financial_trend),
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
        sentiment = runtime.readers.sentiment
        risk = runtime.readers.risk
        capital_flow = runtime.readers.capital_flow
        financial = runtime.readers.financial
        fundamental = runtime.readers.fundamental
        with runtime.db_session() as db:
            latest_financials = financial.latest_financials(db, runtime.stock_code)
            latest_balance = financial.latest_balance_sheet(db, runtime.stock_code)
            latest_cashflow = financial.latest_cashflow_statement(db, runtime.stock_code)
            financial_ctx = {
                "financial_indicator_latest": _wrap_snapshot(
                    financial,
                    financial.localize_raw_data(latest_financials, "data.financial_indicator")
                ),
                "balance_sheet_latest": _wrap_snapshot(financial, latest_balance),
                "cashflow_statement_latest": _wrap_snapshot(financial, latest_cashflow),
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
)
