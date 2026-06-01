from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.ai.llm_engine.context.capital_flow import CapitalFlowSource
from app.ai.llm_engine.context.financial import FinancialSource
from app.ai.llm_engine.context.fundamental import FundamentalSource
from app.ai.llm_engine.context.risk import RiskSource
from app.ai.llm_engine.context.section_wrappers import (
    wrap_dict_section,
    wrap_list_section,
    wrap_snapshot_section,
)
from app.ai.llm_engine.context.sentiment import SentimentSource
from app.ai.llm_engine.context.technical import TechnicalSource


class _WrapDictMixin:
    source: Any

    def wrap_dict(self, payload: Any) -> dict[str, Any]:
        return wrap_dict_section(payload)


class _WrapListMixin:
    source: Any

    def wrap_list(
        self,
        payload: Any,
        *,
        empty_status: str = "missing",
        include_count: bool = False,
    ) -> dict[str, Any]:
        return wrap_list_section(
            payload,
            empty_status=empty_status,
            include_count=include_count,
        )


class _WrapSnapshotMixin:
    source: Any

    def wrap_snapshot(self, payload: Any) -> dict[str, Any]:
        return wrap_snapshot_section(payload)


@dataclass(slots=True)
class FundamentalReader(_WrapDictMixin):
    source: FundamentalSource = field(default_factory=FundamentalSource)

    def basic_info(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_basic_info(db, stock_code)

    def financials(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_financials(db, stock_code)

    def industry_rank(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_industry_rank(db, stock_code)

    def valuation(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_valuation(db, stock_code)

    def forecast(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_forecast(db, stock_code)

    def northbound_flow(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_northbound_flow(db, stock_code)

    def top_holders(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_top_holders(db, stock_code)

    def normalize_holder_change_label(self, change_value: Any) -> str:
        return self.source._normalize_holder_change_label(change_value)

    def fund_holding(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_fund_holding(db, stock_code)

    def financial_trend(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_financial_trend(db, stock_code)

    def insider_activity(self, db: Session, stock_code: str, *, months: int = 6) -> dict[str, Any]:
        return self.source._get_insider_activity(db, stock_code, months=months)

    def seo_history(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_seo_history(db, stock_code)

    def lockup_release(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_lockup_release(db, stock_code)

    def margin_analysis(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_margin_analysis(db, stock_code)

    def dragon_tiger_activity(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_market_wide_dragon_tiger_activity(db, stock_code)


@dataclass(slots=True)
class TechnicalReader(_WrapDictMixin):
    source: TechnicalSource = field(default_factory=TechnicalSource)

    def realtime_market(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_realtime_market(db, stock_code)

    def latest_indicators(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_latest_indicators(db, stock_code)

    def index_context(self, db: Session) -> dict[str, Any]:
        return self.source._get_index_context(db)

    def recent_klines(self, db: Session, stock_code: str, *, days: int) -> list[dict[str, Any]]:
        return self.source._get_recent_klines(db, stock_code, days=days)


@dataclass(slots=True)
class CapitalFlowReader:
    source: CapitalFlowSource = field(default_factory=CapitalFlowSource)

    def money_flow(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_money_flow(db, stock_code)

    def shareholder(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_shareholder(db, stock_code)

    def northbound(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_northbound(db, stock_code)

    def dragon_tiger(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_dragon_tiger(db, stock_code)

    def margin(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_margin(db, stock_code)

    def money_flow_trend(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_money_flow_trend(db, stock_code)

    def northbound_trend(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_northbound_trend(db, stock_code)

    def dragon_tiger_effect(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._analyze_dragon_tiger_effect(db, stock_code)

    def sector_flow(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_sector_flow(db, stock_code)

    def block_trade(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_block_trade(db, stock_code)


@dataclass(slots=True)
class SentimentReader(_WrapDictMixin, _WrapListMixin):
    source: SentimentSource = field(default_factory=SentimentSource)

    def hot_rank(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_hot_rank(db, stock_code)

    def recent_interactive_qa(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_recent_interactive_qa(db, stock_code)


@dataclass(slots=True)
class RiskReader(_WrapDictMixin, _WrapListMixin):
    source: RiskSource = field(default_factory=RiskSource)

    def pledge(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_pledge(db, stock_code)

    def insider(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_insider(db, stock_code)

    def lockup(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_lockup(db, stock_code)

    def shareholder(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_shareholder(db, stock_code)

    def shareholder_trend(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_shareholder_trend(db, stock_code)

    def analyze_financial_risks(self, fin_ctx: dict[str, Any]) -> dict[str, Any]:
        return self.source._analyze_financial_risks(fin_ctx)


@dataclass(slots=True)
class FinancialReader(_WrapSnapshotMixin):
    source: FinancialSource = field(default_factory=FinancialSource)

    def localize_raw_data(self, raw_data: dict[str, Any] | None, table: str) -> dict[str, Any] | None:
        return self.source._localize_raw_data(raw_data, table)

    def latest_financials(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_latest_financials(db, stock_code)

    def historical_summary(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_historical_summary(db, stock_code)

    def latest_income_statement(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_latest_income_statement(db, stock_code)

    def income_statement_summary(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_income_statement_summary(db, stock_code)

    def latest_balance_sheet(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_latest_balance_sheet(db, stock_code)

    def balance_sheet_history(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_balance_sheet_history(db, stock_code)

    def latest_cashflow_statement(self, db: Session, stock_code: str) -> dict[str, Any]:
        return self.source._get_latest_cashflow_statement(db, stock_code)

    def cashflow_statement_history(self, db: Session, stock_code: str) -> list[dict[str, Any]]:
        return self.source._get_cashflow_statement_history(db, stock_code)


@dataclass(slots=True)
class ContextReaders:
    fundamental: FundamentalReader = field(default_factory=FundamentalReader)
    technical: TechnicalReader = field(default_factory=TechnicalReader)
    capital_flow: CapitalFlowReader = field(default_factory=CapitalFlowReader)
    sentiment: SentimentReader = field(default_factory=SentimentReader)
    risk: RiskReader = field(default_factory=RiskReader)
    financial: FinancialReader = field(default_factory=FinancialReader)
