from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.endpoints.trading import get_trade_record, get_trade_records


class _EndpointQuery:
    def __init__(self, *, first_result=None, all_result=None, scalar_result=None):
        self._first_result = first_result
        self._all_result = all_result or []
        self._scalar_result = scalar_result

    def filter(self, *_args, **_kwargs):
        return self

    def outerjoin(self, *_args, **_kwargs):
        return self

    def order_by(self, *_args, **_kwargs):
        return self

    def offset(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def first(self):
        return self._first_result

    def all(self):
        return self._all_result

    def scalar(self):
        return self._scalar_result


class _EndpointDB:
    def __init__(self, queries):
        self._queries = list(queries)

    def query(self, *_args, **_kwargs):
        return self._queries.pop(0)


def _fake_user(account_id):
    return SimpleNamespace(id=1, account=SimpleNamespace(account_id=account_id))


@pytest.mark.asyncio
async def test_get_trade_record_returns_gross_turnover_and_net_amount():
    record = SimpleNamespace(
        account_id=uuid4(),
        trade_id=uuid4(),
        order_id=uuid4(),
        session_id=uuid4(),
        stock_code="000001.SZ",
        action="buy",
        fill_price=10.0,
        quantity=100,
        commission=5.0,
        stamp_duty=0.0,
        transfer_fee=0.02,
        total_fees=5.02,
        net_amount=1005.02,
        trade_time=SimpleNamespace(isoformat=lambda: "2026-03-23T12:00:00"),
    )
    db = _EndpointDB([
        _EndpointQuery(first_result=record),
        _EndpointQuery(scalar_result="Ping An Bank"),
    ])

    result = await get_trade_record(record.trade_id, current_user=_fake_user(record.account_id), db=db)

    assert result["turnover"] == 1000.0
    assert result["net_amount"] == 1005.02


@pytest.mark.asyncio
async def test_get_trade_records_returns_gross_turnover_and_net_amount():
    record = SimpleNamespace(
        account_id=uuid4(),
        trade_id=uuid4(),
        order_id=uuid4(),
        session_id=uuid4(),
        stock_code="000001.SZ",
        action="sell",
        fill_price=11.0,
        quantity=100,
        commission=5.0,
        stamp_duty=1.1,
        transfer_fee=0.02,
        total_fees=6.12,
        net_amount=1093.88,
        trade_time=SimpleNamespace(isoformat=lambda: "2026-03-23T12:00:00"),
    )
    db = _EndpointDB([
        _EndpointQuery(first_result=SimpleNamespace(session_id=record.session_id, user_id=1)),
        _EndpointQuery(all_result=[(record, "Ping An Bank")]),
    ])

    result = await get_trade_records(record.session_id, current_user=_fake_user(record.account_id), db=db)

    assert result[0]["turnover"] == 1100.0
    assert result[0]["net_amount"] == 1093.88
