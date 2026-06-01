import json
import logging
from pathlib import Path
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger(__name__)


class ColumnMapper:
    """
    负责将源数据列名映射为标准数据库列名
    Loads mappings from ./column_mapping.json.
    """
    _mapping_config = None

    @classmethod
    def _load_json_config(cls, attr_name: str, file_name: str, default: Dict[str, Any]):
        config = getattr(cls, attr_name)
        if config is None:
            config_path = Path(__file__).resolve().parent / file_name
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.error(f"Failed to load {file_name}: {e}")
                config = default
            setattr(cls, attr_name, config)
        return config

    @classmethod
    def get_global_mapping(cls, source: str) -> Dict[str, str]:
        config = cls._load_json_config(
            "_mapping_config",
            "column_mapping.json",
            {"global_mappings": {}, "table_specific_mappings": {}},
        )
        return config.get("global_mappings", {}).get(source, {})

    @classmethod
    def get_table_mapping(cls, table: str, source: str) -> Dict[str, str]:
        config = cls._load_json_config(
            "_mapping_config",
            "column_mapping.json",
            {"global_mappings": {}, "table_specific_mappings": {}},
        )
        table_mappings = config.get("table_specific_mappings", {}).get(table, {})
        return table_mappings.get(source, {})

    @classmethod
    def get_financial_indicator_standard_key(cls, source_key: str, source: str) -> str:
        source_mapping = cls.get_table_mapping("data.financial_indicator", source)
        return source_mapping.get(source_key, source_key)

    @staticmethod
    def map_columns(df: pd.DataFrame, target_table: str, source: str = 'tushare', strict: bool = True) -> pd.DataFrame:
        """
        映射 DataFrame 列名
        :param strict: If True, raises ValueError if mapping keys are missing in df
        """
        mapping = {}
        
        # 1. Global mapping
        global_mapping = ColumnMapper.get_global_mapping(source)
        mapping.update(global_mapping)
            
        # 2. Table specific mapping (Higher priority)
        table_mapping = ColumnMapper.get_table_mapping(target_table, source)
        mapping.update(table_mapping)
        
        if strict:
            # 方案 B: 严格双向匹配
            # 1. 映射配置 (table_mapping) 中的所有 Key 必须在 DataFrame 中存在
            table_keys = set(table_mapping.keys())
            df_keys = set(df.columns)
            
            missing_in_df = table_keys - df_keys
            if missing_in_df:
                logger.error(f"CRITICAL: Column mapping mismatch (Missing in DF) for {target_table}")
                logger.error(f"Mapping keys defined for this table: {sorted(list(table_keys))}")
                logger.error(f"DF columns present: {sorted(list(df_keys))}")
                logger.error(f"Missing keys: {sorted(list(missing_in_df))}")
                raise ValueError(f"Column mapping mismatch! Mapping keys missing in DF: {sorted(list(missing_in_df))}")
            
            # 2. DataFrame 中的所有列必须在映射配置 (global + table) 中定义
            all_mapping_keys = set(mapping.keys())
            extra_in_df = df_keys - all_mapping_keys
            
            if extra_in_df:
                logger.error(f"CRITICAL: Column mapping mismatch (Extra in DF) for {target_table}")
                logger.error(f"DF columns: {sorted(list(df_keys))}")
                logger.error(f"Allowed keys (mapping.json): {sorted(list(all_mapping_keys))}")
                logger.error(f"Extra columns: {sorted(list(extra_in_df))}")
                raise ValueError(f"Column mapping mismatch! Extra columns in DF not in mapping: {sorted(list(extra_in_df))}")
            
        # 3. Rename with conflict avoidance
        for src, dst in mapping.items():
            if src in df.columns:
                if dst in df.columns and src != dst:
                    # Target column already exists, drop source to avoid duplication
                    df.drop(columns=[src], inplace=True)
                else:
                    df.rename(columns={src: dst}, inplace=True)
        
        return df
