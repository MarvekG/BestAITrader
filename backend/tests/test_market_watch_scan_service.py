from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest

from app.ai.market_watch import service as market_watch_service
from app.ai.market_watch.schemas import (
    DebateParameters,
    MarketWatchMarkdownDocument,
    MarketWatchSettingsUpdate,
    MarketWatchSourceType,
    WatchAiDecision,
)
from app.ai.market_watch.service import scan_market_watch
from app.ai.market_watch.settings import upsert_market_watch_settings
from app.models.account import Account
from app.models.async_task import AsyncTask
from app.models.data_storage import StockBasic
from app.models.market_watch import MarketWatchEvent
from app.models.position import Position
from app.models.session import Session
from app.models.stock_warehouse import StockWarehouse
from app.models.system_setting import SystemSetting
from app.models.user import User


class FakeWatchAiGate:
    """Test double that records the Watch AI payload and returns a fixed decision."""

    def __init__(self, decisions: WatchAiDecision | list[WatchAiDecision]) -> None:
        self.decisions = decisions if isinstance(decisions, list) else [decisions]
        self.payloads: list[dict[str, Any]] = []

    async def decide(self, payload: dict[str, Any]) -> list[WatchAiDecision]:
        """Record payloads passed to Watch AI."""
        self.payloads.append(payload)
        return self.decisions


class FakeSourceDocumentFetcher:
    """Test double for configured market-watch source document rendering."""

    calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        urls: list[Any],
        source_type: MarketWatchSourceType,
    ) -> list[MarketWatchMarkdownDocument]:
        """Return one Markdown document per configured URL."""
        self.calls.append(
            {
                "urls": [getattr(url, "url", url) for url in urls],
                "source_type": source_type,
            }
        )
        return [
            _source_document(
                document_id=f"{source_type}-{index + 1}",
                source_type=source_type,
                url=getattr(url, "url", url),
            )
            for index, url in enumerate(urls)
        ]


class BlockingSourceDocumentFetcher:
    """Test double that blocks source document loads so tests can observe concurrency."""

    def __init__(self, expected_calls: int) -> None:
        self.expected_calls = expected_calls
        self.release = asyncio.Event()
        self.all_started = asyncio.Event()
        self.calls: list[dict[str, Any]] = []
        self.active_calls = 0
        self.max_active_calls = 0

    async def __call__(
        self,
        urls: list[Any],
        source_type: MarketWatchSourceType,
    ) -> list[MarketWatchMarkdownDocument]:
        """Record the call and wait until the test releases all calls."""
        self.calls.append(
            {
                "urls": [getattr(url, "url", url) for url in urls],
                "source_type": source_type,
            }
        )
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        if len(self.calls) == self.expected_calls:
            self.all_started.set()
        await self.release.wait()
        self.active_calls -= 1
        return [
            _source_document(
                document_id=f"{source_type}-{index + 1}",
                source_type=source_type,
                url=getattr(url, "url", url),
            )
            for index, url in enumerate(urls)
        ]


class RaisingWatchAiGate:
    """Test double that raises when Watch AI is invoked."""

    async def decide(self, payload: dict[str, Any]) -> list[WatchAiDecision]:
        """Raise a deterministic Watch AI failure."""
        _ = payload
        raise RuntimeError("watch ai unavailable")


@pytest.mark.asyncio
async def test_default_llm_client_leaves_watch_ai_content_for_gate_parser(monkeypatch) -> None:
    raw_content = "```json\n[]\n```"
    captured_kwargs: dict[str, Any] = {}

    async def fake_request_llm_completion(**kwargs):
        captured_kwargs.update(kwargs)
        return {"content": raw_content}

    from app.api.endpoints import llm as llm_endpoint

    monkeypatch.setattr(llm_endpoint, "_request_llm_completion", fake_request_llm_completion)

    result = await market_watch_service._DefaultLlmClient().complete_json(
        [{"role": "user", "content": "return watch ai json"}]
    )

    assert result == raw_content
    assert captured_kwargs["role"] == "market_watch"
    assert captured_kwargs["max_tokens"] == 16384
    assert captured_kwargs["model"] == "openai-compatible"
    assert "extra_body" not in captured_kwargs


def test_market_watch_llm_usage_observability_uses_dedicated_lane() -> None:
    from app.api.endpoints.llm import _llm_usage_observability_for_role

    metadata = _llm_usage_observability_for_role("market_watch")

    assert metadata["workflow"] == "market_watch"
    assert metadata["stage"] == "watch_ai_gate"
    assert metadata["call_kind"] == "agent"
    assert metadata["cache_lane"] == "market_watch"
    assert metadata["api_key_alias"] == "market_watch_llm_api_key"


