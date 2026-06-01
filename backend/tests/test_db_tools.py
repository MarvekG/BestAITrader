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
