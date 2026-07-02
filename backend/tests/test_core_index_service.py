from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import pytest

from app.data.analytics.core_index import get_core_index_constituent_codes


@pytest.mark.asyncio
async def test_get_core_index_constituent_codes_raises_without_tushare_token():
    with patch(
        "app.data.analytics.core_index.TushareIngestor.get_pro_client",
        new_callable=AsyncMock,
        side_effect=ValueError("Tushare token is not configured"),
    ):
        with pytest.raises(ValueError, match="Tushare token is not configured"):
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

    with patch(
        "app.data.analytics.core_index.TushareIngestor.get_pro_client",
        new_callable=AsyncMock,
        return_value=mock_pro,
    ):
        codes = await get_core_index_constituent_codes(["000300.SH"])

    assert codes == ["000001.SZ", "600519.SH"]