@pytest.mark.asyncio
async def test_market_watch_debate_launch_respects_global_concurrency_limit(db_session, test_db, monkeypatch) -> None:
    user = User(username="market_watch_limit", email="market_watch_limit@example.com", password_hash="x")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    db_session.add_all([
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
    db_session.commit()
    monkeypatch.setattr("app.ai.market_watch.service.database_module.SessionLocal", test_db)

    response = await market_watch_service._maybe_launch_debate(
        user_id=user.id,
        settings={},
        cooldown_minutes=0,
        auto_launch_debate=True,
        allowed_stock_codes={"000002.SZ"},
        decision=WatchAiDecision(
            stock_code="000002.SZ",
            stock_name="000002.SZ",
            action="start_debate",
            confidence=1.0,
            urgency="high",
            trigger_reason="test",
            evidence_summary="test",
            debate_parameters=DebateParameters(trading_frequency="position", trading_strategy="value"),
        ),
        debate_launcher=lambda **kwargs: None,
        background_tasks=None,
    )

    assert response["status"] == "skipped"
    assert response["reason"] == "concurrency_limit"
    assert db_session.query(AsyncTask).count() == 1


def test_build_watch_ai_payload_sorts_stock_lists_for_cache_stability() -> None:
    payload = market_watch_service._build_watch_ai_payload(
        user_id=100,
        settings={},
        items=[
            {"stock_code": "300750", "stock_name": "宁德时代"},
            {"stock_code": "000001", "stock_name": "平安银行"},
            {"stock_code": "600519", "stock_name": "贵州茅台"},
        ],
        data_documents=[],
        news_documents=[],
        account_summary={},
        positions=[
            {"stock_code": "600519", "market_value": 1000.0},
            {"stock_code": "000001", "market_value": 2000.0},
        ],
    )

    assert [item["stock_code"] for item in payload["warehouse_stocks"]] == ["000001", "300750", "600519"]
    assert [item["stock_code"] for item in payload["positions"]] == ["000001", "600519"]


async def _raising_source_document_fetcher(
    urls: list[str],
    source_type: MarketWatchSourceType,
) -> list[MarketWatchMarkdownDocument]:
    """Raise a deterministic configured source fetch failure."""
    _ = urls, source_type
    raise RuntimeError("source unavailable")


@pytest.fixture(autouse=True)
def _default_market_watch_scan_time(monkeypatch) -> None:
    monkeypatch.setattr(market_watch_service, "_shanghai_now", lambda: datetime(2026, 5, 14, 10, 0))
    FakeSourceDocumentFetcher.calls = []
    monkeypatch.setattr(market_watch_service, "fetch_market_watch_documents", FakeSourceDocumentFetcher())


def _create_user(db_session, user_id: int = 100, *, configure_sources: bool = True) -> User:
    user = User(
        id=user_id,
        username=f"watch_user_{user_id}",
        email=f"watch_user_{user_id}@example.com",
        password_hash="hashed",
    )
    db_session.add(user)
    db_session.commit()
    if configure_sources:
        upsert_market_watch_settings(
            user.id,
            MarketWatchSettingsUpdate(
                data_sources=["https://example.com/data"],
                news_sources=["https://example.com/news"],
            ),
        )
    return user


def _add_stock(
    db_session,
    user_id: int,
    stock_code: str,
    stock_name: str = "Alpha Tech",
    auto_analysis_trading_frequency: str = "中长线持有 (Position Trading)",
    auto_analysis_trading_strategy: str = "价值投资 (Value Investing)",
) -> None:
    db_session.add(
        StockBasic(
            stock_code=stock_code,
            name=stock_name,
            industry="Semiconductor",
            market="SZ",
        )
    )
    db_session.add(
        StockWarehouse(
            user_id=user_id,
            stock_code=stock_code,
            is_active=True,
            auto_analysis_trading_frequency=auto_analysis_trading_frequency,
            auto_analysis_trading_strategy=auto_analysis_trading_strategy,
        )
    )
    db_session.commit()


def _add_quote(
    db_session,
    stock_code: str,
    *,
    change_percent: float = 4.2,
    change_5min: float = 1.3,
    volume_ratio: float = 2.8,
    turnover_rate: float = 6.1,
    main_net_inflow_today: float = 60_000_000.0,
    timestamp: datetime | None = None,
) -> None:
    _ = (
        db_session,
        stock_code,
        change_percent,
        change_5min,
        volume_ratio,
        turnover_rate,
        main_net_inflow_today,
        timestamp,
    )


def _add_quiet_quote(db_session, stock_code: str) -> None:
    _add_quote(
        db_session,
        stock_code,
        change_percent=0.2,
        change_5min=0.1,
        volume_ratio=1.0,
        turnover_rate=1.0,
        main_net_inflow_today=0.0,
    )


def _add_position(db_session, user_id: int, stock_code: str) -> None:
    account = Account(
        user_id=user_id,
        total_assets=Decimal("100000.0000"),
        market_value=Decimal("25000.0000"),
    )
    db_session.add(account)
    db_session.flush()
    db_session.add(
        Position(
            account_id=account.account_id,
            stock_code=stock_code,
            total_shares=1000,
            current_price=Decimal("12.3400"),
            market_value=Decimal("25000.0000"),
            profit_loss=Decimal("1200.0000"),
            profit_loss_pct=Decimal("0.0500"),
        )
    )
    db_session.commit()


def _source_document(
    document_id: str = "news-1",
    *,
    source_type: MarketWatchSourceType = "news",
    url: str = "https://example.com/news",
    markdown: str | None = None,
) -> MarketWatchMarkdownDocument:
    return MarketWatchMarkdownDocument(
        id=document_id,
        source_type=source_type,
        url=url,
        final_url=url,
        title="Policy support expands",
        markdown=markdown or "# Policy support expands\n\nPolicy support expands for advanced manufacturing.",
        status=200,
        captured_at=datetime.now(timezone.utc),
    )


def _ignore_decision(stock_code: str = "000001", stock_name: str = "Alpha Tech") -> WatchAiDecision:
    return WatchAiDecision(
        stock_code=stock_code,
        stock_name=stock_name,
        action="monitor",
        confidence=0.6,
        urgency="medium",
        trigger_reason="Needs monitoring",
        evidence_summary="Anomaly exists but evidence is not enough.",
    )


def _start_debate_decision(stock_code: str = "000001", stock_name: str = "Alpha Tech") -> WatchAiDecision:
    return WatchAiDecision(
        stock_code=stock_code,
        stock_name=stock_name,
        action="start_debate",
        confidence=0.91,
        urgency="high",
        trigger_reason="Strong anomaly and news context",
        evidence_summary="Quote anomaly and news are aligned.",
        debate_parameters=DebateParameters(
            trading_frequency="day",
            trading_strategy="trend",
            simplified=True,
            debate_focus=["price-volume confirmation"],
            risk_notes=["cooldown checked"],
        ),
    )


def _add_recent_launch_event(
    db_session,
    user_id: int,
    stock_code: str = "000001",
    *,
    trigger_reason: str | None = None,
    evidence_summary: str | None = None,
    created_at: datetime | None = None,
    event_type: str = "debate_launched",
    status: str = "success",
    session_status: str = "active",
) -> None:
    """
    写入一条盯盘辩论事件，供近期启动判重测试使用。

    Args:
        db_session: 测试数据库会话。
        user_id: 当前测试用户 ID。
        stock_code: 事件关联股票代码。
        trigger_reason: 盯盘 AI 触发原因。
        evidence_summary: 盯盘 AI 证据摘要。
        created_at: 事件创建时间。
        event_type: 盯盘审计事件类型。
        status: 盯盘审计事件状态。
        session_status: 关联辩论会话状态。
    """
    event_created_at = created_at or (datetime.now() - timedelta(minutes=5))
    session = Session(
        user_id=user_id,
        stock_code=stock_code,
        trading_frequency="日内交易 (Day Trading)",
        trading_strategy="趋势追踪 (Trend Following)",
        status=session_status,
        created_at=event_created_at,
        updated_at=event_created_at,
    )
    db_session.add(session)
    db_session.commit()
    watch_ai_decision = None
    if trigger_reason is not None or evidence_summary is not None:
        watch_ai_decision = {
            "stock_code": stock_code,
            "trigger_reason": trigger_reason,
            "evidence_summary": evidence_summary,
        }
    db_session.add(
        MarketWatchEvent(
            user_id=user_id,
            event_type=event_type,
            status=status,
            watch_ai_decision=watch_ai_decision,
            debate_session_id=str(session.session_id),
            created_at=event_created_at,
        )
    )
    db_session.commit()


@pytest.mark.asyncio
async def test_scan_skips_on_non_trading_day_without_loading_news_or_ai(db_session, caplog) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    gate = FakeWatchAiGate(_ignore_decision())
    caplog.set_level(logging.INFO, logger="app.ai.market_watch.service")

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
        now=datetime(2026, 5, 16, 10, 0),
    )

    assert result["debate_launch"] == {"status": "skipped", "reason": "non_trading_day"}
    assert result["stock_count"] == 0
    assert result["news_count"] == 0
    assert result["ai_evaluated"] is False
    assert FakeSourceDocumentFetcher.calls == []
    assert gate.payloads == []
    assert "Market watch scan skipped" in caplog.text
    assert "reason=non_trading_day" in caplog.text


@pytest.mark.asyncio
async def test_scan_skips_when_required_source_urls_are_missing(db_session) -> None:
    user = _create_user(db_session, configure_sources=False)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    gate = FakeWatchAiGate(_ignore_decision())

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
    )

    assert result["debate_launch"] == {"status": "skipped", "reason": "missing_source_urls"}
    assert result["stock_count"] == 0
    assert result["news_count"] == 0
    assert result["ai_evaluated"] is False
    assert FakeSourceDocumentFetcher.calls == []
    assert gate.payloads == []


