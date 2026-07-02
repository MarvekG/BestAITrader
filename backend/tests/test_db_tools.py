import pytest

import app.core.database as database_module
from app.ai.agentic import tools as tools_module
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

    dragon_tiger_schema = {column["name"]: column for column in result["schemas"]["DragonTigerData"]}
    assert dragon_tiger_schema["net_buy_amount"]["unit"] == "元"
    assert dragon_tiger_schema["floating_market_capitalization"]["unit"] == "元"

    realtime_schema = {column["name"]: column for column in result["schemas"]["StockRealtimeMarket"]}
    assert realtime_schema["volume"]["unit"] == "股"
    assert realtime_schema["total_market_cap"]["unit"] == "元"
    assert result["field_units"]["StockRealtimeMarket"]["volume"]["unit"] == "股"

    industry_schema = {column["name"]: column for column in result["schemas"]["IndustryData"]}
    assert industry_schema["total_market_cap"]["unit"] == "万元"

    sector_schema = {column["name"]: column for column in result["schemas"]["SectorMoneyFlow"]}
    assert sector_schema["net_inflow"]["unit"] == "元"
    assert sector_schema["close_price"]["display_name"] == "板块最新指数"
    assert sector_schema["close_price"]["unit"] == "点"
    assert result["field_units"]["SectorMoneyFlow"]["close_price"]["unit"] == "点"


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


@pytest.mark.asyncio
async def test_query_and_calculate_closes_db_session_before_sandbox(test_db, monkeypatch):
    """沙箱执行前应关闭数据库查询会话。"""
    state = {"active": 0, "exited": 0}

    class TrackingSessionContext:
        def __init__(self):
            self._session_context = test_db()

        async def __aenter__(self):
            state["active"] += 1
            return await self._session_context.__aenter__()

        async def __aexit__(self, exc_type, exc, tb):
            try:
                return await self._session_context.__aexit__(exc_type, exc, tb)
            finally:
                state["active"] -= 1
                state["exited"] += 1

    async def fake_sandbox(_code):
        assert state["active"] == 0
        assert state["exited"] == 1
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(database_module, "AsyncSessionLocal", TrackingSessionContext)
    monkeypatch.setattr(tools_module, "execute_python_in_sandbox", fake_sandbox)

    result = await query_and_calculate.ainvoke(
        {
            "table_name": "StockBasic",
            "filters": [],
            "compute_code": "result = {'row_count': len(data)}",
            "limit": 1,
        }
    )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_get_database_schema_translates_column_info():
    """数据库结构工具应翻译 SQLAlchemy Column.info 中的名称和单位 key。"""
    result = await get_database_schema.ainvoke({})
    kline_schema = {column["name"]: column for column in result["schemas"]["KlineData"]}
    assert kline_schema["open"]["display_name"] == "开盘"
    assert kline_schema["open"]["unit"] == "元"


@pytest.mark.asyncio
async def test_get_database_schema_column_units_match_field_unit_metadata():
    """所有模型字段单位应与 schema 字段单位元数据保持一致。"""
    result = await get_database_schema.ainvoke({})

    for model_name, columns in result["schemas"].items():
        field_units = result["field_units"].get(model_name, {})
        for column in columns:
            if "unit" not in column:
                continue
            assert field_units[column["name"]]["unit"] == column["unit"], (
                f"{model_name}.{column['name']} unit metadata mismatch"
            )
