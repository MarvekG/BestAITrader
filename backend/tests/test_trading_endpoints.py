from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.endpoints.trading import get_trade_record, get_trade_records


def _fake_user():
    return SimpleNamespace(id=1)


@pytest.mark.asyncio
async def test_get_trade_record_returns_gross_turnover_and_net_amount(monkeypatch):
    record = SimpleNamespace(
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

    async def _get_owned_trade_record(_trade_id, _current_user):
        return record

    async def _get_stock_name(_stock_code):
        return "Ping An Bank"

    monkeypatch.setattr("app.api.endpoints.trading.get_owned_trade_record", _get_owned_trade_record)
    monkeypatch.setattr("app.api.endpoints.trading.get_stock_name", _get_stock_name)

    result = await get_trade_record(record.trade_id, current_user=_fake_user())

    assert result["turnover"] == 1000.0
    assert result["net_amount"] == 1005.02


@pytest.mark.asyncio
async def test_get_trade_records_returns_gross_turnover_and_net_amount(monkeypatch):
    session_id = uuid4()
    account_id = uuid4()
    record = SimpleNamespace(
        trade_id=uuid4(),
        order_id=uuid4(),
        session_id=session_id,
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

    async def _get_owned_session(_session_id, _current_user):
        return SimpleNamespace(session_id=_session_id, user_id=1)

    async def _get_current_user_account(_current_user):
        return SimpleNamespace(account_id=account_id)

    async def _list_owned_session_trades(_session_id, _account_id, *, skip, limit):
        assert _session_id == session_id
        assert _account_id == account_id
        assert skip == 0
        assert limit == 100
        return [(record, "Ping An Bank")]

    monkeypatch.setattr("app.api.endpoints.trading.get_owned_session", _get_owned_session)
    monkeypatch.setattr("app.api.endpoints.trading.get_current_user_account", _get_current_user_account)
    monkeypatch.setattr("app.api.endpoints.trading.list_owned_session_trades", _list_owned_session_trades)

    result = await get_trade_records(session_id, current_user=_fake_user())

    assert result[0]["turnover"] == 1100.0
    assert result[0]["net_amount"] == 1093.88