@pytest.mark.asyncio
async def test_scan_market_watch_does_not_check_removed_research_task_lock(db_session, monkeypatch):
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001.SZ")
    _add_quote(db_session, "000001.SZ")
    gate = FakeWatchAiGate(_ignore_decision())

    assert not hasattr(market_watch_service, "get_market_watch_skip_reason")

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
        now=datetime(2026, 5, 14, 10, 0),
    )

    assert result["ai_evaluated"] is True
    assert result["debate_launch"]["status"] == "not_started"
    assert gate.payloads


@pytest.mark.asyncio
async def test_scan_can_run_on_non_trading_day_when_enabled(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    upsert_market_watch_settings(
        user.id,
        MarketWatchSettingsUpdate(scan_non_trading_days=True),
    )
    gate = FakeWatchAiGate(_ignore_decision())

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
        now=datetime(2026, 5, 16, 10, 0),
    )

    assert result["debate_launch"]["status"] == "not_started"
    assert result["stock_count"] == 1
    assert result["news_count"] == 1
    assert result["ai_evaluated"] is True
    assert FakeSourceDocumentFetcher.calls == [
        {
            "urls": ["https://example.com/data"],
            "source_type": "data",
        },
        {
            "urls": ["https://example.com/news"],
            "source_type": "news",
        },
    ]
    assert len(gate.payloads) == 1


