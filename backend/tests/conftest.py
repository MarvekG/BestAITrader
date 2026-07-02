import os
import sqlite3
import sys
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import psycopg2
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# 必须在导入 app 任何模块之前设置，避免 Settings 校验失败。
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("FIRST_SUPERUSER_PASSWORD", "test-password-for-pytest")


_ORIGINAL_PSYCOPG2_CONNECT = psycopg2.connect


def _fail_on_postgres_host_connect(*args, **kwargs):
    dsn = str(args[0]) if args else str(kwargs.get("dsn", ""))
    host = str(kwargs.get("host", ""))
    if "host=postgres" in dsn or host == "postgres":
        raise AssertionError("Tests must not connect to postgres host; use sqlite_async_test_engine instead")
    return _ORIGINAL_PSYCOPG2_CONNECT(*args, **kwargs)


psycopg2.connect = _fail_on_postgres_host_connect


sys.modules.setdefault("tushare", MagicMock())
sys.modules.setdefault("tushare.pro", MagicMock())
sys.modules.setdefault("tushare.pro.client", MagicMock())


def _patch_app_startup_side_effects() -> None:
    startup_db_module = import_module("app.core.startup_db")
    refresh_scheduler_module = import_module("app.data.refresh_scheduler")
    async_scheduler_module = import_module("app.tasks.async_scheduler")
    task_manager_module = import_module("app.tasks.task_manager")
    redis_module = import_module("app.core.redis_client")

    startup_db_module.initialize_database = AsyncMock(return_value=None)
    startup_db_module.reset_active_analysis_sessions = AsyncMock(return_value=0)
    task_manager_module.task_manager.cleanup_zombie_tasks = AsyncMock(return_value=0)
    refresh_scheduler_module.refresh_scheduler.start = Mock(return_value=None)
    refresh_scheduler_module.refresh_scheduler.stop = Mock(return_value=None)
    async_scheduler_module.async_task_scheduler.start = Mock(return_value=None)
    async_scheduler_module.async_task_scheduler.stop = Mock(return_value=None)
    redis_module.redis_client.init_pool = AsyncMock(return_value=None)
    redis_module.redis_client.close = AsyncMock(return_value=None)
    redis_module.redis_client.get = AsyncMock(return_value=None)
    redis_module.redis_client.set = AsyncMock(return_value=False)
    redis_module.redis_client.delete = AsyncMock(return_value=False)
    redis_module.redis_client.exists = AsyncMock(return_value=False)
    redis_module.redis_client.clear_pattern = AsyncMock(return_value=0)
    redis_module.redis_client.lpush = AsyncMock(return_value=0)
    redis_module.redis_client.lrange = AsyncMock(return_value=[])
    redis_module.redis_client.ltrim = AsyncMock(return_value=False)
    redis_module.redis_client.expire = AsyncMock(return_value=False)
    redis_module.redis_client.publish = AsyncMock(return_value=0)
    redis_module.redis_client.redis = Mock()


def _patch_llm_for_tests() -> None:
    llm_endpoint_module = import_module("app.api.endpoints.llm")
    llm_usage_module = import_module("app.crud.llm_usage_log")

    async def _mock_request_llm_completion(
        *,
        messages,
        temperature=0.7,
        max_tokens=2000,
        response_format=None,
        extra_body=None,
        role="generic",
    ):
        if response_format and response_format.get("type") == "json_object":
            return {
                "content": (
                    '{"decision":"hold","recommendation":"hold","reason":"Mocked response","confidence":0.5,'
                    '"analysis":"Mocked analysis.","report":"Mocked report.","score":50}'
                ),
                "raw_response": SimpleNamespace(),
            }
        return {
            "content": "Mocked LLM Response",
            "raw_response": SimpleNamespace(),
        }

    async def _mock_record_llm_usage(*args, **kwargs):
        return None

    llm_endpoint_module._request_llm_completion = _mock_request_llm_completion
    llm_usage_module.record_llm_usage = _mock_record_llm_usage

    for module_name in [
        "app.ai.llm_engine.agents.base",
    ]:
        try:
            imported = import_module(module_name)
        except Exception:
            continue
        if hasattr(imported, "record_llm_usage"):
            imported.record_llm_usage = _mock_record_llm_usage


class _TestDataIngestionService:
    def __init__(self):
        self.write_dataframe = AsyncMock(return_value=True)


def _patch_ingestor_import_side_effects() -> None:
    tushare_module = import_module("app.data.ingestors.plugins.tushare_ingestor")
    tushare_module.DataIngestionService = _TestDataIngestionService
    tushare_module.ts.pro_api = Mock(return_value=Mock())


def _sqlite_test_tables():
    from app.models.account import Account
    from app.models.account_equity_snapshot import AccountEquitySnapshot
    from app.ai.stock_picker.interactive_research.models import (
        InteractiveResearchMessage,
        InteractiveResearchRun,
    )
    from app.models.async_task import AsyncTask
    from app.models.data_storage import (
        IndexDaily,
        KlineData,
        StockBasic,
        StockMargin,
        StockRealtimeMarket,
        StockValuationHistory,
    )
    from app.models.debate_message import DebateMessage
    from app.models.experience_review_event import ExperienceReviewEvent
    from app.models.experience_index import ExperienceIndex
    from app.models.llm_usage_log import LLMUsageLog
    from app.models.market_watch import MarketWatchEvent
    from app.models.order import Order
    from app.models.pm_decision import PMDecisionRecord
    from app.models.position import Position
    from app.models.session import Session
    from app.models.stock_indicators import StockIndicators
    from app.models.stock_warehouse import StockWarehouse
    from app.models.system_setting import SystemSetting
    from app.models.trade_record import TradeRecord
    from app.models.user import User

    return [
        User.__table__,
        Session.__table__,
        Account.__table__,
        AccountEquitySnapshot.__table__,
        Position.__table__,
        Order.__table__,
        PMDecisionRecord.__table__,
        TradeRecord.__table__,
        StockWarehouse.__table__,
        SystemSetting.__table__,
        AsyncTask.__table__,
        MarketWatchEvent.__table__,
        DebateMessage.__table__,
        ExperienceReviewEvent.__table__,
        ExperienceIndex.__table__,
        LLMUsageLog.__table__,
        StockBasic.__table__,
        KlineData.__table__,
        IndexDaily.__table__,
        StockRealtimeMarket.__table__,
        StockValuationHistory.__table__,
        StockMargin.__table__,
        StockIndicators.__table__,
        InteractiveResearchRun.__table__,
        InteractiveResearchMessage.__table__,
    ]


