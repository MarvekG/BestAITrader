from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.ai.llm_engine.context.section_wrappers import status_payload, wrap_snapshot_section
from app.data.metadata.field_units import format_payload_values
from app.models.data_storage import FinancialIndicator, StockIncomeStatement, StockBalanceSheet, StockCashflowStatement
from app.core.utils.formatters import StockCodeStandardizer
from app.data.metadata.financial_report_localizer import (
    drop_nulls,
    localize_financial_report_payload,
)

class FinancialSource:
    """Builds latest-snapshot financial context for AI analysis."""

    _INCOME_STATEMENT_TABLE = "data.stock_income_statement"
    _BALANCE_SHEET_TABLE = "data.stock_balance_sheet"
    _CASHFLOW_STATEMENT_TABLE = "data.stock_cashflow_statement"

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

    def _append_financial_units(self, value: Any) -> Any:
        """按标准字段单位配置格式化财务快照数据。

        Args:
            value: 财务快照原始字典或嵌套值。

        Returns:
            已按 ``table_field_units.json`` 补单位的展示数据；未配置单位的字段保持原样。
        """
        return format_payload_values("data.financial_indicator", value)

    def _format_latest_financials_for_context(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """格式化最新财务指标快照给 LLM 展示。

        Args:
            payload: ``_get_latest_financials`` 返回的原始财务快照。

        Returns:
            字段结构不变、已按标准字段单位配置格式化的上下文字典。
        """
        return self._append_financial_units(payload)

    def _format_statement_data_for_context(self, data: Dict[str, Any], table: str) -> Dict[str, Any]:
        """先按标准字段键补全财报字段单位。

        Args:
            data: 财务报表 ``data`` 字段中的标准字段键值。
            table: 标准财务报表表名。

        Returns:
            已删除空值并补单位的标准字段报表数据。
        """
        return format_payload_values(table, self._drop_nulls(data.copy()))

    def _serialize_statement(
        self,
        statement_record: Any,
        table: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """序列化单期财务报表记录。

        Args:
            statement_record: SQLAlchemy 财务报表记录。
            table: 标准财务报表表名。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            包含 ``data`` 和 ``meta`` 的报表快照；缺少数据时返回空字典。
        """
        if not statement_record or not statement_record.data:
            return {}

        raw_data = self._drop_nulls(statement_record.data.copy())
        data = (
            self._format_statement_data_for_context(raw_data, table)
            if format_for_context
            else raw_data
        )
        result = {
            "data": data,
            "meta": {
                "report_date": str(statement_record.report_date),
                "announcement_date": str(statement_record.announcement_date) if statement_record.announcement_date else None,
                "report_type": statement_record.report_type,
                "currency": statement_record.currency,
                "is_audit": statement_record.is_audit,
                "data_source": statement_record.data_source,
            }
        }
        if format_for_context:
            return self._localize_raw_data(result, table) or {}
        return self._drop_nulls(result)

    def _serialize_income_statement(
        self,
        income_record: Any,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """序列化利润表记录。

        Args:
            income_record: 利润表 SQLAlchemy 记录。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            利润表快照；缺少数据时返回空字典。
        """
        return self._serialize_statement(
            income_record,
            self._INCOME_STATEMENT_TABLE,
            format_for_context=format_for_context,
        )

    def _serialize_balance_sheet(
        self,
        balance_record: Any,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """序列化资产负债表记录。

        Args:
            balance_record: 资产负债表 SQLAlchemy 记录。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            资产负债表快照；缺少数据时返回空字典。
        """
        return self._serialize_statement(
            balance_record,
            self._BALANCE_SHEET_TABLE,
            format_for_context=format_for_context,
        )

    def _serialize_cashflow_statement(
        self,
        cashflow_record: Any,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """序列化现金流量表记录。

        Args:
            cashflow_record: 现金流量表 SQLAlchemy 记录。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            现金流量表快照；缺少数据时返回空字典。
        """
        return self._serialize_statement(
            cashflow_record,
            self._CASHFLOW_STATEMENT_TABLE,
            format_for_context=format_for_context,
        )

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

    def _get_latest_income_statement(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期利润表。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            最新一期利润表快照；缺少数据时返回空字典。
        """
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

        return self._serialize_income_statement(income_record, format_for_context=format_for_context)

    def _get_income_statement_summary(
        self,
        db: Session,
        stock_code: str,
        limit: int = 8,
        *,
        format_for_context: bool = True,
    ) -> list[Dict[str, Any]]:
        """读取利润表最近多期摘要。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            limit: 返回期数上限。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            按报告期倒序排列的利润表摘要列表。
        """
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
            if (payload := self._serialize_income_statement(row, format_for_context=format_for_context))
        ]

    def _get_latest_balance_sheet(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期资产负债表。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            最新一期资产负债表快照；缺少数据时返回空字典。
        """
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

        return self._serialize_balance_sheet(balance_record, format_for_context=format_for_context)

    def _get_balance_sheet_history(
        self,
        db: Session,
        stock_code: str,
        limit: int = 8,
        *,
        format_for_context: bool = True,
    ) -> list[Dict[str, Any]]:
        """读取资产负债表最近多期摘要。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            limit: 返回期数上限。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            按报告期倒序排列的资产负债表摘要列表。
        """
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
            history.append(self._serialize_balance_sheet(row, format_for_context=format_for_context))
        return history

    def _get_latest_cashflow_statement(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期现金流量表。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            最新一期现金流量表快照；缺少数据时返回空字典。
        """
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

        return self._serialize_cashflow_statement(cashflow_record, format_for_context=format_for_context)

    def _get_cashflow_statement_history(
        self,
        db: Session,
        stock_code: str,
        limit: int = 8,
        *,
        format_for_context: bool = True,
    ) -> list[Dict[str, Any]]:
        """读取现金流量表最近多期摘要。

        Args:
            db: 数据库会话。
            stock_code: 股票代码。
            limit: 返回期数上限。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            按报告期倒序排列的现金流量表摘要列表。
        """
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
            history.append(self._serialize_cashflow_statement(row, format_for_context=format_for_context))
        return history