@pytest.mark.asyncio
async def test_scan_logs_configured_source_fetch_failure_with_exception(db_session, monkeypatch) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    logged_messages: list[str] = []

    def capture_exception(message: str, *args, **kwargs) -> None:
        _ = args, kwargs
        logged_messages.append(message)

    monkeypatch.setattr(market_watch_service.logger, "exception", capture_exception)

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_ignore_decision()),
        source_document_fetcher=_raising_source_document_fetcher,
    )

    assert result["stock_count"] == 1
    assert result["news_documents"][0]["error"] == "RuntimeError: source unavailable"
    assert "Market watch configured source fetch failed" in logged_messages


@pytest.mark.asyncio
async def test_scan_skips_outside_configured_time_window_without_loading_news_or_ai(db_session, caplog) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    upsert_market_watch_settings(
        user.id,
        MarketWatchSettingsUpdate(scan_start_time="10:00", scan_end_time="14:30"),
    )
    gate = FakeWatchAiGate(_ignore_decision())
    caplog.set_level(logging.INFO, logger="app.ai.market_watch.service")

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
        now=datetime(2026, 5, 14, 9, 59),
    )

    assert result["debate_launch"] == {"status": "skipped", "reason": "outside_scan_time_window"}
    assert result["stock_count"] == 0
    assert result["news_count"] == 0
    assert result["ai_evaluated"] is False
    assert FakeSourceDocumentFetcher.calls == []
    assert gate.payloads == []
    assert "Market watch scan skipped" in caplog.text
    assert "reason=outside_scan_time_window" in caplog.text


@pytest.mark.asyncio
async def test_scan_returns_warehouse_source_documents_and_position_context(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    _add_position(db_session, user.id, "000001")
    gate = FakeWatchAiGate(_ignore_decision())

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
    )

    assert result["stock_count"] == 1
    assert result["ai_evaluated"] is True
    assert result["debate_launch"]["status"] == "not_started"
    item = result["items"][0]
    assert item["stock_code"] == "000001"
    assert item["stock_name"] == "Alpha Tech"
    assert item["industry"] == "Semiconductor"
    assert item["market"] == "SZ"
    assert item["quote_status"] == "missing"
    assert item["quote"] is None
    assert item["has_position"] is True
    assert item["position_ratio"] == 0.25
    assert result["news_documents"][0]["markdown"].startswith("# Policy support expands")


