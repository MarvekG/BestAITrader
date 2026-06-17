"""
财务数据上下文构建器 - 实时按需拉取模式

改造说明：
- 不再从数据库读取持久化的财务数据
- 改为实时调用数据源接口获取最新财务数据
- 完成字段翻译和单位拼接后返回给 LLM
"""

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Optional
from sqlalchemy.orm import Session
from app.ai.llm_engine.context.section_wrappers import status_payload, wrap_snapshot_section
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

    @staticmethod
    def _default_date_range() -> tuple[str, str]:
        """生成财务上下文实时拉取使用的默认日期范围。

        Returns:
            最近两年的起止日期，格式为 ``YYYYMMDD``。
        """
        today = datetime.now().date()
        start_date = today - timedelta(days=730)
        return start_date.strftime("%Y%m%d"), today.strftime("%Y%m%d")

    @staticmethod
    def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
        """将数据源返回的标准化记录整理为上下文快照。

        Args:
            record: 数据源返回的一条标准化财务记录。

        Returns:
            包含 ``data`` 和 ``meta`` 的上下文快照。
        """
        data = record.get("data") or {}
        meta = {
            "stock_code": record.get("stock_code"),
            "report_date": str(record.get("report_date")) if record.get("report_date") else None,
            "announcement_date": str(record.get("announcement_date")) if record.get("announcement_date") else None,
            "report_type": record.get("report_type"),
            "data_source": record.get("data_source"),
        }
        return {"data": data, "meta": meta}

    @staticmethod
    def _sort_records(records: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
        """按报告期和公告日倒序排列财务记录。

        Args:
            records: 标准化财务记录列表。

        Returns:
            倒序排列后的记录列表。
        """
        return sorted(
            records,
            key=lambda item: (
                str(item.get("report_date") or ""),
                str(item.get("announcement_date") or ""),
                str(item.get("report_type") or ""),
            ),
            reverse=True,
        )

    async def _call_financial_fetcher(
        self,
        fetcher: Callable[..., Awaitable[Dict[str, Any]]],
        stock_code: str,
    ) -> list[Dict[str, Any]]:
        """调用数据源管理器并提取标准化财务记录。

        Args:
            fetcher: ``ingestor_manager`` 上的财务采集方法。
            stock_code: 标准股票代码。

        Returns:
            标准化财务记录列表；数据源无结果时返回空列表。
        """
        start_date, end_date = self._default_date_range()
        result = await fetcher(stock_code, start_date, end_date)
        if not isinstance(result, dict) or not result.get("success"):
            return []
        records = result.get("data") or []
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    async def _fetch_financial_data_from_source(self, stock_code: str, data_type: str) -> Dict[str, Any]:
        """实时从数据源拉取财务数据。

        Args:
            stock_code: 股票代码。
            data_type: 数据类型 (financial_indicator, income_statement, balance_sheet, cashflow_statement)。

        Returns:
            从数据源返回的原始财务数据。
        """
        from app.data.ingestors.manager import ingestor_manager

        fetcher_map = {
            "financial_indicator": ingestor_manager.fetch_and_ingest_financial_indicators,
            "financial_indicator_history": ingestor_manager.fetch_and_ingest_financial_indicators,
            "income_statement": ingestor_manager.fetch_and_ingest_income_statement,
            "balance_sheet": ingestor_manager.fetch_and_ingest_balance_sheet,
            "cashflow_statement": ingestor_manager.fetch_and_ingest_cashflow_statement,
        }
        fetcher = fetcher_map.get(data_type)
        if fetcher is None:
            return {}

        records = self._sort_records(await self._call_financial_fetcher(fetcher, stock_code))
        if not records:
            return {}
        snapshots = [self._normalize_record(record) for record in records]
        if data_type.endswith("_history"):
            return {"items": snapshots}
        return snapshots[0]

    async def _get_latest_financials(self, db: Session, stock_code: str) -> Dict[str, Any]:
        """获取最新财务指标 - 实时拉取模式。

        Args:
            db: 数据库会话（保留接口兼容性，暂不使用）。
            stock_code: 股票代码。

        Returns:
            财务指标快照。
        """
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        # 实时拉取财务指标数据
        raw_data = await self._fetch_financial_data_from_source(formatted_code, "financial_indicator")

        if not raw_data:
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))
        meta = raw_data.get("meta", {})

        result = {
            "data": data,
            "meta": meta
        }

        return self._drop_nulls(result)

    async def _get_historical_summary(self, db: Session, stock_code: str, limit: int = 8) -> list[Dict[str, Any]]:
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
        raw_data = await self._fetch_financial_data_from_source(formatted_code, "financial_indicator_history")

        history: list[Dict[str, Any]] = []
        for item in raw_data.get("items", [])[:limit]:
            if not item:
                continue
            history.append(self._drop_nulls({
                "data": self._drop_nulls(item.get("data", {})),
                "meta": item.get("meta", {}),
            }))
        return history

    async def _get_latest_income_statement(
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

        raw_data = await self._fetch_financial_data_from_source(formatted_code, "income_statement")

        if not raw_data or not raw_data.get("data"):
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))

        result = {
            "data": data,
            "meta": raw_data.get("meta", {})
        }

        if format_for_context:
            return self._localize_raw_data(result, self._INCOME_STATEMENT_TABLE) or {}
        return self._drop_nulls(result)


    async def _get_latest_balance_sheet(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期资产负债表 - 实时拉取模式。"""
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        raw_data = await self._fetch_financial_data_from_source(formatted_code, "balance_sheet")

        if not raw_data or not raw_data.get("data"):
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))

        result = {
            "data": data,
            "meta": raw_data.get("meta", {})
        }

        if format_for_context:
            return self._localize_raw_data(result, self._BALANCE_SHEET_TABLE) or {}
        return self._drop_nulls(result)


    async def _get_latest_cashflow_statement(
        self,
        db: Session,
        stock_code: str,
        *,
        format_for_context: bool = True,
    ) -> Dict[str, Any]:
        """读取最新一期现金流量表 - 实时拉取模式。"""
        formatted_code = StockCodeStandardizer.standardize(stock_code)

        raw_data = await self._fetch_financial_data_from_source(formatted_code, "cashflow_statement")

        if not raw_data or not raw_data.get("data"):
            return {}

        data = self._drop_nulls(raw_data.get("data", {}))

        result = {
            "data": data,
            "meta": raw_data.get("meta", {})
        }

        if format_for_context:
            return self._localize_raw_data(result, self._CASHFLOW_STATEMENT_TABLE) or {}
        return self._drop_nulls(result)
