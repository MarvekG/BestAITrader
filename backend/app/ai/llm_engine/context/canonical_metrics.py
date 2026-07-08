from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Mapping

from sqlalchemy import desc, select

from app.ai.llm_engine.context.calculations import to_float
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


def _build_metrics(
    *,
    close_price: float | None,
    total_share: float | None,
    total_market_value: float | None,
    pe_ttm: float | None,
    pb: float | None,
    dividend_yield: float | None,
    valuation_date: str | None,
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


def _percentile_rank(current_value: float | None, history_values: list[float]) -> float | None:
    """计算当前值在历史样本中的分位。

    Args:
        current_value: 当前指标值。
        history_values: 历史样本值。

    Returns:
        小于等于当前值的样本占比百分数；缺少样本时返回 None。
    """
    if current_value is None or not history_values:
        return None
    valid_values = [value for value in history_values if value is not None]
    if not valid_values:
        return None
    lower_count = sum(1 for value in valid_values if value < current_value)
    return round(lower_count / len(valid_values) * 100, 4)


def _build_percentile_payload(records: list[Any], valuation_date: Any) -> AIContextPayload:
    """构建估值历史分位上下文。

    Args:
        records: 最新估值记录及历史样本。
        valuation_date: 最新估值日期。

    Returns:
        PE/PB/股息率在 1/3/5 年窗口内的历史分位。
    """
    if not records or valuation_date is None:
        return {"status": "missing"}

    latest = records[0]
    windows = {
        "1y": valuation_date - timedelta(days=365),
        "3y": valuation_date - timedelta(days=365 * 3),
        "5y": valuation_date - timedelta(days=365 * 5),
    }
    earliest_window_start = min(windows.values())
    payload: AIContextPayload = {
        "status": "available",
        "data_sources": ["data.stock_valuation_history"],
        "scope": f"valuation history percentile windows from {earliest_window_start} to {valuation_date}",
        "valuation_date": str(valuation_date),
        "notes": "percentile fields rank the current value inside each lookback window.",
    }
    for suffix, start_date in windows.items():
        window_records = [record for record in records if record.data_date and record.data_date >= start_date]
        payload[f"window_start_{suffix}"] = str(start_date)
        payload[f"window_end_{suffix}"] = str(valuation_date)
        payload[f"sample_count_{suffix}"] = len(window_records)
        payload[f"basis_{suffix}"] = f"history from {start_date} to {valuation_date}"
        for field in ["pe_ttm", "pb", "dividend_yield"]:
            current = to_float(getattr(latest, field, None))
            values = [
                to_float(getattr(record, field, None))
                for record in window_records
                if to_float(getattr(record, field, None)) is not None
            ]
            payload[f"{field}_percentile_{suffix}"] = _percentile_rank(current, values)
    return format_payload_values("canonical_metrics.percentiles", payload)


def _render_table(metrics: list[CanonicalMetric], valuation_date: str | None) -> str:
    formatted_values = format_payload_values(
        "canonical_metrics",
        {metric.key: metric.value for metric in metrics},
    )
    lines = [
        "Canonical valuation metrics computed deterministically from valuation history.",
        f"Valuation date: {valuation_date or 'missing'}.",
        "",
        "| Metric | Value | Formula/source |",
        "| --- | --- | --- |",
    ]
    for metric in metrics:
        display_value = formatted_values.get(metric.key) if metric.value is not None else "missing"
        lines.append(f"| {metric.label} | {display_value} | {metric.formula} |")
    return "\n".join(lines)


async def build_canonical_metrics(db: Any, stock_code: str) -> AIContextPayload:
    """从估值历史确定性计算估值与行情派生指标。

    Args:
        db: 数据库会话。
        stock_code: 标准股票代码。

    Returns:
        含 ``table_markdown``（注入 prompt 的表格）与 ``metrics``（机器可读 key→value）的 payload。
    """
    result = await db.execute(
        select(StockValuationHistory)
        .where(StockValuationHistory.stock_code == stock_code)
        .order_by(desc(StockValuationHistory.data_date))
    )
    records = list(result.scalars().all())
    valuation = records[0] if records else None
    if valuation is None:
        return {"status": "missing"}

    valuation_date = str(valuation.data_date) if valuation is not None else None

    metrics = _build_metrics(
        close_price=to_float(valuation.close_price) if valuation else None,
        total_share=to_float(valuation.total_share) if valuation else None,
        total_market_value=to_float(valuation.total_market_value) if valuation else None,
        pe_ttm=to_float(valuation.pe_ttm) if valuation else None,
        pb=to_float(valuation.pb) if valuation else None,
        dividend_yield=to_float(valuation.dividend_yield) if valuation else None,
        valuation_date=valuation_date,
    )

    return {
        "status": "available",
        "valuation_date": valuation_date,
        "table_markdown": _render_table(metrics, valuation_date),
        "metrics": {
            metric.key: metric.as_dict()
            for metric in metrics
        },
        "percentiles": _build_percentile_payload(records, valuation.data_date if valuation else None),
    }


class CanonicalMetricsProvider:
    name = "canonical_metrics"

    async def build(
        self,
        runtime: Any,
        sections: Mapping[str, AIContextPayload],
    ) -> AIContextLayer:
        async with runtime.async_session() as db:
            payload = await build_canonical_metrics(db, runtime.stock_code)
            return AIContextLayer(self.name, payload)
