import akshare as ak
import pandas as pd

try:
    df = ak.stock_board_industry_name_em()
    print("Columns:", df.columns.tolist())
    if not df.empty:
        print("First row:", df.iloc[0].to_dict())
        # Check data types
        print("Data Types:")
        print(df.dtypes)
    else:
        print("DataFrame is empty.")
except Exception as e:
    print(f"Error: {e}")
