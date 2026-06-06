from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from app.ai.market_watch.settings import (
    get_market_watch_settings,
    market_watch_settings_key,
    merge_market_watch_settings,
    upsert_market_watch_settings,
)
from app.ai.market_watch.schemas import MarketWatchEventSchema, MarketWatchSettingsResponse, MarketWatchSettingsUpdate
from app.models.system_setting import SystemSetting
from app.models.user import User


def test_merge_market_watch_settings_keeps_existing_values_for_partial_update() -> None:
    existing = MarketWatchSettingsResponse(user_id=7)
    update = MarketWatchSettingsUpdate(
        scan_interval_seconds=45,
        scan_start_time="10:00",
        scan_end_time="14:30",
        data_sources=[" example.com/quotes "],
        news_sources=["https://news.example.com/feed"],
    )

    merged = merge_market_watch_settings(existing, update)

    assert merged.user_id == 7
    assert merged.scan_interval_seconds == 45
    assert merged.scan_non_trading_days is False
    assert merged.scan_start_time == "10:00"
    assert merged.scan_end_time == "14:30"
    assert merged.cooldown_minutes == 60
    assert merged.recent_debate_dedup_enabled is True
    assert merged.recent_debate_lookback_hours == 24
    assert merged.trading_frequency == "中长线持有 (Position Trading)"
    assert merged.data_sources[0].url == "https://example.com/quotes"
    assert merged.news_sources[0].url == "https://news.example.com/feed"


def test_market_watch_settings_normalizes_source_url_selector_specs() -> None:
    update = MarketWatchSettingsUpdate(
        data_sources=[
            " example.com/quotes @@ body > div.main @@ #news-list ",
            "https://example.com/full",
        ],
        news_sources="https://news.example.com/feed @@ article.main\nhttps://news.example.com/full",
    )

    assert [source.model_dump() for source in update.data_sources or []] == [
        {"url": "https://example.com/quotes", "content_selectors": ["body > div.main", "#news-list"], "cleanup_patterns": []},
        {"url": "https://example.com/full", "content_selectors": [], "cleanup_patterns": []},
    ]
    assert [source.model_dump() for source in update.news_sources or []] == [
        {"url": "https://news.example.com/feed", "content_selectors": ["article.main"], "cleanup_patterns": []},
        {"url": "https://news.example.com/full", "content_selectors": [], "cleanup_patterns": []},
    ]


def test_market_watch_settings_keeps_cleanup_patterns_per_source() -> None:
    update = MarketWatchSettingsUpdate(
        data_sources=[
            {
                "url": "example.com/quotes",
                "content_selectors": [" main "],
                "cleanup_patterns": [r"(?m)^noise$", r" (?m)^noise$ ", ""],
            }
        ]
    )

    source = (update.data_sources or [])[0]
    assert source.url == "https://example.com/quotes"
    assert source.content_selectors == ["main"]
    assert source.cleanup_patterns == [r"(?m)^noise$"]


def test_merge_market_watch_settings_treats_explicit_none_as_omitted() -> None:
    existing = MarketWatchSettingsResponse(
        user_id=7,
        scan_interval_seconds=45,
        auto_launch_debate=False,
    )
    update = MarketWatchSettingsUpdate(
        scan_interval_seconds=None,
        auto_launch_debate=None,
    )

    merged = merge_market_watch_settings(existing, update)

    assert merged.scan_interval_seconds == 45
    assert merged.auto_launch_debate is False


def test_market_watch_settings_reject_invalid_runtime_config() -> None:
    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(scan_interval_seconds=29)

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(scan_start_time="9:30")

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(scan_start_time="15:00", scan_end_time="09:30")

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(cooldown_break_confidence=1.5)

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(recent_debate_lookback_hours=0)

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(trading_frequency="")

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(data_sources=["ftp://example.com/feed"])

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(news_sources=["https://"])

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(data_sources=[])

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(news_sources=[])

    with pytest.raises(ValidationError):
        MarketWatchSettingsUpdate(data_sources=[{"url": "https://example.com/feed", "cleanup_patterns": ["["]}])

