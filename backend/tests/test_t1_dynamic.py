import pytest
from datetime import datetime, timedelta
from app.trading.trading_engine import TradingEngine
from uuid import UUID

@pytest.mark.asyncio
async def test_t1_dynamic_logic():
    engine = TradingEngine()
    
    # 模拟账户
    account = {
        "cash_balance": 100000.0,
        "total_assets": 100000.0,
        "market_value": 0.0
    }
    
    # 1. 模拟买入 (今天)
    order_buy = {
        "action": "buy",
        "shares": 100,
        "price": 10.0,
        "order_type": "limit",
        "stock_code": "000001.SZ"
    }
    
    result_buy = await engine.execute_order(order_buy, account, None)
    assert result_buy["success"] is True
    pos = result_buy["updated_position"]
    
    # 验证今天买入的可用股份为 0
    assert engine.get_sellable_shares(pos["purchase_details"]) == 0
    
    # 2. 修改买入时间为昨天
    yesterday_iso = (datetime.now() - timedelta(days=1)).isoformat()
    pos["purchase_details"]["ledger"][0]["time"] = yesterday_iso
    
    # 验证模拟“昨天”买入后，今天可卖出
    assert engine.get_sellable_shares(pos["purchase_details"]) == 100
    
    # 3. 执行卖出
    order_sell = {
        "action": "sell",
        "shares": 100,
        "price": 11.0,
        "order_type": "limit",
        "stock_code": "000001.SZ"
    }
    
    # 注意：execute_order 内部会再次调用 get_sellable_shares
    result_sell = await engine.execute_order(order_sell, result_buy["updated_account"], pos)
    assert result_sell["success"] is True
    assert result_sell["executed_shares"] == 100
    
    # 验证卖出后账本为空
    assert len(result_sell["updated_position"]["purchase_details"]["ledger"]) == 0 if result_sell["updated_position"] else True
