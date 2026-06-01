import asyncio
import pandas as pd
from unittest.mock import Mock, AsyncMock
from app.data.ingestors.plugins.tushare_ingestor import TushareIngestor
import pytest

@pytest.mark.asyncio
async def test_shareholder_complement():
    # 模拟 Tushare 客户端
    mock_pro = Mock()
    
    # 准备股东户数数据
    mock_shareholder_df = pd.DataFrame([
        {'ts_code': '600519.SH', 'end_date': '20230331', 'holder_num': 100000, 'ann_date': '20230420'},
        {'ts_code': '600519.SH', 'end_date': '20230630', 'holder_num': 110000, 'ann_date': '20230820'},
    ])
    
    # 准备日线基础数据
    mock_basic_df = pd.DataFrame([
        {'ts_code': '600519.SH', 'trade_date': '20230331', 'total_share': 125619.78, 'total_mv': 2280000.0},
        {'ts_code': '600519.SH', 'trade_date': '20230630', 'total_share': 125619.78, 'total_mv': 2120000.0},
    ])
    
    # 模拟 _run_in_executor
    ingestor = TushareIngestor()
    ingestor.pro = mock_pro
    # 第一次调用 stk_holdernumber, 第二次调用 daily_basic, 第三次调用 write_dataframe
    ingestor._run_in_executor = AsyncMock(side_effect=[mock_shareholder_df, mock_basic_df, True])
    
    # 模拟 ingestion_service.write_dataframe
    mock_ingestion_service = Mock()
    mock_ingestion_service.write_dataframe = Mock()
    ingestor.ingestion_service = mock_ingestion_service
    
    # 执行同步
    print("Starting full method sync...")
    try:
        result = await ingestor.fetch_and_ingest_stock_shareholder_count('600519.SH')
        print(f"Sync finished with result: {result}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise e
    
    # 验证 _run_in_executor 被调用情况 (stk_holdernumber, daily_basic, write_dataframe)
    print(f"Total calls to _run_in_executor: {ingestor._run_in_executor.call_count}")
    if ingestor._run_in_executor.call_count < 3:
         print("Method did not reach write_dataframe step!")
         return

    # 获取第三次调用 (write_dataframe) 的参数
    # args[0] = write_dataframe method
    # args[1] = 'shareholder_count'
    # args[2] = dataframe
    last_call = ingestor._run_in_executor.call_args_list[-1]
    args, _ = last_call
    df_result = args[2]
    
    print("\nComplemented DataFrame from write_dataframe call:")
    print(df_result)
    
    # 检查结果
    # 20230630 是最新一条 (iloc[0] 因为 sort_values('end_date', ascending=False))
    # Wait, tushare_ingestor.py finally sorts by end_date DESC
    row_0630 = df_result[df_result['end_date'] == pd.to_datetime('20230630')].iloc[0]
    print(f"Row 0630: \n{row_0630}")
    
    assert row_0630['holder_count_prev'] == 100000
    assert row_0630['holder_count_change'] == 10000
    assert row_0630['holder_count_change_ratio'] == 10.0
    assert row_0630['total_share'] == 125619.78 * 10000
    assert row_0630['total_mv'] == 2120000.0 * 10000
    
    expected_avg_shares = (125619.78 * 10000) / 110000
    assert pytest.approx(float(row_0630['avg_hold_shares']), 0.1) == expected_avg_shares
    print("Verification Successful!")

if __name__ == "__main__":
    asyncio.run(test_shareholder_complement())
