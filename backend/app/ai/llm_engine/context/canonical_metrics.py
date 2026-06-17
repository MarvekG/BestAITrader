from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import desc

from app.ai.llm_engine.context.types import AIContextLayer, AIContextPayload
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import StockValuationHistory


@dataclass(slots=True)
class CanonicalMetric:
    key: str
    label: str
    value: float | None
    formula: str

    def as_dict(self) -> dict[str, Any]:
        """转换为上下文输出字典。

        Returns:
            包含指标标识、英文标签、原始数值和英文公式说明的字典。
        """
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "formula": self.formula,
        }


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_metrics(
    *,
    close_price: float | None,
    total_share: float | None,
    total_market_value: float | None,
    pe_ttm: float | None,
    pb: float | None,
    dividend_yield: float | None,
    money_cap: float | None,
    st_borr: float | None,
    lt_borr: float | None,
    valuation_date: str | None,
    balance_date: str | None,
) -> list[CanonicalMetric]:
    metrics: list[CanonicalMetric] = []

    total_share_formula = f"stock_valuation_history.total_share as of {valuation_date or 'unknown date'}"
    if total_share is None and total_market_value and close_price:
        total_share = total_market_value / close_price
        total_share_formula = "total_market_value / close_price (derived when total_share is missing)"

    market_cap = total_market_value
    if market_cap is None and close_price is not None and total_share:
        market_cap = close_price * total_share
    metrics.append(CanonicalMetric(
        key="market_cap",
        label="Market capitalization",
        value=market_cap,
        formula=(
            "close_price * total_share"
            if close_price is not None and total_share
            else "stock_valuation_history.total_market_value"
        ),
    ))

    net_cash = None
    if money_cap is not None:
        net_cash = money_cap - (st_borr or 0.0) - (lt_borr or 0.0)
    metrics.append(CanonicalMetric(
        key="net_cash",
        label="Net cash",
        value=net_cash,
        formula=(
            "money_cap - st_borr - lt_borr"
            if money_cap is not None
            else "stock_balance_sheet.data.money_cap missing"
        ),
    ))

    per_share_net_cash = None
    if net_cash is not None and total_share:
        per_share_net_cash = net_cash / total_share
    metrics.append(CanonicalMetric(
        key="per_share_net_cash",
        label="Net cash per share",
        value=per_share_net_cash,
        formula=(
            "net_cash / total_share"
            if net_cash is not None and total_share
            else "net_cash or total_share missing"
        ),
    ))

    net_cash_to_mcap = None
    if net_cash is not None and market_cap:
        net_cash_to_mcap = net_cash / market_cap * 100
    metrics.append(CanonicalMetric(
        key="net_cash_to_mcap",
        label="Net cash to market capitalization",
        value=net_cash_to_mcap,
        formula=(
            "net_cash / market_cap * 100"
            if net_cash is not None and market_cap
            else "net_cash or market_cap missing"
        ),
    ))

    per_share_net_cash_to_price = None
    if per_share_net_cash is not None and close_price:
        per_share_net_cash_to_price = per_share_net_cash / close_price * 100
    metrics.append(CanonicalMetric(
        key="net_cash_to_price",
        label="Net cash per share to price",
        value=per_share_net_cash_to_price,
        formula=(
            "per_share_net_cash / close_price * 100"
            if per_share_net_cash is not None and close_price
            else "per_share_net_cash or close_price missing"
        ),
    ))

    metrics.append(CanonicalMetric(
        key="pe_ttm",
        label="Price-to-earnings ratio TTM",
        value=pe_ttm,
        formula=f"stock_valuation_history.pe_ttm as of {valuation_date or 'unknown date'}",
    ))
    metrics.append(CanonicalMetric(
        key="pb",
        label="Price-to-book ratio",
        value=pb,
        formula=f"stock_valuation_history.pb as of {valuation_date or 'unknown date'}",
    ))
    metrics.append(CanonicalMetric(
        key="dividend_yield",
        label="Dividend yield",
        value=dividend_yield,
        formula=f"stock_valuation_history.dividend_yield as of {valuation_date or 'unknown date'}",
    ))
    metrics.append(CanonicalMetric(
        key="total_share",
        label="Total shares",
        value=total_share,
        formula=total_share_formula,
    ))

    return metrics


def _render_table(metrics: list[CanonicalMetric], valuation_date: str | None, balance_date: str | None) -> str:
    formatted_values = format_payload_values(
        "canonical_metrics",
        {metric.key: metric.value for metric in metrics},
    )
    lines = [
        "Canonical derived metrics computed deterministically from raw financial fields.",
        f"Valuation date: {valuation_date or 'missing'}; balance-sheet report date: {balance_date or 'missing'}.",
        "",
        "| Metric | Value | Formula/source |",
        "| --- | --- | --- |",
    ]
    for metric in metrics:
        display_value = formatted_values.get(metric.key) if metric.value is not None else "missing"
        lines.append(f"| {metric.label} | {display_value} | {metric.formula} |")
    return "\n".join(lines)


def build_canonical_metrics(db: Any, stock_code: str) -> AIContextPayload:
    """从原始财务字段确定性计算高频派生指标。

    Args:
        db: 数据库会话。
        stock_code: 标准股票代码。

    Returns:
        含 ``table_markdown``（注入 prompt 的表格）与 ``metrics``（机器可读 key→value）的 payload。
    """
    valuation = db.query(StockValuationHistory).filter(
        StockValuationHistory.stock_code == stock_code,
    ).order_by(desc(StockValuationHistory.data_date)).first()
    if valuation is None:
        return {"status": "missing"}

    balance_data: Mapping[str, Any] = {}
    valuation_date = str(valuation.data_date) if valuation is not None else None
    balance_date = None

    metrics = _build_metrics(
        close_price=_to_float(valuation.close_price) if valuation else None,
        total_share=_to_float(valuation.total_share) if valuation else None,
        total_market_value=_to_float(valuation.total_market_value) if valuation else None,
        pe_ttm=_to_float(valuation.pe_ttm) if valuation else None,
        pb=_to_float(valuation.pb) if valuation else None,
        dividend_yield=_to_float(valuation.dividend_yield) if valuation else None,
        money_cap=_to_float(balance_data.get("money_cap")),
        st_borr=_to_float(balance_data.get("st_borr")),
        lt_borr=_to_float(balance_data.get("lt_borr")),
        valuation_date=valuation_date,
        balance_date=balance_date,
    )

    return {
        "status": "available",
        "valuation_date": valuation_date,
        "balance_report_date": balance_date,
        "table_markdown": _render_table(metrics, valuation_date, balance_date),
        "metrics": {
            metric.key: metric.as_dict()
            for metric in metrics
        },
    }


class CanonicalMetricsProvider:
    name = "canonical_metrics"

    async def build(
        self,
        runtime: Any,
        sections: Mapping[str, AIContextPayload],
    ) -> AIContextLayer:
        with runtime.db_session() as db:
            payload = build_canonical_metrics(db, runtime.stock_code)
            return AIContextLayer(self.name, payload)