@pytest.mark.asyncio
async def test_scan_sends_source_documents_and_stock_context_to_watch_ai(db_session, caplog) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    gate = FakeWatchAiGate(_ignore_decision())
    caplog.set_level(logging.DEBUG, logger="app.ai.market_watch.service")

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
    )

    assert result["news_count"] == 1
    assert result["news_documents"][0]["id"] == "news-1"
    assert len(gate.payloads) == 1
    payload = gate.payloads[0]
    assert payload["warehouse_stocks"][0]["stock_code"] == "000001"
    assert payload["warehouse_stocks"][0]["trading_frequency_code"] == "position"
    assert payload["warehouse_stocks"][0]["trading_strategy_code"] == "value"
    assert payload["news_documents"][0]["id"] == "news-1"
    assert "news_items" not in payload
    assert payload["account_summary"]["user_id"] == user.id
    assert "trading_frequency" not in payload["settings"]
    assert "trading_strategy" not in payload["settings"]
    assert "trading_frequency_code" not in payload["settings"]
    assert "trading_strategy_code" not in payload["settings"]
    assert "Market watch Watch AI full input" in caplog.text
    assert "ai_input=" in caplog.text
    assert "Alpha Tech" in caplog.text
    assert "news-1" in caplog.text


@pytest.mark.asyncio
async def test_scan_sends_recent_successful_debate_launches_to_watch_ai(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="同一公告触发",
        evidence_summary="公告和盘口异动一致",
        created_at=datetime(2026, 5, 14, 9, 30),
    )
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="旧事件",
        evidence_summary="超过 24 小时",
        created_at=datetime(2026, 5, 13, 9, 0),
    )
    gate = FakeWatchAiGate(_ignore_decision())

    await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
        now=datetime(2026, 5, 14, 10, 0),
    )

    recent_launches = gate.payloads[0]["recent_debate_launches"]
    assert len(recent_launches) == 1
    assert recent_launches[0]["stock_code"] == "000001"
    assert recent_launches[0]["trigger_reason"] == "同一公告触发"
    assert recent_launches[0]["evidence_summary"] == "公告和盘口异动一致"
    assert recent_launches[0]["debate_session_id"]


def test_recent_debate_launches_exclude_skipped_and_non_success_events(db_session) -> None:
    user = _create_user(db_session)
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="成功启动事件",
        evidence_summary="已真实启动辩论",
        created_at=datetime(2026, 5, 14, 9, 30),
    )
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="跳过事件",
        evidence_summary="没有启动辩论",
        created_at=datetime(2026, 5, 14, 9, 40),
        event_type="debate_skipped",
        status="skipped",
    )
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="失败事件",
        evidence_summary="启动未成功",
        created_at=datetime(2026, 5, 14, 9, 50),
        status="failed",
    )

    launches = market_watch_service._load_recent_debate_launches(
        user_id=user.id,
        now=datetime(2026, 5, 14, 10, 0),
        lookback_hours=24,
    )

    assert len(launches) == 1
    assert launches[0]["trigger_reason"] == "成功启动事件"


def test_recent_debate_launches_exclude_failed_debate_session(db_session) -> None:
    user = _create_user(db_session)
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="成功流程事件",
        evidence_summary="辩论流程仍有效",
        created_at=datetime(2026, 5, 14, 9, 20),
    )
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="会话失败事件",
        evidence_summary="辩论会话失败",
        created_at=datetime(2026, 5, 14, 9, 30),
        session_status="failed",
    )
    launches = market_watch_service._load_recent_debate_launches(
        user_id=user.id,
        now=datetime(2026, 5, 14, 10, 0),
        lookback_hours=24,
    )

    assert len(launches) == 1
    assert launches[0]["trigger_reason"] == "成功流程事件"


@pytest.mark.asyncio
async def test_recent_debate_launch_window_uses_runtime_setting(db_session) -> None:
    user = _create_user(db_session)
    _add_recent_launch_event(
        db_session,
        user.id,
        stock_code="000001",
        trigger_reason="配置窗口内事件",
        evidence_summary="25 小时前事件",
        created_at=datetime(2026, 5, 13, 9, 0),
    )
    launches = market_watch_service._load_recent_debate_launches(
        user_id=user.id,
        now=datetime(2026, 5, 14, 10, 0),
        lookback_hours=26,
    )

    assert len(launches) == 1
    assert launches[0]["trigger_reason"] == "配置窗口内事件"


@pytest.mark.asyncio
async def test_scan_pushes_same_source_markdown_to_frontend_and_watch_ai(db_session, monkeypatch) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    gate = FakeWatchAiGate(_ignore_decision())
    published_payloads: list[dict[str, Any]] = []

    async def capture_documents_payload(payload: dict[str, Any]) -> int:
        published_payloads.append(payload)
        return 1

    async def cleaning_fetcher(
        urls: list[str],
        source_type: MarketWatchSourceType,
    ) -> list[MarketWatchMarkdownDocument]:
        documents: list[MarketWatchMarkdownDocument] = []
        for index, url in enumerate(urls):
            source_url = getattr(url, "url", url)
            markdown = f"# {source_type}\n* |\nVisible {source_type} context"
            documents.append(
                _source_document(
                    document_id=f"{source_type}-{index + 1}",
                    source_type=source_type,
                    url=source_url,
                    markdown=markdown,
                )
            )
        return documents

    monkeypatch.setattr(market_watch_service, "publish_market_watch_documents_payload", capture_documents_payload)

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
        source_document_fetcher=cleaning_fetcher,
    )

    assert len(published_payloads) == 1
    pushed_documents = {document["id"]: document["markdown"] for document in published_payloads[0]["documents"]}
    ai_documents = {
        document["id"]: document["markdown"]
        for document in gate.payloads[0]["data_documents"] + gate.payloads[0]["news_documents"]
    }
    response_documents = {
        document["id"]: document["markdown"]
        for document in result["data_documents"] + result["news_documents"]
    }
    assert pushed_documents == ai_documents == response_documents
    assert all("* |" in markdown for markdown in pushed_documents.values())
    assert pushed_documents["data-1"] == "# data\n* |\nVisible data context"
    assert pushed_documents["news-1"] == "# news\n* |\nVisible news context"


