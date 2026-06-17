import os
import sqlite3
import sys
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# 必须在导入 app 任何模块之前设置，避免 Settings 校验失败。
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
os.environ.setdefault("FIRST_SUPERUSER_PASSWORD", "test-password-for-pytest")


sys.modules.setdefault("tushare", MagicMock())
sys.modules.setdefault("tushare.pro", MagicMock())
sys.modules.setdefault("tushare.pro.client", MagicMock())


def _patch_app_startup_side_effects() -> None:
    init_db_module = import_module("app.core.init_db")
    refresh_scheduler_module = import_module("app.data.refresh_scheduler")
    async_scheduler_module = import_module("app.tasks.async_scheduler")
    redis_module = import_module("app.core.redis_client")

    init_db_module.init_db = lambda db: None
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

    def _mock_record_llm_usage(*args, **kwargs):
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
        self.write_dataframe = Mock(return_value=True)


def _patch_ingestor_import_side_effects() -> None:
    tushare_module = import_module("app.data.ingestors.plugins.tushare_ingestor")
    tushare_module.DataIngestionService = _TestDataIngestionService
    tushare_module.ts.pro_api = Mock(return_value=Mock())


def _sqlite_test_tables():
    from app.models.account import Account
    from app.models.account_equity_snapshot import AccountEquitySnapshot
    from app.ai.stock_picker.models import (
        StockSelectionCandidate,
        StockSelectionEvent,
        StockSelectionRun,
    )
    from app.ai.stock_picker.interactive_research.models import (
        InteractiveResearchMessage,
        InteractiveResearchRun,
    )
    from app.models.async_task import AsyncTask
    from app.models.data_storage import (
        IndexDaily,
        KlineData,
        StockBasic,
        StockRealtimeMarket,
        StockValuationHistory,
    )
    from app.models.debate_message import DebateMessage
    from app.models.experience_review_event import ExperienceReviewEvent
    from app.models.experience_index import ExperienceIndex
    from app.models.market_watch import MarketWatchEvent
    from app.models.order import Order
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
        TradeRecord.__table__,
        StockWarehouse.__table__,
        SystemSetting.__table__,
        AsyncTask.__table__,
        MarketWatchEvent.__table__,
        DebateMessage.__table__,
        ExperienceReviewEvent.__table__,
        ExperienceIndex.__table__,
        StockBasic.__table__,
        KlineData.__table__,
        IndexDaily.__table__,
        StockRealtimeMarket.__table__,
        StockValuationHistory.__table__,
        StockIndicators.__table__,
        StockSelectionRun.__table__,
        StockSelectionEvent.__table__,
        StockSelectionCandidate.__table__,
        InteractiveResearchRun.__table__,
        InteractiveResearchMessage.__table__,
    ]


def _clear_sqlite_tables(session_factory) -> None:
    tables = _sqlite_test_tables()
    db = session_factory()
    try:
        for table in reversed(tables):
            db.execute(table.delete())
        db.commit()
    finally:
        db.close()


_patch_ingestor_import_side_effects()
_patch_app_startup_side_effects()
_patch_llm_for_tests()


@pytest.fixture(scope="session")
def sqlite_test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        try:
            cursor.execute("ATTACH DATABASE ':memory:' AS data")
        except sqlite3.OperationalError as exc:
            if "already in use" not in str(exc):
                raise
        try:
            cursor.execute("ATTACH DATABASE ':memory:' AS stock_picker")
        except sqlite3.OperationalError as exc:
            if "already in use" not in str(exc):
                raise
        try:
            cursor.execute("ATTACH DATABASE ':memory:' AS stock_picker_interactive")
        except sqlite3.OperationalError as exc:
            if "already in use" not in str(exc):
                raise
        finally:
            cursor.close()

    return engine


@pytest.fixture(scope="session")
def sqlite_session_factory(sqlite_test_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=sqlite_test_engine)


@pytest.fixture(scope="session", autouse=True)
def sqlite_test_schema(sqlite_test_engine):
    from app.core.database import Base

    tables = _sqlite_test_tables()
    Base.metadata.create_all(bind=sqlite_test_engine, tables=tables)
    yield
    Base.metadata.drop_all(bind=sqlite_test_engine, tables=tables)


@pytest.fixture
def test_db(sqlite_test_schema, sqlite_session_factory):
    from app.core.config import settings
    import app.core.database as db_module
    from app.core.database import get_db

    original_system_language = settings.SYSTEM_LANGUAGE
    original_db_session_local = db_module.SessionLocal
    db_module.SessionLocal = sqlite_session_factory

    from app.main import app
    import app.main as app_main_module
    import app.ai.experience.service as experience_service_module
    import app.ai.stock_picker.service as stock_picker_service_module
    import app.tasks.async_task_runner as async_task_runner_module

    original_main_session_local = app_main_module.SessionLocal
    app_main_module.SessionLocal = sqlite_session_factory
    original_async_task_runner_session_local = async_task_runner_module.SessionLocal
    async_task_runner_module.SessionLocal = sqlite_session_factory
    original_experience_session_local = experience_service_module.SessionLocal
    experience_service_module.SessionLocal = sqlite_session_factory
    original_stock_picker_session_local = stock_picker_service_module.SessionLocal
    stock_picker_service_module.SessionLocal = sqlite_session_factory

    def override_get_db():
        db = sqlite_session_factory()
        try:
            yield db
        finally:
            db.close()

    _clear_sqlite_tables(sqlite_session_factory)
    app.dependency_overrides[get_db] = override_get_db

    try:
        yield sqlite_session_factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        stock_picker_service_module.SessionLocal = original_stock_picker_session_local
        experience_service_module.SessionLocal = original_experience_session_local
        async_task_runner_module.SessionLocal = original_async_task_runner_session_local
        app_main_module.SessionLocal = original_main_session_local
        db_module.SessionLocal = original_db_session_local
        settings.SYSTEM_LANGUAGE = original_system_language
        _clear_sqlite_tables(sqlite_session_factory)


@pytest.fixture
def db_session(test_db):
    db = test_db()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client(test_db):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client, db_session):
    import uuid

    from app.crud.user import create_user
    from app.schemas.user import UserCreate

    username = f"test_{uuid.uuid4().hex[:8]}"
    password = "password123"

    create_user(
        db_session,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password=password,
        ),
    )
    response = client.post(
        "/api/v1/auth/login",
        data={
            "username": username,
            "password": password,
        },
    )

    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
