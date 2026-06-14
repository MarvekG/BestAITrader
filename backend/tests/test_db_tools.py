import pytest

from app.ai.agentic.tools import get_database_schema, query_and_calculate


@pytest.mark.asyncio
async def test_get_database_schema_returns_model_columns():
    """验证数据库结构工具返回当前 SQLAlchemy 模型字段。"""
    result = await get_database_schema.ainvoke({})

    assert "error" not in result
    assert "schemas" in result
    assert "KlineData" in result["schemas"]

    kline_columns = {column["name"] for column in result["schemas"]["KlineData"]}
    assert {"stock_code", "date", "open", "close"}.issubset(kline_columns)
    kline_schema = {column["name"]: column for column in result["schemas"]["KlineData"]}
    assert kline_schema["open"]["unit"] == "元"
    assert kline_schema["turnover"]["unit"] == "元"
    assert kline_schema["volume"]["unit"] == "手"

    valuation_schema = {column["name"]: column for column in result["schemas"]["StockValuationHistory"]}
    assert valuation_schema["total_market_value"]["unit"] == "元"
    assert valuation_schema["total_share"]["unit"] == "股"
    assert valuation_schema["dividend_yield"]["unit"] == "%"

    northbound_schema = {column["name"]: column for column in result["schemas"]["NorthboundData"]}
    assert northbound_schema["hold_ratio"]["unit"] == "比例"
    assert northbound_schema["net_buy_amount"]["unit"] == "元"

    assert result["field_units"]["FinancialIndicator"]["roe"]["unit"] == "%"

    balance_default_unit = result["field_units"]["StockBalanceSheet"]["$default"]
    assert balance_default_unit["unit"] == "元"

    sector_schema = {column["name"]: column for column in result["schemas"]["SectorMoneyFlow"]}
    assert "unit" not in sector_schema["net_inflow"]


@pytest.mark.asyncio
async def test_query_and_calculate_rejects_unknown_table_without_db_access():
    """未知表名应在连接数据库前被拒绝。"""
    result = await query_and_calculate.ainvoke(
        {
            "table_name": "MissingTable",
            "filters": [],
            "compute_code": "result = 1",
            "limit": 1,
        }
    )

    assert result == {"error": "Table 'MissingTable' not found."}
