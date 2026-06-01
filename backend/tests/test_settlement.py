from datetime import datetime, timedelta
from types import SimpleNamespace

from app.tasks.settlement import execute_daily_settlement


class _SettlementQuery:
    def __init__(self, positions):
        self.positions = positions

    def all(self):
        return self.positions


class _SettlementDB:
    def __init__(self, positions):
        self.positions = positions
        self.committed = False
        self.rolled_back = False

    def query(self, _model):
        return _SettlementQuery(self.positions)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _SettlementContext:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self.db

    def __exit__(self, exc_type, exc, tb):
        return False


def test_execute_daily_settlement_clamps_available_shares(monkeypatch):
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    position = SimpleNamespace(
        total_shares=100,
        available_shares=0,
        frozen_shares=100,
        purchase_details={"ledger": [{"time": yesterday, "shares": 200, "price": 10.0}]},
    )
    db = _SettlementDB([position])

    monkeypatch.setattr("app.tasks.settlement.SessionLocal", lambda: _SettlementContext(db))

    execute_daily_settlement()

    assert db.committed is True
    assert position.available_shares == 100
    assert position.frozen_shares == 0