@pytest.mark.asyncio
async def test_scan_evaluates_watch_ai_when_configured_news_source_exists(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quiet_quote(db_session, "000001")

    first_gate = FakeWatchAiGate(_ignore_decision())
    first_result = await scan_market_watch(
        user.id,
        watch_ai_gate=first_gate,
    )
    second_gate = FakeWatchAiGate(_ignore_decision())
    second_result = await scan_market_watch(
        user.id,
        watch_ai_gate=second_gate,
    )

    assert first_result["ai_evaluated"] is True
    assert len(first_gate.payloads) == 1
    assert second_result["ai_evaluated"] is True
    assert len(second_gate.payloads) == 1


@pytest.mark.asyncio
async def test_scan_evaluates_watch_ai_for_configured_data_and_news_sources(db_session) -> None:
    user = _create_user(db_session, configure_sources=False)
    _add_stock(db_session, user.id, "000001")
    _add_quiet_quote(db_session, "000001")
    upsert_market_watch_settings(
        user.id,
        MarketWatchSettingsUpdate(
            data_sources=["https://example.com/data"],
            news_sources=["https://example.com/news"],
        ),
    )
    gate = FakeWatchAiGate(_ignore_decision())

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
    )

    assert result["ai_evaluated"] is True
    assert result["data_document_count"] == 1
    assert result["news_count"] == 1
    assert len(gate.payloads) == 1


