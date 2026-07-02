from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.crud.order import crud_order
from app.models.order import Order


@pytest.mark.asyncio
async def test_cancel_order_rejects_filled_order(monkeypatch):
    db = AsyncMock()
    order = Order(order_id=uuid4(), status="filled")

    async def _get_by_id(_db, _order_id):
        return order

    monkeypatch.setattr(crud_order, "get_by_id", _get_by_id)

    result = await crud_order.cancel_order(db, uuid4())

    assert result["success"] is False
    assert result["order"] is order
    assert order.status == "filled"
    db.commit.assert_not_awaited()
