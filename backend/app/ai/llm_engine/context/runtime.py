from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import select

from app.core import database as database_module
from app.core.request_context import get_current_user_id
from app.ai.llm_engine.context.readers import ContextReaders
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import FinancialCalendar, StockBasic
from app.core.utils.formatters import StockCodeStandardizer


def extract_status(payload: Any) -> str:
    if isinstance(payload, dict):
        status = payload.get("status") or payload.get("data_status")
        if status:
            return str(status)
        if not payload:
            return "missing"
        return "available"
    if isinstance(payload, list):
        return "available" if payload else "missing"
    return "available" if payload is not None else "missing"


def merge_status(*payloads: Any) -> str:
    statuses = [extract_status(payload) for payload in payloads if payload is not None]
    if not statuses:
        return "missing"
    if any(status == "error" for status in statuses):
        return "error"
    if any(status == "available" for status in statuses):
        if any(status in {"missing", "stale", "partial"} for status in statuses):
            return "partial"
        return "available"
    if any(status == "stale" for status in statuses):
        return "stale"
    return "missing"


class AIContextRuntime:
    def __init__(self, stock_code: str):
        """初始化单次 AI 上下文构建运行时。

        Args:
            stock_code: 待构建上下文的股票代码。
        """
        self.stock_code = StockCodeStandardizer.standardize(stock_code)
        self.generated_at = datetime.now()
        self.user_id = get_current_user_id()
        self.errors: list[Dict[str, str]] = []
        self._stock_basic: Optional[StockBasic] = None
        self.readers = ContextReaders()

    async def get_stock_basic(self, db: Any) -> Optional[StockBasic]:
        if self._stock_basic is None:
            result = await db.execute(select(StockBasic).where(StockBasic.stock_code == self.stock_code))
            self._stock_basic = result.scalars().first()
        return self._stock_basic

    async def stock_name(self, db: Any) -> str:
        stock = await self.get_stock_basic(db)
        return stock.name if stock and stock.name else "Unknown"

    async def build_earnings_countdown(self, db: Any) -> Dict[str, Any]:
        result = await db.execute(
            select(FinancialCalendar)
            .where(
                FinancialCalendar.stock_code == self.stock_code,
                FinancialCalendar.actual_date >= self.generated_at.date(),
            )
            .order_by(FinancialCalendar.actual_date)
        )
        next_event = result.scalars().first()

        if not next_event:
            return {"status": "missing"}

        days_left = (next_event.actual_date - self.generated_at.date()).days
        payload = {
            "status": "available",
            "next_disclosure_date": str(next_event.actual_date),
            "report_period": next_event.report_period,
            "days_countdown": max(0, days_left),
        }
        return format_payload_values("events.earnings_countdown", payload)

    def record_error(self, provider: str, exc: Exception) -> None:
        self.errors.append({"provider": provider, "message": str(exc)})

    def async_session(self) -> Any:
        return database_module.AsyncSessionLocal()