@pytest.mark.asyncio
async def test_scan_loads_data_and_news_source_documents_concurrently(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quiet_quote(db_session, "000001")
    fetcher = BlockingSourceDocumentFetcher(expected_calls=2)
    scan_task = asyncio.create_task(
        scan_market_watch(
            user.id,
            watch_ai_gate=FakeWatchAiGate(_ignore_decision()),
            source_document_fetcher=fetcher,
        )
    )

    try:
        await asyncio.wait_for(fetcher.all_started.wait(), timeout=1)
        assert fetcher.max_active_calls == 2
    finally:
        fetcher.release.set()
        result = await asyncio.wait_for(scan_task, timeout=2)

    assert result["data_document_count"] == 1
    assert result["news_count"] == 1


@pytest.mark.asyncio
async def test_scan_sends_full_markdown_documents_without_news_cache_truncation(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    upsert_market_watch_settings(
        user.id,
        MarketWatchSettingsUpdate(
            news_sources=["https://example.com/news-a", "https://example.com/news-b"],
        ),
    )
    gate = FakeWatchAiGate(_ignore_decision())
    markdown = "# Full News\n\n" + ("long body\n" * 500)

    async def full_markdown_fetcher(
        urls: list[str],
        source_type: MarketWatchSourceType,
    ) -> list[MarketWatchMarkdownDocument]:
        return [
            _source_document(
                document_id=f"{source_type}-{index + 1}",
                source_type=source_type,
                url=getattr(url, "url", url),
                markdown=markdown,
            )
            for index, url in enumerate(urls)
        ]

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=gate,
        source_document_fetcher=full_markdown_fetcher,
    )

    assert result["news_count"] == 2
    assert [item["id"] for item in result["news_documents"]] == ["news-1", "news-2"]
    assert result["news_documents"][0]["markdown"] == markdown
    assert gate.payloads[0]["news_documents"][0]["markdown"] == markdown


@pytest.mark.asyncio
async def test_scan_enforces_cooldown(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    _add_recent_launch_event(db_session, user.id)
    launcher_calls: list[dict[str, Any]] = []

    cooldown_result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision().model_copy(update={"confidence": 0.84})),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    assert cooldown_result["debate_launch"]["status"] == "skipped"
    assert cooldown_result["debate_launch"]["reason"] == "cooldown"
    assert len(launcher_calls) == 0


@pytest.mark.asyncio
async def test_scan_skips_launch_when_auto_launch_disabled_by_settings(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    upsert_market_watch_settings(user.id, MarketWatchSettingsUpdate(auto_launch_debate=False))
    launcher_calls: list[dict[str, Any]] = []

    settings_result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision()),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    assert settings_result["debate_launch"]["status"] == "skipped"
    assert settings_result["debate_launch"]["reason"] == "auto_launch_disabled"
    assert launcher_calls == []
    skipped_event = db_session.query(MarketWatchEvent).filter(MarketWatchEvent.event_type == "debate_skipped").one()
    assert skipped_event.reason == "auto_launch_disabled"


@pytest.mark.asyncio
async def test_scan_breaks_cooldown_for_high_confidence_decision_with_news_evidence(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    _add_recent_launch_event(db_session, user.id)
    launcher_calls: list[dict[str, Any]] = []

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision()),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    assert result["debate_launch"]["status"] == "launched"
    assert result["debate_launch"]["cooldown_broken"] is True
    assert len(launcher_calls) == 1
    task = db_session.query(AsyncTask).filter(AsyncTask.task_id == result["debate_launch"]["task_id"]).one()
    assert task.user_id == user.id


@pytest.mark.asyncio
async def test_scan_does_not_break_cooldown_below_confidence_threshold(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    _add_recent_launch_event(db_session, user.id)
    decision = _start_debate_decision().model_copy(update={"confidence": 0.84})
    launcher_calls: list[dict[str, Any]] = []

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(decision),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    assert result["debate_launch"]["status"] == "skipped"
    assert result["debate_launch"]["reason"] == "cooldown"
    assert launcher_calls == []


@pytest.mark.asyncio
async def test_scan_breaks_cooldown_by_confidence_without_news_identity(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    _add_recent_launch_event(db_session, user.id)
    launcher_calls: list[dict[str, Any]] = []

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision()),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    assert result["debate_launch"]["status"] == "launched"
    assert result["debate_launch"]["cooldown_broken"] is True
    assert len(launcher_calls) == 1


@pytest.mark.asyncio
async def test_scan_existing_pending_or_running_task_always_skips(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    db_session.add(
        AsyncTask(
            task_name="AI Analysis - 000001",
            task_type="ai_analysis",
            status="pending",
            allow_concurrent=False,
            parameters={"stock_code": "000001", "session_id": "existing"},
        )
    )
    db_session.commit()

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision()),
        debate_launcher=lambda **kwargs: None,
    )

    assert result["debate_launch"]["status"] == "skipped"
    assert result["debate_launch"]["reason"] == "existing_task"
    assert db_session.query(Session).count() == 0


@pytest.mark.asyncio
async def test_scan_filters_watch_ai_decision_when_target_stock_is_unwatched(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")
    decision = _start_debate_decision(stock_code="999999", stock_name="Outside Stock")
    launcher_calls: list[dict[str, Any]] = []

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(decision),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    assert result["watch_ai_decision"] is None
    assert result["debate_launch"]["status"] == "not_started"
    assert result["debate_launch"]["reason"] == "watch_ai_decision"
    assert launcher_calls == []
    assert db_session.query(Session).count() == 0
    event = db_session.query(MarketWatchEvent).filter(MarketWatchEvent.event_type == "ai_decision").one()
    assert event.watch_ai_decision == []


@pytest.mark.asyncio
async def test_scan_returns_multiple_watch_ai_stock_results_and_launch_outcomes(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001", "Alpha Tech")
    _add_stock(db_session, user.id, "000002", "Beta Tech")
    launcher_calls: list[dict[str, Any]] = []

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate([
            _ignore_decision("000001", "Alpha Tech"),
            _start_debate_decision("000002", "Beta Tech"),
        ]),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    assert [item["stock_code"] for item in result["watch_ai_decision"]] == ["000001", "000002"]
    assert [item["stock_code"] for item in result["debate_launches"]] == ["000001", "000002"]
    assert result["debate_launches"][0]["status"] == "not_started"
    assert result["debate_launches"][1]["status"] == "launched"
    assert result["debate_launch"]["status"] == "launched"
    assert result["debate_launch"]["stock_code"] == "000002"
    assert result["launched_debate_count"] == 1
    assert launcher_calls[0]["stock_code"] == "000002"


@pytest.mark.asyncio
async def test_scan_pushes_latest_watch_ai_decision_to_frontend(db_session, monkeypatch) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001", "Alpha Tech")
    published_payloads: list[dict[str, Any]] = []

    async def capture_event_payload(payload: dict[str, Any]) -> int:
        published_payloads.append(payload)
        return 1

    monkeypatch.setattr(market_watch_service, "publish_market_watch_event_payload", capture_event_payload)

    await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_ignore_decision("000001", "Alpha Tech")),
    )

    ai_payload = next(payload for payload in published_payloads if payload["event_type"] == "ai_decision")
    assert ai_payload["watch_ai_decision"][0]["stock_code"] == "000001"
    assert ai_payload["watch_ai_decision"][0]["action"] == "monitor"
    assert "target_stock_code" not in ai_payload
    assert "target_stock_name" not in ai_payload
    assert "summary" not in ai_payload


@pytest.mark.asyncio
async def test_scan_keeps_scan_response_and_audits_error_when_watch_ai_fails(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=RaisingWatchAiGate(),
    )

    assert result["stock_count"] == 1
    assert result["news_count"] == 1
    assert result["ai_evaluated"] is True
    assert result["watch_ai_decision"] is None
    assert result["debate_launch"]["status"] == "failed"
    assert result["debate_launch"]["reason"] == "watch_ai_failed"
    event = db_session.query(MarketWatchEvent).filter(MarketWatchEvent.event_type == "error").one()
    assert event.status == "failed"
    assert event.error_message == "watch ai unavailable"


@pytest.mark.asyncio
async def test_scan_marks_created_task_and_session_failed_when_launcher_fails(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")

    def failing_launcher(**kwargs) -> None:
        _ = kwargs
        raise RuntimeError("scheduler unavailable")

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision()),
        debate_launcher=failing_launcher,
    )

    assert result["debate_launch"]["status"] == "failed"
    assert result["debate_launch"]["reason"] == "launch_failed"
    task = db_session.query(AsyncTask).one()
    session = db_session.query(Session).one()
    assert task.status == "failed"
    assert task.error_message == "scheduler unavailable"
    assert session.status == "failed"
    event = db_session.query(MarketWatchEvent).filter(MarketWatchEvent.event_type == "error").one()
    assert event.task_id == task.task_id
    assert event.debate_session_id == str(session.session_id)


