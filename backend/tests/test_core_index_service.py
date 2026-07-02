from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import pytest

from app.data.analytics.core_index import get_core_index_constituent_codes


@pytest.mark.asyncio
async def test_get_core_index_constituent_codes_raises_without_tushare_token():
    with patch("app.data.analytics.core_index.get_data_source_config_value", new_callable=AsyncMock, return_value=""):
        with pytest.raises(RuntimeError, match="Tushare token is required"):
            await get_core_index_constituent_codes(["000300.SH"])


@pytest.mark.asyncio
async def test_get_core_index_constituent_codes_uses_latest_trade_date():
    mock_pro = Mock()
    mock_pro.index_weight.return_value = pd.DataFrame(
        {
            "index_code": ["000300.SH", "000300.SH", "000300.SH"],
            "con_code": ["600519.SH", "000001.SZ", "300750.SZ"],
            "trade_date": ["20260331", "20260331", "20260228"],
        }
    )

    with patch("app.data.analytics.core_index.get_data_source_config_value", new_callable=AsyncMock, return_value="test-token"), \
         patch("app.data.analytics.core_index.ts.pro_api", return_value=mock_pro):
        codes = await get_core_index_constituent_codes(["000300.SH"])

    assert codes == ["000001.SZ", "600519.SH"]
