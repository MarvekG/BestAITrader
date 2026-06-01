import pytest
from unittest.mock import MagicMock
from sqlalchemy.orm import Session
# Import the function directly
from app.api.endpoints.data import get_db_stocks

@pytest.mark.asyncio
async def test_get_db_stocks_keys_transformation():
    # Mock database session
    mock_db = MagicMock(spec=Session)
    
    # Mock query chain
    mock_query = MagicMock()
    mock_filter = MagicMock()
    
    # Setup chain: db.query() -> filter() -> count() / offset() -> limit() -> all()
    mock_db.query.return_value = mock_query
    # query.filter returns new query object (or same)
    mock_query.filter.return_value = mock_filter
    # count
    mock_filter.count.return_value = 1
    mock_query.count.return_value = 1
    
    # offset -> limit -> all
    mock_offset = MagicMock()
    mock_limit = MagicMock()
    
    mock_query.offset.return_value = mock_offset
    mock_filter.offset.return_value = mock_offset
    
    mock_offset.limit.return_value = mock_limit
    
    # Mock item
    mock_item = MagicMock()
    mock_item.__dict__ = {"stock_code": "000001", "name": "Test Stock", "_sa_instance_state": "ignored"}
    
    mock_limit.all.return_value = [mock_item]

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