@pytest.mark.asyncio
async def test_scan_marks_created_task_and_session_failed_when_scheduler_is_missing(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(db_session, user.id, "000001")
    _add_quote(db_session, "000001")

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision()),
    )

    assert result["debate_launch"]["status"] == "failed"
    assert result["debate_launch"]["reason"] == "launch_failed"
    task = db_session.query(AsyncTask).one()
    session = db_session.query(Session).one()
    assert task.status == "failed"
    assert task.error_message == "market watch debate scheduler is unavailable"
    assert session.status == "failed"


@pytest.mark.asyncio
async def test_scan_successful_launch_writes_audit_and_returns_session_and_task_ids(db_session) -> None:
    user = _create_user(db_session)
    _add_stock(
        db_session,
        user.id,
        "000001",
        auto_analysis_trading_frequency="日内交易 (Day Trading)",
        auto_analysis_trading_strategy="趋势追踪 (Trend Following)",
    )
    _add_quote(db_session, "000001")
    launcher_calls: list[dict[str, Any]] = []

    result = await scan_market_watch(
        user.id,
        watch_ai_gate=FakeWatchAiGate(_start_debate_decision()),
        debate_launcher=lambda **kwargs: launcher_calls.append(kwargs),
    )

    launch = result["debate_launch"]
    assert launch["status"] == "launched"
    assert launch["session_id"]
    assert launch["task_id"]
    session = db_session.query(Session).filter(Session.session_id == UUID(launch["session_id"])).one()
    assert session.trading_frequency == "日内交易 (Day Trading)"
    assert session.trading_strategy == "趋势追踪 (Trend Following)"
    task = db_session.query(AsyncTask).filter(AsyncTask.task_id == launch["task_id"]).first()
    assert task is not None
    assert set(task.parameters) == {"session_id", "stock_code", "trading_frequency", "trading_strategy"}
    assert task.parameters["trading_frequency"] == "日内交易 (Day Trading)"
    assert task.parameters["trading_strategy"] == "趋势追踪 (Trend Following)"
    launch_event = (
        db_session.query(MarketWatchEvent)
        .filter(MarketWatchEvent.event_type == "debate_launched")
        .one()
    )
    assert launch_event.status == "success"
    assert launch_event.debate_session_id == launch["session_id"]
    assert launch_event.task_id == launch["task_id"]
    assert launcher_calls[0]["task_id"] == launch["task_id"]
    assert set(launcher_calls[0]) == {
        "task_id",
        "session_id",
        "stock_code",
        "trading_frequency",
        "trading_strategy",
        "trigger_reason",
        "evidence_summary",
    }
    assert launcher_calls[0]["trading_frequency"] == "日内交易 (Day Trading)"
    assert launcher_calls[0]["trading_strategy"] == "趋势追踪 (Trend Following)"
    assert launcher_calls[0]["trigger_reason"] == "Strong anomaly and news context"
    assert launcher_calls[0]["evidence_summary"] == "Quote anomaly and news are aligned."
