from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.models.position import Position
from app.models.async_task import AsyncTask
from app.models.session import Session
from app.models.stock_warehouse import StockWarehouse
from app.models.system_setting import SystemSetting
from app.trading.discipline_service import scan_position_disciplines
from app.trading import discipline_service
from app.trading.discipline_settings import PositionDisciplineSettingsResponse


async def _create_user(db, async_create_user, async_ensure_account):
    user = await async_create_user(
        db,
        username="discipline_service",
        email="discipline_service@example.com",
        password="password123",
    )
    account = await async_ensure_account(db, user)
    return user, account


async def _create_position(db, account, *, pm_session_id=None):
    position = Position(
        account_id=account.account_id,
        stock_code="000001.SZ",
        total_shares=100,
        available_shares=100,
        frozen_shares=0,
        avg_cost=Decimal("10.00"),
        current_price=Decimal("10.00"),
        market_value=Decimal("1000.00"),
        profit_loss=Decimal("0.00"),
        profit_loss_pct=Decimal("0.0000"),
        purchase_details={},
        stop_loss=Decimal("9.50"),
        pm_session_id=pm_session_id or uuid4(),
    )
    db.add(position)
    await db.commit()
    await db.refresh(position)
    return position


async def _add_stock_warehouse(db, user_id):
    db.add(
        StockWarehouse(
            user_id=user_id,
            stock_code="000001.SZ",
            is_active=True,
            auto_analysis_trading_frequency="中长线持有 (Position Trading)",
            auto_analysis_trading_strategy="价值投资 (Value Investing)",
        )
    )
    await db.commit()


@pytest.mark.asyncio
async def test_position_discipline_debate_respects_global_concurrency_limit(
    async_db_session,
    monkeypatch,
    async_create_user,
    async_ensure_account,
):
    user, _account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    async_db_session.add_all([
        SystemSetting(key="ai_debate.max_concurrent", value=1, description="test"),
        AsyncTask(
            task_name="AI Analysis - 000001.SZ",
            task_type="ai_analysis",
            status="running",
            allow_concurrent=False,
            parameters={"stock_code": "000001.SZ"},
            user_id=user.id,
        ),
    ])
    await async_db_session.commit()
    async def _not_duplicate(**_kwargs):
        return False

    monkeypatch.setattr("app.trading.discipline_service._is_duplicate_discipline_trigger", _not_duplicate)

    result = await discipline_service._handle_position_discipline_trigger(
        user_id=user.id,
        settings=PositionDisciplineSettingsResponse(
            user_id=user.id,
            enabled=True,
            scan_non_trading_days=True,
            auto_launch_debate=True,
        ),
        item={
            "trigger": "stop_loss",
            "stock_code": "000002.SZ",
            "threshold": Decimal("9.50"),
            "latest_price": Decimal("9.40"),
            "pm_session_id": uuid4(),
        },
        debate_launcher=lambda **kwargs: None,
        background_tasks=None,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "concurrency_limit"
    count = await async_db_session.scalar(select(func.count()).select_from(AsyncTask))
    assert count == 1


@pytest.mark.asyncio
async def test_discipline_scan_skips_duplicate_latest_stock_trigger(
    async_db_session,
    monkeypatch,
    async_create_user,
    async_ensure_account,
):
    user, account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    pm_session_id = uuid4()
    await _create_position(async_db_session, account, pm_session_id=pm_session_id)
    await _add_stock_warehouse(async_db_session, user.id)
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        AsyncMock(return_value={"success": True, "latest_price": "9.40"}),
    )

    launcher_calls = []
    settings = PositionDisciplineSettingsResponse(
        user_id=user.id,
        enabled=True,
        scan_non_trading_days=True,
        auto_launch_debate=True,
    )

    first = await scan_position_disciplines(
        user.id,
        settings=settings,
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
        now=datetime(2026, 6, 18, 10, 0),
    )
    second = await scan_position_disciplines(
        user.id,
        settings=settings,
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
        now=datetime(2026, 6, 18, 10, 1),
    )

    assert first["launched_debate_count"] == 1
    assert second["launched_debate_count"] == 0
    assert second["debate_launches"][0]["status"] == "skipped"
    assert second["debate_launches"][0]["reason"] == "duplicate_discipline_trigger"
    assert len(launcher_calls) == 1
    expected_trigger = {
        "trigger_type": "stop_loss",
        "threshold": "9.5000",
        "latest_price": "9.40",
        "source_pm_session_id": str(pm_session_id),
        "source": "position_discipline",
    }
    assert launcher_calls[0]["discipline_trigger"] == expected_trigger
    assert launcher_calls[0]["sync_before_analysis"] is True

    task = (await async_db_session.execute(select(AsyncTask))).scalars().one()
    assert task.parameters["discipline_trigger"] == expected_trigger
    assert task.parameters["sync_before_analysis"] is True

    setting = (
        await async_db_session.execute(
            select(SystemSetting).where(
                SystemSetting.user_id == user.id,
                SystemSetting.key == "position_discipline.dedup_state",
            )
        )
    ).scalars().one()
    state = setting.value["stocks"]["000001.SZ"]
    assert state["trigger"] == "stop_loss"
    assert state["threshold"] == "9.5000"
    assert state["pm_session_id"] == str(pm_session_id)
    assert isinstance(state["triggered_at"], int)


@pytest.mark.asyncio
async def test_discipline_scan_marks_created_records_failed_when_scheduler_fails(
    async_db_session,
    monkeypatch,
    async_create_user,
    async_ensure_account,
):
    user, account = await _create_user(async_db_session, async_create_user, async_ensure_account)
    await _create_position(async_db_session, account)
    await _add_stock_warehouse(async_db_session, user.id)
    monkeypatch.setattr(
        "app.ai.agentic.tools._resolve_latest_stock_price",
        AsyncMock(return_value={"success": True, "latest_price": "9.40"}),
    )

    def failing_launcher(**_kwargs):
        raise RuntimeError("scheduler unavailable")

    result = await scan_position_disciplines(
        user.id,
        settings=PositionDisciplineSettingsResponse(
            user_id=user.id,
            enabled=True,
            scan_non_trading_days=True,
            auto_launch_debate=True,
        ),
        debate_launcher=failing_launcher,
        now=datetime(2026, 6, 18, 10, 0),
    )

    launch = result["debate_launches"][0]
    assert launch["status"] == "failed"
    assert launch["reason"] == "launch_failed"
    assert launch["error"] == "scheduler unavailable"

    task = (await async_db_session.execute(select(AsyncTask))).scalars().one()
    session = (await async_db_session.execute(select(Session))).scalars().one()
    assert task.status == "failed"
    assert task.error_message == "scheduler unavailable"
    assert task.completed_at is not None
    assert session.status == "failed"
    count = await async_db_session.scalar(
        select(func.count()).select_from(SystemSetting).where(SystemSetting.key == "position_discipline.dedup_state")
    )
    assert count == 0
