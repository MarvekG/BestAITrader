from unittest.mock import AsyncMock, patch

import pytest

from app.api.endpoints.data import get_ai_context


@pytest.mark.asyncio
async def test_get_ai_context_uses_new_service_and_returns_time_layers():
    mock_context = {
        "metadata": {"status": "available", "stock_code": "000001.SZ"},
        "realtime": {"status": "available"},
        "snapshot": {"status": "available"},
        "history": {"status": "missing"},
        "signals": {"status": "available"},
        "events": {"status": "missing"},
    }

    with patch("app.api.endpoints.data.AIContextService") as MockService:
        mock_service = MockService.return_value
        mock_service.build = AsyncMock(return_value=mock_context)

        result = await get_ai_context("000001.SZ")

        assert result == mock_context
        mock_service.build.assert_awaited_once_with("000001.SZ")
