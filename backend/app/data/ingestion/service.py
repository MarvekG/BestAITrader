import pandas as pd
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
from sqlalchemy import text, func, MetaData
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.core.database import engine
from app.models.data_registry import ApiRegistry
from app.core.utils.formatters import StockCodeStandardizer
from app.models.data_storage import (
    Base, CommonData, KlineData, StockBasic, NorthboundData, DragonTigerData, StockValuationHistory,
    IndustryData, StockRealtimeMarket,
    StockLimitUpPool, StockLimitDownPool, StockZhabanPool, StockMoneyFlow, StockShareholder, StockPledge, StockPledgeSummary,
    StockInsider, StockRelease, StockMargin, IndexDaily,
    StockBlockTrade, SectorMoneyFlow,
    StockTopHolders
)


# 配置日志
from app.core.logger import get_logger
logger = get_logger(__name__)


class DataIngestionService:
    def __init__(self):
        self.engine = engine
        self.metadata = Base.metadata
        # 建立表名到模型类的映射，用于快速查找
        self.table_map = {
            'common_data': CommonData,
            'kline_data': KlineData,
            'stock_basic': StockBasic,
            'northbound_data': NorthboundData,
            'dragon_tiger_data': DragonTigerData,
            'stock_valuation_history': StockValuationHistory,
            'industry_data': IndustryData,
            'stock_realtime_market': StockRealtimeMarket,
            'stock_limit_up_pool': StockLimitUpPool,
            'stock_limit_down_pool': StockLimitDownPool,
            'stock_zhaban_pool': StockZhabanPool,
            'stock_money_flow': StockMoneyFlow,
            'stock_shareholder_count': StockShareholder,
            'stock_pledge_risk': StockPledge,
            'stock_insider_trading': StockInsider,
            'stock_lockup_release': StockRelease,
            'stock_margin_data': StockMargin,
            'index_daily': IndexDaily,
            'stock_block_trade': StockBlockTrade,
            'sector_money_flow': SectorMoneyFlow,
            'stock_pledge_summary': StockPledgeSummary,
            'stock_top_holders': StockTopHolders,
            # 兼容带 schema 的名称 (虽然 SQLAlchemy metadata.tables 可能不带 schema)
            'data.common_data': CommonData,
            'data.kline_data': KlineData,
            'data.stock_basic': StockBasic,
            'data.northbound_data': NorthboundData,
            'data.dragon_tiger_data': DragonTigerData,
            'data.stock_valuation_history': StockValuationHistory,
            'data.stock_realtime_market': StockRealtimeMarket,
            'data.stock_limit_up_pool': StockLimitUpPool,
            'data.stock_limit_down_pool': StockLimitDownPool,
            'data.stock_zhaban_pool': StockZhabanPool,
            'data.stock_money_flow': StockMoneyFlow,
            'data.stock_shareholder_count': StockShareholder,
            'data.stock_pledge_risk': StockPledge,
            'data.stock_insider_trading': StockInsider,
            'data.stock_lockup_release': StockRelease,
            'data.stock_margin_data': StockMargin,
            'data.index_daily': IndexDaily,
            'data.stock_pledge_summary': StockPledgeSummary,
            'data.stock_top_holders': StockTopHolders
        }
        # 确保表存在
        if self.engine.dialect.name.startswith("postgresql"):
            with self.engine.begin() as conn:
                conn.execute(text("CREATE SCHEMA IF NOT EXISTS stock_picker;"))
        Base.metadata.create_all(self.engine)

    def write_dataframe(
        self,
        api_name: str,
        df: pd.DataFrame,
        source: str = 'tushare',
        force_sync: bool = False,
        target_table: Optional[str] = None,
    ):
        """
        通用数据写入方法
        :param api_name: API名称 (用于 CommonData 的 api_name 字段)
        :param df: Pandas DataFrame 数据
        :param source: 数据来源
        :param force_sync: 保留参数，当前写入逻辑不使用。
        :param target_table: 目标表名 (如 'kline_data' 或 'common_data')。如果为 None，默认写入 CommonData
        """
        if df.empty:
            logger.warning(f"Skipping empty dataframe for {api_name}")
            return False

        # 确定目标模型
        target_model = None
        if target_table:
            # 尝试从映射中查找
            target_model = self.table_map.get(target_table)
            if not target_model:
                # 尝试去掉 'data.' 或 'public.' 前缀查找
                clean_name = target_table.replace('data.', '').replace('public.', '')
                target_model = self.table_map.get(clean_name)
        
        # 默认为 CommonData
        if not target_model:
            target_model = CommonData
            logger.info(f"Writing {api_name} to Generic CommonData (Target: {target_table or 'Default'})")
        else:
            logger.info(f"Writing {api_name} to Dedicated Model: {target_model.__tablename__}")

        # 优化：过滤掉 stock_basic 中不存在的 stock_code，避免外键冲突
        # ETF 相关表不需要校验 stock_basic，因为 ETF 代码通常不在 stock_basic 中
        if 'stock_code' in df.columns and target_model not in [StockBasic, SectorMoneyFlow, IndustryData]:
            try:
                logger.info(f"Filtering stock_code for {api_name} against stock_basic...")
                with self.engine.begin() as conn:
                    valid_df = pd.read_sql("SELECT stock_code FROM data.stock_basic", conn)
                
                if valid_df.empty:
                    logger.error(f"TABLE stock_basic IS EMPTY! All records for {api_name} with stock_code will be filtered. Please sync stock_basic first.")
                
                valid_stock_codes = set(valid_df['stock_code'].astype(str).tolist())
                
                original_count = len(df)
                # 记录被过滤的代码，以便调试
                mask = df['stock_code'].astype(str).isin(valid_stock_codes)
                invalid_df = df[~mask]
                df = df[mask].copy()
                
                filtered_count = original_count - len(df)
                
                if filtered_count > 0:
                    sample_invalid = invalid_df['stock_code'].head(5).tolist()
                    logger.warning(
                        f"Filtered {filtered_count} records from {api_name} because stock_code does not exist in stock_basic. "
                        f"Sample invalid codes: {sample_invalid}. Please ensure stock_basic is synced."
                    )
                
                if df.empty and original_count > 0:
                    logger.warning(f"No records left after filtering stock_codes for {api_name}, skipping ingestion")
                    return False
            except Exception as e:
                logger.error(f"Failed to filter stock_codes for {api_name}: {e}")
                # 过滤异常时不阻断流程，让后续可能的 DB 抛出具体错误，或者如果是外键缺失则正常失败

        # 智能去重：根据模型的唯一约束或唯一索引对 DataFrame 进行去重，避免 ON CONFLICT DO UPDATE 报错 (CardinalityViolation)
        try:
            from sqlalchemy.schema import UniqueConstraint
            table = target_model.__table__
            dedup_cols = []
            
            # 查找业务主键（唯一约束）
            unique_constraints = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
            if unique_constraints:
                dedup_cols = [col.name for col in unique_constraints[0].columns]
            
            # 如果没找到，查找唯一索引
            if not dedup_cols:
                unique_indexes = [idx for idx in table.indexes if idx.unique]
                if unique_indexes:
                    dedup_cols = [c.name for c in unique_indexes[0].columns]
            
            if dedup_cols:
                # 检查这些列是否都在 DF 中
                missing = [c for c in dedup_cols if c not in df.columns]
                if not missing:
                    original_len = len(df)
                    df = df.drop_duplicates(subset=dedup_cols, keep='last')
                    new_len = len(df)
                    if original_len > new_len:
                        logger.info(f"Deduplicated {original_len - new_len} records for {api_name} based on columns {dedup_cols}")
                else:
                    # 某些列不在 DF 中（可能是 CommonData），执行全量去重
                    df = df.drop_duplicates()
            else:
                # 没有任何约束，全量去重
                df = df.drop_duplicates()
        except Exception as e:
            logger.warning(f"Failed to auto-deduplicate dataframe for {api_name}: {e}")

        try:
            records = self._prepare_records(df, target_model, api_name, source)
            if not records:
                logger.warning(f"No valid records prepared for {api_name}")
                return False

            self._bulk_upsert(target_model, records)
            return True

        except Exception as e:
            logger.error(f"Failed to write dataframe for {api_name}: {e}", exc_info=True)
            return False

    @staticmethod
    def _json_serializable(obj):
        """Recursive helper to make dict/list JSON serializable"""
        if isinstance(obj, (datetime, pd.Timestamp)):
            return obj.isoformat()
        if hasattr(obj, 'date') and callable(getattr(obj, 'date')): # datetime
             return obj.date().isoformat()
        if hasattr(obj, 'isoformat'): # date
            return obj.isoformat()
        if isinstance(obj, dict):
             return {k: DataIngestionService._json_serializable(v) for k, v in obj.items()}
        if isinstance(obj, list):
             return [DataIngestionService._json_serializable(v) for v in obj]
        return obj

    def _prepare_records(self, df: pd.DataFrame, target_model, api_name: str, source: str) -> List[Dict]:
        """准备写入数据库的记录"""
        records = []
        
        # 获取目标模型的列名，用于过滤无效字段 (如果是专用表)
        model_columns = set(c.name for c in target_model.__table__.columns) if target_model != CommonData else set()

        for _, row in df.iterrows():
            row_dict = row.to_dict()
            
            # 清理 NaN 值 并处理 JSON 序列化
            clean_row = {}
            for k, v in row_dict.items():
                if pd.isna(v):
                    clean_row[k] = None
                else:
                    # 使用递归序列化处理所有值，确保 JSON 字段内的日期也能正确转义
                    clean_row[k] = self._json_serializable(v)

            if target_model == CommonData:
                # CommonData 逻辑
                record = self._prepare_common_data_record(clean_row, api_name, source)
                records.append(record)
            else:
                # 专用表逻辑
                # 1. 注入 data_source
                if 'data_source' in model_columns and 'data_source' not in clean_row:
                    clean_row['data_source'] = source
                
                # 2. 过滤掉不在模型中的字段 (避免 SQL 错误)
                final_record = {k: v for k, v in clean_row.items() if k in model_columns}
                
                # 3. 确保 ID 存在 (对于 bulk_insert，如果数据库不自动生成或者显式传 None 会报错)
                if 'id' in model_columns and not final_record.get('id'):
                    import uuid
                    final_record['id'] = str(uuid.uuid4())
                
                records.append(final_record)
        
        return records

    def _prepare_common_data_record(self, row_dict: Dict, api_name: str, source: str) -> Dict:
        """构建 CommonData 记录"""
        # 1. 提取 Stock Code
        stock_code = (
            row_dict.get('stock_code') or 
            row_dict.get('code') or 
            row_dict.get('symbol') or 
            row_dict.get('ts_code') or 
            row_dict.get('代码') or 
            'UNKNOWN'
        )
        if stock_code != 'UNKNOWN':
             stock_code = StockCodeStandardizer.standardize(str(stock_code))

        # 2. 智能提取 Update Date (PK)
        update_date_val = None
        # key candidates
        date_keys = ['trade_date', 'date', '日期', 'report_date', 'update_date', '持股日期', '上榜日', '公告日期']
        
        for k in date_keys:
            if row_dict.get(k):
                update_date_val = row_dict.get(k)
                break
        
        # 尝试从 update_time 提取
        if not update_date_val and row_dict.get('update_time'):
             try:
                update_date_val = pd.to_datetime(row_dict.get('update_time')).date()
             except:
                pass

        # 默认为今天
        if update_date_val:
            try:
                # 统一转为 date 对象，再由 SQLAlchemy 处理
                if isinstance(update_date_val, str):
                    update_date = pd.to_datetime(update_date_val).date()
                elif hasattr(update_date_val, 'date') and not isinstance(update_date_val, (str, float, int)): # datetime
                    update_date = update_date_val.date() if callable(update_date_val.date) else update_date_val.date
                else: # date or other
                    update_date = update_date_val
            except:
                update_date = pd.Timestamp.now().date()
        else:
            update_date = pd.Timestamp.now().date()

        # 3. 构建 Data Payload
        # 全部数据作为 payload
        json_payload = row_dict # SQLAlchemy JSONB 自动处理 dict 序列化

        return {
            "api_name": api_name,
            "stock_code": stock_code,
            "update_date": update_date,
            "data_payload": json_payload,
            "data_source": source
        }

    def _bulk_upsert(self, model, records: List[Dict]):
        """执行批量 Upsert"""
        if not records:
            return

        table = model.__table__
        
        # 确定冲突检测的键 (Conflict Target)
        # 优先使用 Unique Constraint (业务主键)，其次使用 Unique Index，最后 PKey
        # 因为很多表 (如 KlineData) 使用 UUID 作为 PK，但实际去重依赖业务字段 (stock_code, date)
        
        from sqlalchemy.schema import UniqueConstraint, Index
        
        conflict_target = None
        
        # 1. 尝试 UniqueConstraint
        unique_constraints = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
        if unique_constraints:
            for uc in unique_constraints:
                 if uc.columns:
                    conflict_target = [col.name for col in uc.columns]
                    break
        
        # 2. 尝试 Unique Index (SQLAlchemy 模型定义的 unique=True 字段通常会生成 Index)
        if not conflict_target:
            unique_indexes = [idx for idx in table.indexes if idx.unique]
            if unique_indexes:
                 # 优先找包含 stock_code 的索引 (Heuristic)
                 for idx in unique_indexes:
                     cols = [c.name for c in idx.columns]
                     conflict_target = cols
                     if 'stock_code' in cols:
                         break

        # 3. 尝试 Primary Key
        if not conflict_target:
             conflict_target = [key.name for key in inspect(model).primary_key]

        if not conflict_target:
             # 无主键也无唯一键，直接 insert
             stmt = pg_insert(table).values(records)
        else:
            stmt = pg_insert(table).values(records)
            
            # 获取 record keys，确保只更新本次 insert 涉及的字段 (Partial Update)
            record_keys = set(records[0].keys()) if records else set()

            # 构建更新字段：排除 conflict_target 和 created_at
            # 注意：ID 也不应该更新，但通常不在 records 中
            update_dict = {
                c.name: stmt.excluded[c.name]
                for c in table.columns
                if c.name not in conflict_target 
                and c.name != 'created_at' 
                and c.name != 'id'
                and c.name in record_keys
            }
            
            if update_dict:
                stmt = stmt.on_conflict_do_update(
                    index_elements=conflict_target,
                    set_=update_dict
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=conflict_target)

        with self.engine.begin() as conn:
            conn.execute(stmt)
        
        logger.info(f"Upserted {len(records)} records to {model.__tablename__}")


# Helper Import (Lazy to avoid circular if placed at top, though top is fine usually)
from sqlalchemy.inspection import inspect
