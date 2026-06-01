from unittest.mock import MagicMock
from uuid import uuid4

from app.crud.order import crud_order
from app.models.order import Order


def test_cancel_order_rejects_partial_order(monkeypatch):
    db = MagicMock()
    order = Order(order_id=uuid4(), status="partial")
    monkeypatch.setattr(crud_order, "get_by_id", lambda _db, _order_id: order)

    result = crud_order.cancel_order(db, uuid4())

    assert result["success"] is False
    assert result["order"] is order
    assert order.status == "partial"
    db.commit.assert_not_called()
