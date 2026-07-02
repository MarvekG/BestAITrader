from decimal import Decimal
from uuid import uuid4

import pytest

from app.models.order import Order
from app.models.account import Account
from app.tasks import pending_order_match_scheduler as scheduler


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _PendingOrderDB:
    def __init__(self, order):
        self._order = order

    async def execute(self, _statement):
        return _ScalarResult(self._order)


@pytest.mark.asyncio
async def test_match_pending_order_awaits_market_price(monkeypatch):
    order = Order(
        order_id=uuid4(),
        account_id=uuid4(),
        stock_code="000001.SZ",
        action="buy",
        order_type="limit",
        price=Decimal("10.00"),
        shares=100,
        filled_shares=0,
        status="pending",
    )

    async def _resolve_price(*_args, **_kwargs):
        return 10.5

    monkeypatch.setattr(scheduler, "_resolve_order_price", _resolve_price)

    result = await scheduler._match_pending_order(_PendingOrderDB(order), order.order_id)

    assert result["success"] is True
    assert result["matched"] is False
    assert result["reason"] == "limit_price_not_triggered"
    assert result["latest_price"] == 10.5


def test_scheduler_account_total_assets_recompute_includes_frozen_cash():
    account = Account(
        available_cash=Decimal("100.00"),
        frozen_cash=Decimal("20.00"),
        market_value=Decimal("30.00"),
    )

    scheduler._recompute_account_total_assets(account)

    assert account.total_assets == Decimal("150.00")