async def _clear_async_sqlite_tables(session_factory) -> None:
    tables = _sqlite_test_tables()
    async with session_factory() as db:
        for table in reversed(tables):
            await db.execute(table.delete())
        await db.commit()


def _run_async(coro):
    import asyncio

    return asyncio.run(coro)


async def create_test_user(db, *, username: str, email: str | None = None, password: str = "password123"):
    from app.crud.user import get_password_hash
    from app.models.user import User

    user = User(
        username=username,
        email=email or f"{username}@example.com",
        password_hash=get_password_hash(password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def ensure_test_account(db, user, initial_capital="1000000.00"):
    from decimal import Decimal

    from sqlalchemy import select

    from app.models.account import Account

    result = await db.execute(select(Account).where(Account.user_id == user.id))
    account = result.scalars().first()
    if account is not None:
        return account

    capital = Decimal(str(initial_capital))
    account = Account(
        user_id=user.id,
        total_assets=capital,
        initial_capital=capital,
        available_cash=capital,
        frozen_cash=Decimal("0.00"),
        market_value=Decimal("0.00"),
        total_profit_loss=Decimal("0.00"),
        profit_loss_pct=Decimal("0.00"),
        total_trades=0,
        win_rate=Decimal("0.00"),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


_patch_ingestor_import_side_effects()
_patch_app_startup_side_effects()
_patch_llm_for_tests()


@pytest.fixture(scope="session")
def sqlite_test_paths(tmp_path_factory):
    db_dir = tmp_path_factory.mktemp("sqlite-test-db")
    return SimpleNamespace(
        main=db_dir / "main.db",
        data=db_dir / "data.db",
        interactive=db_dir / "stock_picker_interactive.db",
    )
@pytest.fixture(scope="session")
def sqlite_async_test_engine(sqlite_test_paths):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{sqlite_test_paths.main}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        try:
            cursor.execute(f"ATTACH DATABASE '{sqlite_test_paths.data}' AS data")
        except sqlite3.OperationalError as exc:
            if "already in use" not in str(exc):
                raise
        try:
            cursor.execute(f"ATTACH DATABASE '{sqlite_test_paths.interactive}' AS stock_picker_interactive")
        except sqlite3.OperationalError as exc:
            if "already in use" not in str(exc):
                raise
        finally:
            cursor.close()

    yield engine
    _run_async(engine.dispose())
@pytest.fixture(scope="session", autouse=True)
def sqlite_async_session_factory(sqlite_async_test_engine):
    return async_sessionmaker(
        sqlite_async_test_engine,
        expire_on_commit=False,
        autoflush=False,
    )
@pytest.fixture(scope="session", autouse=True)
def sqlite_test_schema(sqlite_async_test_engine):
    from app.core.database import Base

    tables = _sqlite_test_tables()

    async def _create_async_schema():
        async with sqlite_async_test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all, tables=tables)

    _run_async(_create_async_schema())
    yield

    async def _drop_async_schema():
        async with sqlite_async_test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=tables)

    _run_async(_drop_async_schema())


@pytest.fixture
def test_db(sqlite_test_schema, sqlite_async_session_factory):
    from app.core.config import settings
    import app.core.database as db_module
    from app.core.data_source_config_cache import invalidate_data_source_config_cache
    from app.core.database import get_async_db

    original_system_language = settings.SYSTEM_LANGUAGE
    original_async_session_local = db_module.AsyncSessionLocal

    from app.main import app
    async def override_get_async_db():
        async with sqlite_async_session_factory() as db:
            yield db

    db_module.AsyncSessionLocal = sqlite_async_session_factory

    _run_async(_clear_async_sqlite_tables(sqlite_async_session_factory))
    invalidate_data_source_config_cache()
    app.dependency_overrides[get_async_db] = override_get_async_db

    try:
        yield sqlite_async_session_factory
    finally:
        app.dependency_overrides.pop(get_async_db, None)
        db_module.AsyncSessionLocal = original_async_session_local
        settings.SYSTEM_LANGUAGE = original_system_language
        _run_async(_clear_async_sqlite_tables(sqlite_async_session_factory))
        invalidate_data_source_config_cache()


@pytest_asyncio.fixture
async def async_db_session(test_db):
    async with test_db() as db:
        yield db


@pytest.fixture
def run_async():
    return _run_async


@pytest.fixture
def async_create_user():
    return create_test_user


@pytest.fixture
def async_ensure_account():
    return ensure_test_account


@pytest.fixture
def client(test_db):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client, test_db):
    import uuid

    from app.crud.user import get_password_hash
    from app.models.user import User

    username = f"test_{uuid.uuid4().hex[:8]}"
    password = "password123"

    async def _create_user():
        async with test_db() as db:
            db.add(
                User(
                    username=username,
                    email=f"{username}@example.com",
                    password_hash=get_password_hash(password),
                )
            )
            await db.commit()

    _run_async(_create_user())
    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": username,
            "password": password,
        },
    )

    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
