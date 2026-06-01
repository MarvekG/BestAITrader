from typing import Dict, Any, Optional
from sqlalchemy import desc
from sqlalchemy.orm import Session
from app.ai.llm_engine.context.section_wrappers import status_payload, wrap_snapshot_section
from app.models.data_storage import FinancialIndicator, StockIncomeStatement, StockBalanceSheet, StockCashflowStatement
from app.core.utils.formatters import StockCodeStandardizer
from app.data.metadata.financial_report_localizer import (
    drop_nulls,
    localize_financial_report_payload,
)

class FinancialSource:
    """Builds latest-snapshot financial context for AI analysis."""

    def _wrap_snapshot_section(self, payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return wrap_snapshot_section(payload)

    @staticmethod
    def status_payload(data_status: str, **kwargs: Any) -> Dict[str, Any]:
        return status_payload(data_status, **kwargs)

    @staticmethod
    def _drop_nulls(value: Any) -> Any:
        return drop_nulls(value)

    @staticmethod
    def _localize_raw_data(raw_data: Optional[Dict[str, Any]], table: str) -> Optional[Dict[str, Any]]:
        return localize_financial_report_payload(raw_data, table)

    def _serialize_income_statement(self, income_record: Any) -> Dict[str, Any]:
        if not income_record or not income_record.data:
            return {}

        data = self._localize_raw_data(income_record.data.copy(), "data.stock_income_statement") or {}
        result = {
            "data": data,
            "meta": {
                "report_date": str(income_record.report_date),
                "announcement_date": str(income_record.announcement_date) if income_record.announcement_date else None,
                "report_type": income_record.report_type,
                "currency": income_record.currency,
                "is_audit": income_record.is_audit,
                "data_source": income_record.data_source,
            }
        }
        return self._drop_nulls(result)

    def _serialize_balance_sheet(self, balance_record: Any) -> Dict[str, Any]:
        if not balance_record or not balance_record.data:
            return {}

        data = self._localize_raw_data(balance_record.data.copy(), "data.stock_balance_sheet") or {}
        result = {
            "data": data,
            "meta": {
                "report_date": str(balance_record.report_date),
                "announcement_date": str(balance_record.announcement_date) if balance_record.announcement_date else None,
                "report_type": balance_record.report_type,
                "currency": balance_record.currency,
                "is_audit": balance_record.is_audit,
                "data_source": balance_record.data_source,
            }
        }
        return self._drop_nulls(result)

    def _serialize_cashflow_statement(self, cashflow_record: Any) -> Dict[str, Any]:
        if not cashflow_record or not cashflow_record.data:
            return {}

        data = self._localize_raw_data(cashflow_record.data.copy(), "data.stock_cashflow_statement") or {}
        result = {
            "data": data,
            "meta": {
                "report_date": str(cashflow_record.report_date),
                "announcement_date": str(cashflow_record.announcement_date) if cashflow_record.announcement_date else None,
                "report_type": cashflow_record.report_type,
                "currency": cashflow_record.currency,
                "is_audit": cashflow_record.is_audit,
                "data_source": cashflow_record.data_source,
            }
        }
        return self._drop_nulls(result)

    def _get_latest_financials(self, db: Session, stock_code: str) -> Dict[str, Any]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        
        # Latest financial-indicator snapshot only.
        fin_record = db.query(FinancialIndicator).filter(
            FinancialIndicator.stock_code == formatted_code
        ).order_by(
            FinancialIndicator.report_date.desc(),
            FinancialIndicator.announcement_date.desc()
        ).first()

        if not fin_record or not fin_record.data:
            return {}
            
        data = self._drop_nulls(fin_record.data.copy())
        result = {
            "data": data,
            "meta": {
                "report_date": str(fin_record.report_date),
                "announcement_date": str(fin_record.announcement_date) if fin_record.announcement_date else None
            }
        }

        return self._drop_nulls(result)

    def _get_historical_summary(self, db: Session, stock_code: str, limit: int = 8) -> list[Dict[str, Any]]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        rows = db.query(FinancialIndicator).filter(
            FinancialIndicator.stock_code == formatted_code
        ).order_by(
            FinancialIndicator.report_date.desc(),
            FinancialIndicator.announcement_date.desc(),
        ).limit(limit).all()

        history: list[Dict[str, Any]] = []
        for row in rows:
            if not row or not row.data:
                continue
            history.append(self._drop_nulls({
                "data": self._drop_nulls(row.data.copy()),
                "meta": {
                    "report_date": str(row.report_date),
                    "announcement_date": str(row.announcement_date) if row.announcement_date else None,
                    "data_source": getattr(row, "data_source", None),
                },
            }))
        return history

    def _get_latest_income_statement(self, db: Session, stock_code: str) -> Dict[str, Any]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        income_record = db.query(StockIncomeStatement).filter(
            StockIncomeStatement.stock_code == formatted_code
        ).order_by(
            StockIncomeStatement.report_date.desc(),
            StockIncomeStatement.announcement_date.desc(),
            StockIncomeStatement.updated_at.desc()
        ).first()

        if not income_record or not income_record.data:
            return {}

        return self._serialize_income_statement(income_record)

    def _get_income_statement_summary(self, db: Session, stock_code: str, limit: int = 8) -> list[Dict[str, Any]]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        rows = db.query(StockIncomeStatement).filter(
            StockIncomeStatement.stock_code == formatted_code
        ).order_by(
            StockIncomeStatement.report_date.desc(),
            StockIncomeStatement.announcement_date.desc(),
            StockIncomeStatement.updated_at.desc()
        ).limit(limit).all()

        return [
            payload
            for row in rows
            if (payload := self._serialize_income_statement(row))
        ]

    def _get_latest_balance_sheet(self, db: Session, stock_code: str) -> Dict[str, Any]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        balance_record = db.query(StockBalanceSheet).filter(
            StockBalanceSheet.stock_code == formatted_code
        ).order_by(
            StockBalanceSheet.report_date.desc(),
            StockBalanceSheet.announcement_date.desc(),
            StockBalanceSheet.updated_at.desc()
        ).first()

        if not balance_record or not balance_record.data:
            return {}

        return self._serialize_balance_sheet(balance_record)

    def _get_balance_sheet_history(self, db: Session, stock_code: str, limit: int = 8) -> list[Dict[str, Any]]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        rows = db.query(StockBalanceSheet).filter(
            StockBalanceSheet.stock_code == formatted_code
        ).order_by(
            StockBalanceSheet.report_date.desc(),
            StockBalanceSheet.announcement_date.desc(),
            StockBalanceSheet.updated_at.desc()
        ).limit(limit).all()

        history: list[Dict[str, Any]] = []
        for row in rows:
            if not row or not row.data:
                continue
            history.append(self._serialize_balance_sheet(row))
        return history

    def _get_latest_cashflow_statement(self, db: Session, stock_code: str) -> Dict[str, Any]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        cashflow_record = db.query(StockCashflowStatement).filter(
            StockCashflowStatement.stock_code == formatted_code
        ).order_by(
            StockCashflowStatement.report_date.desc(),
            StockCashflowStatement.announcement_date.desc(),
            StockCashflowStatement.updated_at.desc()
        ).first()

        if not cashflow_record or not cashflow_record.data:
            return {}

        return self._serialize_cashflow_statement(cashflow_record)

    def _get_cashflow_statement_history(self, db: Session, stock_code: str, limit: int = 8) -> list[Dict[str, Any]]:
        formatted_code = StockCodeStandardizer.standardize(stock_code)
        rows = db.query(StockCashflowStatement).filter(
            StockCashflowStatement.stock_code == formatted_code
        ).order_by(
            StockCashflowStatement.report_date.desc(),
            StockCashflowStatement.announcement_date.desc(),
            StockCashflowStatement.updated_at.desc()
        ).limit(limit).all()

        history: list[Dict[str, Any]] = []
        for row in rows:
            if not row or not row.data:
                continue
            history.append(self._serialize_cashflow_statement(row))
        return history
