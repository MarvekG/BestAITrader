from datetime import datetime
import uuid

from app.ai.market_watch.schemas import (
    DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS,
    DEFAULT_MARKET_WATCH_SCAN_INTERVAL_SECONDS,
)
from app.crud.user import create_user
from app.models.market_watch import MarketWatchEvent
from app.models.system_setting import SystemSetting
from app.schemas.user import UserCreate


def _create_authenticated_user(client, db_session):
    username = f"market_watch_{uuid.uuid4().hex[:8]}"
    password = "password123"
    user = create_user(
        db_session,
        UserCreate(
            username=username,
            email=f"{username}@example.com",
            password=password,
        ),
    )
    response = client.post(
        "/api/v1/auth/login",
        data={"username": username, "password": password},
    )
    return user, {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_market_watch_settings_returns_defaults(client, auth_headers) -> None:
    response = client.get("/api/v1/market-watch/settings", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert payload["auto_scan_enabled"] is True
    assert payload["scan_interval_seconds"] == DEFAULT_MARKET_WATCH_SCAN_INTERVAL_SECONDS
    assert payload["scan_start_time"] == "09:30"
    assert payload["scan_end_time"] == "15:00"
    assert payload["recent_debate_dedup_enabled"] is True
    assert payload["recent_debate_lookback_hours"] == 24
    assert payload["data_source_urls"] == []
    assert payload["news_source_urls"] == []
    assert payload["clean_source_markdown"] is True
    assert payload["markdown_cleanup_patterns"] == DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS
    assert payload["trading_frequency"] == "中长线持有 (Position Trading)"
    assert payload["trading_strategy"] == "价值投资 (Value Investing)"


def test_market_watch_settings_update_persists_runtime_values(client, auth_headers, db_session) -> None:
    from app.api.endpoints import market_watch

    refresh_calls = []
    market_watch.async_task_scheduler.refresh_schedule = lambda: refresh_calls.append("refresh")

    update_response = client.put(
        "/api/v1/market-watch/settings",
        headers=auth_headers,
        json={
            "scan_interval_seconds": 45,
            "scan_start_time": "10:00",
            "scan_end_time": "14:30",
            "data_source_urls": ["https://example.com/data"],
            "news_source_urls": ["news.example.com/latest"],
            "recent_debate_dedup_enabled": False,
            "recent_debate_lookback_hours": 36,
            "clean_source_markdown": False,
            "markdown_cleanup_patterns": [r"(?m)^REMOVE ME$"],
            "trading_frequency": "日内交易 (Day Trading)",
            "trading_strategy": "趋势追踪 (Trend Following)",
        },
    )
    get_response = client.get("/api/v1/market-watch/settings", headers=auth_headers)

    assert update_response.status_code == 200
    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["scan_interval_seconds"] == 45
    assert payload["scan_start_time"] == "10:00"
    assert payload["scan_end_time"] == "14:30"
    assert payload["data_source_urls"] == ["https://example.com/data"]
    assert payload["news_source_urls"] == ["https://news.example.com/latest"]
    assert payload["recent_debate_dedup_enabled"] is False
    assert payload["recent_debate_lookback_hours"] == 36
    assert payload["clean_source_markdown"] is False
    assert payload["markdown_cleanup_patterns"] == [r"(?m)^REMOVE ME$"]
    assert payload["trading_frequency"] == "日内交易 (Day Trading)"
    assert payload["trading_strategy"] == "趋势追踪 (Trend Following)"
    setting = (
        db_session.query(SystemSetting)
        .filter(SystemSetting.key == "market_watch.settings", SystemSetting.user_id == payload["user_id"])
        .one()
    )
    assert setting.value["scan_interval_seconds"] == 45
    assert setting.value["news_source_urls"] == ["https://news.example.com/latest"]
    assert setting.value["recent_debate_dedup_enabled"] is False
    assert setting.value["recent_debate_lookback_hours"] == 36
    assert setting.value["markdown_cleanup_patterns"] == [r"(?m)^REMOVE ME$"]
    assert refresh_calls == ["refresh"]


def test_market_watch_settings_are_isolated_by_user(client, db_session) -> None:
    owner, owner_headers = _create_authenticated_user(client, db_session)
    other, other_headers = _create_authenticated_user(client, db_session)

    update_response = client.put(
        "/api/v1/market-watch/settings",
        headers=owner_headers,
        json={
            "scan_interval_seconds": 45,
            "data_source_urls": ["https://example.com/data"],
            "news_source_urls": ["https://example.com/news"],
        },
    )
    other_response = client.get("/api/v1/market-watch/settings", headers=other_headers)

    assert update_response.status_code == 200
    assert update_response.json()["user_id"] == owner.id
    assert other_response.status_code == 200
    assert other_response.json()["user_id"] == other.id
    assert other_response.json()["scan_interval_seconds"] == DEFAULT_MARKET_WATCH_SCAN_INTERVAL_SECONDS


def test_market_watch_settings_update_requires_data_and_news_source_urls(client, auth_headers) -> None:
    response = client.put(
        "/api/v1/market-watch/settings",
        headers=auth_headers,
        json={"scan_interval_seconds": 45},
    )

    assert response.status_code == 400
    assert "data_source_urls and news_source_urls are required" in response.json()["detail"]


def test_market_watch_scan_returns_skeleton_response(client, auth_headers) -> None:
    response = client.post("/api/v1/market-watch/scan", headers=auth_headers, json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stock_count"] == 0
    assert payload["ai_evaluated"] is False
    assert payload["items"] == []


def test_market_watch_events_returns_current_user_events(client, auth_headers, db_session) -> None:
    settings_response = client.get("/api/v1/market-watch/settings", headers=auth_headers)
    user_id = settings_response.json()["user_id"]
    event = MarketWatchEvent(
        user_id=user_id,
        event_type="scan",
        status="success",
        created_at=datetime.now(),
    )
    db_session.add(event)
    db_session.commit()

    response = client.get("/api/v1/market-watch/events", headers=auth_headers)

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["event_type"] == "scan"
    assert "target_stock_code" not in payload[0]
    assert "target_stock_name" not in payload[0]
    assert "summary" not in payload[0]
