from typing import List, Dict, Any
from app.data.ingestors.manager import ingestor_manager
import app.core.database as database_module
from app.models.data_storage import (
    StockBasic, StockValuationHistory,
    StockTopHolders, KlineData
)
from app.core.logger import get_logger
from sqlalchemy import desc, select

logger = get_logger(__name__)


class UnsupportedColumnsError(ValueError):
    """
    表示调用方请求了模型不存在的顶层字段。

    Args:
        model_name: SQLAlchemy 模型名。
        unsupported_columns: 请求中不被模型支持的字段列表。
        available_columns: 模型真实可查询字段列表。
    """

    def __init__(self, model_name: str, unsupported_columns: List[str], available_columns: List[str]):
        self.model_name = model_name
        self.unsupported_columns = unsupported_columns
        self.available_columns = available_columns
        super().__init__(f"Unsupported columns: {', '.join(unsupported_columns)}")

    def to_dict(self) -> Dict[str, Any]:
        """
        将字段校验错误转换为工具可返回的结构化错误。

        Returns:
            包含错误类型、模型名、不支持字段、可用字段和修正提示的字典。
        """
        return {
            "error": "Unsupported columns",
            "model_name": self.model_name,
            "unsupported_columns": self.unsupported_columns,
            "available_columns": self.available_columns,
            "hint": (
                "Use get_database_schema and request only top-level model columns. "
                "For JSONB report data, request the top-level data column instead of nested metric keys."
            ),
        }


