from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.models.account import Account
from app.models.account_equity_snapshot import AccountEquitySnapshot
from app.models.user import User


def test_performance_summary_returns_latest_snapshot(client, test_db, run_async, auth_headers) -> None:
    """绩效摘要接口应返回当前用户最新账户快照。"""
    async def _seed_data() -> None:
        async with test_db() as db:
            user = (await db.execute(select(User).where(User.username.like("test_%")))).scalars().first()
            account = Account(
                user_id=user.id,
                total_assets=Decimal("1020000.0000"),
                available_cash=Decimal("500000.0000"),
                frozen_cash=Decimal("0.0000"),
                market_value=Decimal("520000.0000"),
                initial_capital=Decimal("1000000.0000"),
                total_profit_loss=Decimal("20000.0000"),
                total_trades=3,
            )
            db.add(account)
            await db.flush()
            db.add(
                AccountEquitySnapshot(
                    user_id=user.id,
                    account_id=account.account_id,
                    snapshot_date=date(2026, 5, 22),
                    total_assets=Decimal("1020000.0000"),
                    available_cash=Decimal("500000.0000"),
                    market_value=Decimal("520000.0000"),
                    position_count=2,
                    daily_return=Decimal("0.01000000"),
                    cumulative_return=Decimal("0.02000000"),
                    benchmark_code="000300.SH",
                    benchmark_close=Decimal("4040.000000"),
                    benchmark_daily_return=Decimal("0.00500000"),
                    benchmark_cumulative_return=Decimal("0.01000000"),
                    excess_return=Decimal("0.01000000"),
                    max_drawdown=Decimal("-0.03000000"),
                )
            )
            await db.commit()

    run_async(_seed_data())

    response = client.get("/api/v1/performance/summary", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert "total_assets" not in data
    assert data["available_cash"] == 500000.0
    assert data["market_value"] == 520000.0
    assert data["position_count"] == 2
    assert data["cumulative_return"] == 0.02
    assert data["benchmark_cumulative_return"] == 0.01
    assert data["excess_return"] == 0.01
    assert data["max_drawdown"] == -0.03
    assert data["total_trades"] == 3
    assert data["snapshot_date"] == "2026-05-22"


def test_performance_equity_curve_returns_snapshots(client, test_db, run_async, auth_headers) -> None:
    """净值曲线接口应按日期返回账户和基准曲线。"""
    async def _seed_data() -> None:
        async with test_db() as db:
            user = (await db.execute(select(User).where(User.username.like("test_%")))).scalars().first()
            account = Account(
                user_id=user.id,
                total_assets=Decimal("1020000.0000"),
                available_cash=Decimal("500000.0000"),
                frozen_cash=Decimal("0.0000"),
                market_value=Decimal("520000.0000"),
                initial_capital=Decimal("1000000.0000"),
                total_profit_loss=Decimal("20000.0000"),
                total_trades=3,
            )
            db.add(account)
            await db.flush()
            for snapshot_day, cumulative, benchmark in [
                (date(2026, 5, 21), Decimal("0.01000000"), Decimal("0.00500000")),
                (date(2026, 5, 22), Decimal("0.02000000"), Decimal("0.01000000")),
            ]:
                db.add(
                    AccountEquitySnapshot(
                        user_id=user.id,
                        account_id=account.account_id,
                        snapshot_date=snapshot_day,
                        total_assets=Decimal("1000000.0000") * (Decimal("1") + cumulative),
                        available_cash=Decimal("500000.0000"),
                        market_value=Decimal("500000.0000"),
                        position_count=2,
                        daily_return=Decimal("0.01000000"),
                        cumulative_return=cumulative,
                        benchmark_code="000300.SH",
                        benchmark_close=Decimal("4000.000000"),
                        benchmark_daily_return=Decimal("0.00500000"),
                        benchmark_cumulative_return=benchmark,
                        excess_return=cumulative - benchmark,
                        max_drawdown=Decimal("0.00000000"),
                    )
                )
            await db.commit()

    run_async(_seed_data())

    response = client.get("/api/v1/performance/equity-curve", headers=auth_headers)

    assert response.status_code == 200
    data = response.json()
    assert data["benchmark_code"] == "000300.SH"
    assert [item["snapshot_date"] for item in data["items"]] == ["2026-05-21", "2026-05-22"]
    assert all("total_assets" not in item for item in data["items"])
    assert data["items"][0]["cumulative_return"] == 0.01
    assert data["items"][1]["benchmark_cumulative_return"] == 0.01
