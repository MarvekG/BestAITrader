import pytest
from unittest.mock import AsyncMock, MagicMock
# Import the function directly
from app.api.endpoints.data import get_db_stocks

@pytest.mark.asyncio
async def test_get_db_stocks_keys_transformation():
    # Mock async database session
    mock_db = MagicMock()

    # Mock item
    mock_item = MagicMock()
    mock_item.__dict__ = {"stock_code": "000001", "name": "Test Stock", "_sa_instance_state": "ignored"}

    mock_total_result = MagicMock()
    mock_total_result.scalar_one.return_value = 1
    mock_items_result = MagicMock()
    mock_items_result.scalars.return_value.all.return_value = [mock_item]
    mock_db.execute = AsyncMock(side_effect=[mock_total_result, mock_items_result])

    # Call the function directly
    # Note: get_db_stocks is async def
    result = await get_db_stocks(stock_code="000001", skip=0, limit=10, db=mock_db)
    
    # Verify result
    assert result["total"] == 1
    items = result["items"]
    assert len(items) == 1
    first_item = items[0]
    
    # Check transformation
    assert "stock_basic.stock_code" in first_item
    assert first_item["stock_basic.stock_code"] == "000001"
    assert "stock_basic.name" in first_item
    assert first_item["stock_basic.name"] == "Test Stock"
    
    # Ensure original non-prefixed keys are NOT present (since I created a new dict)
    assert "stock_code" not in first_item
