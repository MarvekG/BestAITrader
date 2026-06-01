from datetime import date
from decimal import Decimal

from app.core.database import Base
from app.models.account import Account
from app.models.data_storage import IndexDaily
from app.models.position import Position
from app.models.user import User
from app.performance.service import create_account_equity_snapshot


def test_account_equity_snapshot_model_is_registered() -> None:
    """账户净值快照模型应注册到 SQLAlchemy 元数据。"""
    assert "account_equity_snapshots" in Base.metadata.tables


def test_create_first_snapshot_uses_zero_returns(db_session) -> None:
    """首个账户快照应以 0 作为当日收益、累计收益和回撤。"""
    user = User(id=101, username="perf_user", email="perf@example.com", password_hash="hash", is_active=True)
    account = Account(
        user_id=101,
        total_assets=Decimal("1000000.0000"),
        available_cash=Decimal("800000.0000"),
        frozen_cash=Decimal("0.0000"),
        market_value=Decimal("200000.0000"),
        initial_capital=Decimal("1000000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=0,
    )
    db_session.add(user)
    db_session.add(account)
    db_session.flush()
    db_session.add(
        Position(
            account_id=account.account_id,
            stock_code="000001.SZ",
            total_shares=100,
            available_shares=100,
            frozen_shares=0,
            avg_cost=Decimal("10.0000"),
            current_price=Decimal("11.0000"),
            market_value=Decimal("1100.0000"),
            profit_loss=Decimal("100.0000"),
            profit_loss_pct=Decimal("0.1000"),
            purchase_details={},
        )
    )
    db_session.add(
        IndexDaily(
            index_code="000300.SH",
            trade_date=date(2026, 5, 20),
            close=4000.0,
        )
    )
    db_session.commit()

    snapshot = create_account_equity_snapshot(
        db_session,
        account=account,
        snapshot_date=date(2026, 5, 20),
    )

    assert snapshot.total_assets == Decimal("1000000.0000")
    assert snapshot.available_cash == Decimal("800000.0000")
    assert snapshot.market_value == Decimal("200000.0000")
    assert snapshot.position_count == 1
    assert snapshot.daily_return == Decimal("0.00000000")
    assert snapshot.cumulative_return == Decimal("0.00000000")
    assert snapshot.benchmark_close == Decimal("4000.000000")
    assert snapshot.benchmark_daily_return == Decimal("0.00000000")
    assert snapshot.benchmark_cumulative_return == Decimal("0.00000000")
    assert snapshot.excess_return == Decimal("0.00000000")
    assert snapshot.max_drawdown == Decimal("0.00000000")


def test_create_followup_snapshot_calculates_returns_and_drawdown(db_session) -> None:
    """后续快照应计算账户收益、基准收益、超额收益和最大回撤。"""
    user = User(id=102, username="perf_user_2", email="perf2@example.com", password_hash="hash", is_active=True)
    account = Account(
        user_id=102,
        total_assets=Decimal("1000000.0000"),
        available_cash=Decimal("1000000.0000"),
        frozen_cash=Decimal("0.0000"),
        market_value=Decimal("0.0000"),
        initial_capital=Decimal("1000000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=0,
    )
    db_session.add(user)
    db_session.add(account)
    db_session.flush()
    db_session.add_all([
        IndexDaily(index_code="000300.SH", trade_date=date(2026, 5, 20), close=4000.0),
        IndexDaily(index_code="000300.SH", trade_date=date(2026, 5, 21), close=4080.0),
        IndexDaily(index_code="000300.SH", trade_date=date(2026, 5, 22), close=4040.0),
    ])
    db_session.commit()

    create_account_equity_snapshot(db_session, account=account, snapshot_date=date(2026, 5, 20))
    account.total_assets = Decimal("1100000.0000")
    account.available_cash = Decimal("1100000.0000")
    db_session.add(account)
    db_session.commit()
    create_account_equity_snapshot(db_session, account=account, snapshot_date=date(2026, 5, 21))
    account.total_assets = Decimal("990000.0000")
    account.available_cash = Decimal("990000.0000")
    db_session.add(account)
    db_session.commit()

    snapshot = create_account_equity_snapshot(db_session, account=account, snapshot_date=date(2026, 5, 22))

    assert snapshot.daily_return == Decimal("-0.10000000")
    assert snapshot.cumulative_return == Decimal("-0.01000000")
    assert snapshot.benchmark_daily_return == Decimal("-0.00980392")
    assert snapshot.benchmark_cumulative_return == Decimal("0.01000000")
    assert snapshot.excess_return == Decimal("-0.02000000")
    assert snapshot.max_drawdown == Decimal("-0.10000000")


def test_recreating_same_day_snapshot_updates_without_duplicate_peak(db_session) -> None:
    """重跑同日快照不应让旧的同日资产值影响最大回撤。"""
    user = User(id=103, username="perf_user_3", email="perf3@example.com", password_hash="hash", is_active=True)
    account = Account(
        user_id=103,
        total_assets=Decimal("1000000.0000"),
        available_cash=Decimal("1000000.0000"),
        frozen_cash=Decimal("0.0000"),
        market_value=Decimal("0.0000"),
        initial_capital=Decimal("1000000.0000"),
        total_profit_loss=Decimal("0.0000"),
        total_trades=0,
    )
    db_session.add(user)
    db_session.add(account)
    db_session.flush()
    db_session.add_all([
        IndexDaily(index_code="000300.SH", trade_date=date(2026, 5, 20), close=4000.0),
        IndexDaily(index_code="000300.SH", trade_date=date(2026, 5, 21), close=4080.0),
    ])
    db_session.commit()

    create_account_equity_snapshot(db_session, account=account, snapshot_date=date(2026, 5, 20))
    account.total_assets = Decimal("1100000.0000")
    account.available_cash = Decimal("1100000.0000")
    db_session.add(account)
    db_session.commit()
    create_account_equity_snapshot(db_session, account=account, snapshot_date=date(2026, 5, 21))
    account.total_assets = Decimal("900000.0000")
    account.available_cash = Decimal("900000.0000")
    db_session.add(account)
    db_session.commit()

    snapshot = create_account_equity_snapshot(db_session, account=account, snapshot_date=date(2026, 5, 21))

    assert snapshot.daily_return == Decimal("-0.10000000")
    assert snapshot.cumulative_return == Decimal("-0.10000000")
    assert snapshot.max_drawdown == Decimal("-0.10000000")
    assert db_session.query(IndexDaily).count() == 2