class StockTools:
    """
    Agent 专用的股票数据获取工具集 (Data retrieval tools for Agents)
    封装了从数据库和 ingestor 获取数据并转换为分析友好格式的逻辑。
    """

    @staticmethod
    async def get_stock_basic_info(stock_code: str) -> Dict[str, Any]:
        """获取股票基础信息。

        Args:
            stock_code: 标准股票代码。

        Returns:
            股票基础信息；股本字段来自最新估值表记录，使用“股”口径。
        """
        async with database_module.AsyncSessionLocal() as db:
            stock = (await db.execute(select(StockBasic).where(StockBasic.stock_code == stock_code))).scalar_one_or_none()
            if stock:
                latest_valuation = (await db.execute(
                    select(StockValuationHistory)
                    .where(StockValuationHistory.stock_code == stock_code)
                    .order_by(desc(StockValuationHistory.data_date))
                    .limit(1)
                )).scalar_one_or_none()
                total_share = latest_valuation.total_share if latest_valuation else None
                float_share = latest_valuation.float_share if latest_valuation else None
                return {
                    "stock_code": stock.stock_code,
                    "stock_name": stock.name,
                    "industry": stock.industry,
                    "market": stock.market,
                    "list_date": stock.list_date,
                    "total_share": total_share,
                    "float_share": float_share,
                    "share_unit": "shares" if total_share or float_share else None,
                    "share_source": "stock_valuation_history" if total_share or float_share else None,
                }
        return {}

    @staticmethod
    async def get_valuation_history(stock_code: str, limit: int = 10) -> List[Dict[str, Any]]:
        """获取最近的估值历史 (Get recent valuation history)"""
        async with database_module.AsyncSessionLocal() as db:
            valuations = (await db.execute(
                select(StockValuationHistory)
                .where(StockValuationHistory.stock_code == stock_code)
                .order_by(desc(StockValuationHistory.data_date))
                .limit(limit)
            )).scalars().all()
            return [{k: v for k, v in v.__dict__.items() if not k.startswith('_')} for v in valuations]

    @staticmethod
    async def get_recent_kline(stock_code: str, limit: int = 60) -> List[Dict[str, Any]]:
        """获取最近的日线数据 (Get recent daily Kline data)"""
        async with database_module.AsyncSessionLocal() as db:
            klines = (await db.execute(
                select(KlineData)
                .where(KlineData.stock_code == stock_code)
                .order_by(desc(KlineData.date))
                .limit(limit)
            )).scalars().all()
            if not klines:
                return []

            data = [{k: v for k, v in k.__dict__.items() if not k.startswith('_')} for k in klines]
            return sorted(data, key=lambda item: item.get("date"))

    @staticmethod
    async def get_realtime_quotes(stock_codes: List[str]) -> List[Dict[str, Any]]:
        """获取实时行情 (Get real-time quotes)"""
        results = []
        for code in stock_codes:
            # 尝试通过 ingestor 获取最新行情
            quote = await ingestor_manager.fetch_and_ingest_realtime_market(code)
            if quote:
                results.append(quote)
        return results

    @staticmethod
    async def get_top_holders(stock_code: str) -> List[Dict[str, Any]]:
        """获取十大股东信息 (Get top 10 shareholders)"""
        async with database_module.AsyncSessionLocal() as db:
            latest_report = (await db.execute(
                select(StockTopHolders.report_date)
                .where(StockTopHolders.stock_code == stock_code)
                .order_by(desc(StockTopHolders.report_date))
                .limit(1)
            )).first()
            if not latest_report:
                return []

            report_date = latest_report[0]
            holders = (await db.execute(
                select(StockTopHolders)
                .where(
                    StockTopHolders.stock_code == stock_code,
                    StockTopHolders.report_date == report_date
                )
                .order_by(StockTopHolders.holder_rank.asc().nullslast(), desc(StockTopHolders.hold_amount))
                .limit(10)
            )).scalars().all()
            return [{k: v for k, v in h.__dict__.items() if not k.startswith('_')} for h in holders]

    @staticmethod
    async def check_data_status(stock_code: str) -> Dict[str, Any]:
        """
        检查股票数据的完整性 (Check data completeness in DB)
        返回各维度的最新数据日期或是否存在。
        """
        status = {}
        async with database_module.AsyncSessionLocal() as db:
            # 基础信息
            basic = (await db.execute(select(StockBasic).where(StockBasic.stock_code == stock_code))).scalar_one_or_none()
            status["basic_info"] = "exists" if basic else "missing"

            # K线数据
            latest_k = (await db.execute(
                select(KlineData.date)
                .where(KlineData.stock_code == stock_code)
                .order_by(desc(KlineData.date))
                .limit(1)
            )).first()
            status["kline_data"] = str(latest_k[0]) if latest_k else "missing"

            # 估值数据
            latest_val = (await db.execute(
                select(StockValuationHistory.data_date)
                .where(StockValuationHistory.stock_code == stock_code)
                .order_by(desc(StockValuationHistory.data_date))
                .limit(1)
            )).first()
            status["valuation_history"] = str(latest_val[0]) if latest_val else "missing"

        return status

    async def get_generic_db_data(
        model_name: str,
        identifier: str = "",
        limit: int = 50,
        start_time: str = None,
        end_time: str = None,
        columns: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        从受支持的数据模型查询通用股票或市场数据。

        Args:
            model_name: SQLAlchemy 模型名。
            identifier: 可选标识符，会按模型字段匹配 stock_code、symbol、indicator_code 或 sector_name。
            limit: 最大返回行数。
            start_time: 可选起始时间，用于模型日期字段过滤。
            end_time: 可选结束时间，用于模型日期字段过滤。
            columns: 可选返回列名列表；传入时仅查询并返回这些列。

        Returns:
            查询结果字典列表。

        Raises:
            ValueError: columns 包含模型不支持的列名。
        """
        model = StockTools._resolve_model(model_name)
        if not model:
            logger.error(f"Model {model_name} not found in supported model modules")
            return []

        selected_columns = StockTools._normalize_selected_columns(model, columns)

        async with database_module.AsyncSessionLocal() as db:
            query = StockTools._build_model_query(model, selected_columns)

            # 灵活处理过滤标识符 (Flexible identifier filtering)
            if identifier:
                if hasattr(model, 'stock_code'):
                    query = query.where(model.stock_code == identifier)
                elif hasattr(model, 'symbol'):
                    query = query.where(model.symbol == identifier)
                elif hasattr(model, 'indicator_code'):
                    query = query.where(model.indicator_code == identifier)
                elif hasattr(model, 'sector_name'):
                    query = query.where(model.sector_name == identifier)

            # 时间范围过滤 (Time range filtering)
            date_col_candidates = [
                'trade_date', 'data_date', 'report_date', 'publish_date',
                'update_date', 'date', 'datetime', 'end_date', 'timestamp'
            ]
            applied_date_col = None
            for date_col in date_col_candidates:
                if hasattr(model, date_col):
                    applied_date_col = date_col
                    break

            if applied_date_col:
                col_attr = getattr(model, applied_date_col)
                # 保存原始查询以便回溯 (Save original query for fallback)
                base_query = query
                
                if start_time:
                    query = query.where(col_attr >= start_time)
                if end_time:
                    query = query.where(col_attr <= end_time)

                # 根据该日期字段倒序排列
                query = query.order_by(desc(col_attr))
                
                results = StockTools._query_result_records(await db.execute(query.limit(limit)), selected_columns)
                
                # 如果带时间过滤的结果为空，且确实传了时间参数，则触发回溯 (Fallback if no results)
                if not results and (start_time or end_time):
                    logger.info(
                        "No results in requested range, falling back to latest.",
                        extra={
                            "model_name": model_name,
                            "start_time": start_time,
                            "end_time": end_time,
                        },
                    )
                    results = StockTools._query_result_records(
                        await db.execute(base_query.order_by(desc(col_attr)).limit(limit)),
                        selected_columns,
                    )
                    serialized_results = StockTools._serialize_query_results(results, selected_columns)
                    return [{**item, "_fallback": True} for item in serialized_results]
                
                return StockTools._serialize_query_results(results, selected_columns)

            # 无日期字段的兜底逻辑
            results = StockTools._query_result_records(await db.execute(query.limit(limit)), selected_columns)
            return StockTools._serialize_query_results(results, selected_columns)

    @staticmethod
    def _resolve_model(model_name: str) -> Any:
        """
        从当前支持的模型模块中解析 SQLAlchemy 模型。

        Args:
            model_name: SQLAlchemy 模型名。

        Returns:
            匹配到的模型类；未匹配时返回 None。
        """
        import app.models.data_storage as storage_models
        import app.models.stock_indicators as indicator_models

        model = getattr(storage_models, model_name, None)
        if not model:
            model = getattr(indicator_models, model_name, None)
        return model

    @staticmethod
    def _normalize_selected_columns(model: Any, columns: List[str] | None) -> List[str]:
        """
        校验并规范化查询列名，保留调用方传入顺序。

        Args:
            model: SQLAlchemy 模型类。
            columns: 调用方请求的列名列表。

        Returns:
            已去重且存在于模型上的列名列表；未传 columns 时返回空列表。

        Raises:
            UnsupportedColumnsError: columns 包含模型不支持的列名。
        """
        if not columns:
            return []

        selected_columns = []
        for column in columns:
            column_name = str(column).strip()
            if column_name and column_name not in selected_columns:
                selected_columns.append(column_name)

        unsupported_columns = [column for column in selected_columns if not hasattr(model, column)]
        if unsupported_columns:
            raise UnsupportedColumnsError(
                model.__name__,
                unsupported_columns,
                StockTools._get_model_column_names(model),
            )
        return selected_columns

    @staticmethod
    def _get_model_column_names(model: Any) -> List[str]:
        """
        获取模型真实可查询的顶层字段名。

        Args:
            model: SQLAlchemy 模型类。

        Returns:
            按模型声明顺序排列的字段名列表。
        """
        return [column.name for column in model.__table__.columns]

    @staticmethod
    def _build_model_query(model: Any, selected_columns: List[str]) -> Any:
        """
        根据列选择构建 SQLAlchemy 查询对象。

        Args:
            model: SQLAlchemy 模型类。
            selected_columns: 已校验的列名列表。

        Returns:
            SQLAlchemy 查询对象。
        """
        if not selected_columns:
            return select(model)
        return select(*(getattr(model, column) for column in selected_columns))

    @staticmethod
    def _query_result_records(result: Any, selected_columns: List[str]) -> List[Any]:
        """
        从异步执行结果中提取 ORM 实例或列投影行。

        Args:
            result: AsyncSession.execute 返回的结果对象。
            selected_columns: 已校验的列名列表。

        Returns:
            可序列化前的查询记录列表。
        """
        return result.all() if selected_columns else result.scalars().all()

    @staticmethod
    def _serialize_query_results(records: List[Any], selected_columns: List[str]) -> List[Dict[str, Any]]:
        """
        将 ORM 实例或列投影行统一转换为字典列表。

        Args:
            records: SQLAlchemy 查询返回的记录。
            selected_columns: 已校验的列名列表。

        Returns:
            查询结果字典列表。
        """
        if not selected_columns:
            return [{k: v for k, v in record.__dict__.items() if not k.startswith('_')} for record in records]

        serialized_records = []
        for record in records:
            mapping = record._mapping if hasattr(record, "_mapping") else {}
            serialized_records.append({column: mapping[column] for column in selected_columns})
        return serialized_records
