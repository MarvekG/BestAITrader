from app.data.ingestors.plugins.column_mapping import ColumnMapper
from app.data.metadata.field_labels import get_table_field_label

def test_column_mapper_config_loading():
    mapper = ColumnMapper()
    # Verify global mappings are loaded
    mapping = mapper.get_global_mapping('akshare')
    assert '代码' in mapping
    assert mapping['代码'] == 'stock_code'
    
    # Verify table specific mappings need to be accessed via get_mapping or map_columns
    # But we can check protected _table_specific_mapping if needed, or better, test public API
    
    # Test mapping specific to a table (if defined in json)
    # In json created previously: "stock_basic": { "symbol": "stock_code" }
    
    # Validating protected config after load
    assert ColumnMapper._mapping_config is not None
    assert 'global_mappings' in ColumnMapper._mapping_config
    label = get_table_field_label("data.financial_indicator", "diluted_eps")
    assert label == "稀释每股收益"

def test_get_mapping_equivalents():
    # Testing direct mapping retrieval
    mapping = ColumnMapper.get_global_mapping('akshare')
    assert mapping.get('代码') == 'stock_code'
    
    mapping_en = ColumnMapper.get_global_mapping('tushare')
    assert mapping_en.get('ts_code') == 'stock_code'

def test_financial_indicator_standard_key_loaded_from_json():
    assert ColumnMapper.get_financial_indicator_standard_key("dt_eps", "tushare_fina_indicator") == "diluted_eps"
    assert ColumnMapper.get_financial_indicator_standard_key("ar_turn", "tushare_fina_indicator") == "accounts_receivable_turnover"
    assert ColumnMapper.get_financial_indicator_standard_key("摊薄每股收益(元)", "akshare_financial_analysis_indicator") == "eps"
    assert ColumnMapper.get_financial_indicator_standard_key("销售毛利率(%)", "akshare_financial_analysis_indicator") == "gross_margin"
    assert ColumnMapper.get_financial_indicator_standard_key("净利润增长率(%)", "akshare_financial_analysis_indicator") == "net_profit_yoy"
    assert ColumnMapper.get_financial_indicator_standard_key("总资产周转率(次)", "akshare_financial_analysis_indicator") == "asset_turnover"
    assert ColumnMapper.get_financial_indicator_standard_key("assets_turn", "tushare_fina_indicator") == "asset_turnover"
    assert ColumnMapper.get_financial_indicator_standard_key("total_revenue_ps", "tushare_fina_indicator") == "total_revenue_ps"
    assert ColumnMapper.get_financial_indicator_standard_key("revenue_ps", "tushare_fina_indicator") == "revenue_ps"
    assert ColumnMapper.get_financial_indicator_standard_key("经营现金净流量与净利润的比率(%)", "akshare_financial_analysis_indicator") == "ocf_to_profit"
    assert ColumnMapper.get_financial_indicator_standard_key("应收账款周转天数(天)", "akshare_financial_analysis_indicator") == "accounts_receivable_turnover_days"
    assert ColumnMapper.get_financial_indicator_standard_key("流动资产周转天数(天)", "akshare_financial_analysis_indicator") == "current_asset_turnover_days"
    assert ColumnMapper.get_financial_indicator_standard_key("capital_rese_ps", "tushare_fina_indicator") == "capital_reserve_ps"
    assert ColumnMapper.get_financial_indicator_standard_key("surplus_rese_ps", "tushare_fina_indicator") == "surplus_reserve_ps"
    assert ColumnMapper.get_financial_indicator_standard_key("undist_profit_ps", "tushare_fina_indicator") == "undistributed_profit_ps"
    assert ColumnMapper.get_financial_indicator_standard_key("retainedps", "tushare_fina_indicator") == "retained_earnings_ps"
    assert ColumnMapper.get_financial_indicator_standard_key("roe_dt", "tushare_fina_indicator") == "roe_diluted"
    assert ColumnMapper.get_financial_indicator_standard_key("加权净资产收益率(%)", "akshare_financial_analysis_indicator") == "roe_waa"


def test_tushare_income_statement_mapping_includes_optional_fields():
    mapping = ColumnMapper.get_table_mapping("data.stock_income_statement", "tushare_income_statement")
    expected_identity_fields = {
        "amodcost_fin_assets",
        "asset_disp_income",
        "credit_impa_loss",
        "end_net_profit",
        "net_after_nr_lp_correct",
        "net_expo_hedging_benefits",
        "oth_impair_loss_assets",
        "oth_income",
        "total_opcost",
    }

    for field in expected_identity_fields:
        assert mapping[field] == field


def test_get_table_field_label_prefers_table_labels():
    assert get_table_field_label("data.financial_indicator", "diluted_eps") == "稀释每股收益"