def test_market_watch_settings_can_disable_recent_debate_deduplication() -> None:
    existing = MarketWatchSettingsResponse(user_id=7)
    update = MarketWatchSettingsUpdate(recent_debate_dedup_enabled=False)

    merged = merge_market_watch_settings(existing, update)

    assert merged.recent_debate_dedup_enabled is False


def test_market_watch_settings_save_allows_sources_to_be_configured_later(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    db.add(User(id=7, username="market-watch-owner", email="market-watch-owner@example.com", password_hash="hash"))
    db.commit()
    db.close()

    settings = upsert_market_watch_settings(
        7,
        MarketWatchSettingsUpdate(scan_interval_seconds=45),
    )

    assert settings.scan_interval_seconds == 45
    assert settings.data_sources == []
    assert settings.news_sources == []


def test_market_watch_settings_default_scan_window_matches_a_share_session() -> None:
    settings = MarketWatchSettingsResponse(user_id=7)

    assert settings.scan_start_time == "09:30"
    assert settings.scan_end_time == "15:00"
    assert settings.scan_interval_seconds == 300
    assert settings.scan_non_trading_days is False
    assert settings.recent_debate_lookback_hours == 24
    assert settings.trading_frequency == "中长线持有 (Position Trading)"
    assert settings.trading_strategy == "价值投资 (Value Investing)"
    assert settings.data_sources == []
    assert settings.news_sources == []


def test_market_watch_settings_persist_in_system_settings_table(test_db) -> None:
    session_factory = test_db
    db = session_factory()
    db.add(User(id=7, username="market-watch-owner", email="market-watch-owner@example.com", password_hash="hash"))
    db.commit()
    db.close()

    updated = upsert_market_watch_settings(
        7,
        MarketWatchSettingsUpdate(
            scan_interval_seconds=45,
            scan_non_trading_days=True,
            recent_debate_lookback_hours=48,
            data_sources=[{"url": "https://example.com/data", "cleanup_patterns": [r"(?m)^REMOVE ME$"]}],
            news_sources=["news.example.com/latest"],
            trading_frequency="日内交易 (Day Trading)",
            trading_strategy="趋势追踪 (Trend Following)",
        ),
    )

    db = session_factory()
    row = db.query(SystemSetting).filter(
        SystemSetting.key == market_watch_settings_key(7),
        SystemSetting.user_id == 7,
    ).one()
    loaded = get_market_watch_settings(7)

    assert updated.scan_interval_seconds == 45
    assert updated.scan_non_trading_days is True
    assert updated.recent_debate_lookback_hours == 48
    assert row.value["scan_non_trading_days"] is True
    assert row.value["recent_debate_lookback_hours"] == 48
    assert row.value["data_sources"] == [
        {"url": "https://example.com/data", "content_selectors": [], "cleanup_patterns": [r"(?m)^REMOVE ME$"]}
    ]
    assert row.value["news_sources"] == [
        {"url": "https://news.example.com/latest", "content_selectors": [], "cleanup_patterns": []}
    ]
    assert row.value["trading_frequency"] == "日内交易 (Day Trading)"
    assert row.value["trading_strategy"] == "趋势追踪 (Trend Following)"
    assert loaded.trading_frequency == "日内交易 (Day Trading)"
    assert loaded.news_sources[0].url == "https://news.example.com/latest"


def test_market_watch_event_schema_keeps_audit_payload_small_and_structured() -> None:
    event = MarketWatchEventSchema(
        user_id=7,
        event_type="debate_skipped",
        status="skipped",
        target_stock_code="600519",
        target_stock_name="贵州茅台",
        summary="冷却命中，跳过自动启动",
        created_at=datetime.now() - timedelta(minutes=1),
    )

    payload = event.model_dump()

    assert payload["event_type"] == "debate_skipped"
    assert "news_fingerprints" not in payload
    assert "target_stock_code" not in payload
    assert "target_stock_name" not in payload
    assert "summary" not in payload
    assert payload["watch_ai_decision"] is None


def test_market_watch_event_schema_defaults_list_fields() -> None:
    event = MarketWatchEventSchema(
        user_id=7,
        event_type="scan",
        status="success",
    )

    assert event.watch_ai_decision is None
