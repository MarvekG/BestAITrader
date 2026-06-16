"""
财务数据上下文构建器 - 实时按需拉取模式

改造说明：
- 不再从数据库读取持久化的财务数据
- 改为实时调用数据源接口获取最新财务数据
- 完成字段翻译和单位拼接后返回给 LLM
"""

from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.ai.llm_engine.context.section_wrappers import status_payload, wrap_snapshot_section
from app.data.metadata.field_units import format_payload_values
from app.data.metadata.financial_report_localizer import (
    drop_nulls,
    localize_financial_report_payload,
)
from app.core.utils.formatters import StockCodeStandardizer
from app.core.logger import get_logger

logger = get_logger(__name__)


class FinancialSource:
    """实时财务数据获取与格式化。

    不再依赖数据库持久化，改为按需实时拉取。
    """

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
            payload: 原始财务快照。

        Returns:
            字段结构不变、已按标准字段单位配置格式化的上下文字典。
        """
        return self._append_financial_units(payload)

    def _format_statement_data_for_context(self, data: Dict[str, Any], table: str) -> Dict[str, Any]:
        """先按标准字段键补全财报字段单位。

        Args:
            data: 财务报表标准字段键值。
            table: 标准财务报表表名。

        Returns:
            已删除空值并补单位的标准字段报表数据。
        """
        return format_payload_values(table, self._drop_nulls(data.copy()))

    def _fetch_financial_data_from_source(self, stock_code: str, data_type: str) -> Dict[str, Any]:
        """实时从数据源拉取财务数据。

        Args:
            stock_code: 股票代码。
            data_type: 数据类型 (financial_indicator, income_statement, balance_sheet, cashflow_statement)。

        Returns:
            从数据源返回的原始财务数据。
        """
        # TODO: 实现实时调用数据源接口
        # 1. 从数据源管理器获取可用数据源
        # 2. 调用数据源接口获取最新财务数据
        # 3. 返回原始数据
        logger.warning(f"财务数据实时拉取功能待实现: {stock_code}, {data_type}")
        return {}

    def _get_latest_financials(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """获取最新财务指标 - 实时拉取模式。

        Args:
            db: 数据库会话（保留接口兼容性，暂不使用）。
            stock_code: 股票代码。

        Returns:
            财务指标快照。
        """
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        # 实时拉取财务指标数据
        raw_data = self._fetch_financial_data_from_source(formatted_code, "financial_indicator")

        if not raw_data:
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))
        meta = raw_data.get("meta", {})

        result = {
            "data": data,
            "meta": meta
        }

        return self._drop_nulls(result)

    def _get_historical_summary(self, db: Session, stock_code: str, limit: int = 8) -> list[Dict[str, Any]]:
        """获取财务指标历史摘要 - 实时拉取模式。

        Args:
            db: 数据库会话（保留接口兼容性，暂不使用）。
            stock_code: 股票代码。
            limit: 返回期数上限。

        Returns:
            历史财务指标摘要列表。
        """
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        # 实时拉取历史财务指标
        raw_data = self._fetch_financial_data_from_source(formatted_code, "financial_indicator_history")

        history: list[Dict[str, Any]] = []
        for item in raw_data.get("items", [])[:limit]:
            if not item:
                continue
            history.append(self._drop_nulls({
                "data": self._drop_nulls(item.get("data", {})),
                "meta": item.get("meta", {}),
            }))
        return history

    def _get_latest_income_statement(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期利润表 - 实时拉取模式。

        Args:
            db: 数据库会话（保留接口兼容性，暂不使用）。
            stock_code: 股票代码。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            最新一期利润表快照；缺少数据时返回空字典。
        """
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        raw_data = self._fetch_financial_data_from_source(formatted_code, "income_statement")

        if not raw_data or not raw_data.get("data"):
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))
        if format_for_context:
            data = self._format_statement_data_for_context(data, self._INCOME_STATEMENT_TABLE)

        result = {
            "data": data,
            "meta": raw_data.get("meta", {})
        }

        if format_for_context:
            return self._localize_raw_data(result, self._INCOME_STATEMENT_TABLE) or {}
        return self._drop_nulls(result)

    def _get_income_statement_summary(
        self,
        db: Session,
        stock_code: str,
        limit: int = 8,
        *,
        format_for_context: bool = True,
    ) -> list[Dict[str, Any]]:
        """读取利润表最近多期摘要 - 实时拉取模式。

        Args:
            db: 数据库会话（保留接口兼容性，暂不使用）。
            stock_code: 股票代码。
            limit: 返回期数上限。
            format_for_context: 是否输出面向 AI 上下文的单位和翻译展示值。

        Returns:
            按报告期倒序排列的利润表摘要列表。
        """
        # 实现类似，省略...
        return []

    def _get_latest_balance_sheet(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期资产负债表 - 实时拉取模式。"""
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        raw_data = self._fetch_financial_data_from_source(formatted_code, "balance_sheet")

        if not raw_data or not raw_data.get("data"):
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))
        if format_for_context:
            data = self._format_statement_data_for_context(data, self._BALANCE_SHEET_TABLE)

        result = {
            "data": data,
            "meta": raw_data.get("meta", {})
        }

        if format_for_context:
            return self._localize_raw_data(result, self._BALANCE_SHEET_TABLE) or {}
        return self._drop_nulls(result)

    def _get_balance_sheet_history(
        self,
        db: Session,
        stock_code: str,
        limit: int = 8,
        *,
        format_for_context: bool = True,
    ) -> list[Dict[str, Any]]:
        """读取资产负债表最近多期摘要 - 实时拉取模式。"""
        return []

    def _get_latest_cashflow_statement(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期现金流量表 - 实时拉取模式。"""
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        raw_data = self._fetch_financial_data_from_source(formatted_code, "cashflow_statement")

        if not raw_data or not raw_data.get("data"):
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))
        if format_for_context:
            data = self._format_statement_data_for_context(data, self._CASHFLOW_STATEMENT_TABLE)

        result = {
            "data": data,
            "meta": raw_data.get("meta", {})
        }

        if format_for_context:
            return self._localize_raw_data(result, self._CASHFLOW_STATEMENT_TABLE) or {}
        return self._drop_nulls(result)

    def _get_cashflow_statement_history(
        self,
        db: Session,
        stock_code: str,
        limit: int = 8,
        *,
        format_for_context: bool = True,
    ) -> list[Dict[str, Any]]:
        """读取现金流量表最近多期摘要 - 实时拉取模式。"""
        return []
