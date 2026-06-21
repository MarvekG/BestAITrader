from datetime import datetime
import uuid

from app.ai.market_watch.schemas import DEFAULT_MARKET_WATCH_SCAN_INTERVAL_SECONDS, MarketWatchMarkdownDocument
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
    assert payload["data_sources"] == []
    assert payload["news_sources"] == []
    assert "trading_frequency" not in payload
    assert "trading_strategy" not in payload


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
            "data_sources": [{"url": "https://example.com/data", "cleanup_patterns": [r"(?m)^REMOVE ME$"]}],
            "news_sources": ["news.example.com/latest"],
            "recent_debate_dedup_enabled": False,
            "recent_debate_lookback_hours": 36,
        },
    )
    get_response = client.get("/api/v1/market-watch/settings", headers=auth_headers)

    assert update_response.status_code == 200
    assert get_response.status_code == 200
    payload = get_response.json()
    assert payload["scan_interval_seconds"] == 45
    assert payload["scan_start_time"] == "10:00"
    assert payload["scan_end_time"] == "14:30"
    assert payload["data_sources"] == [
        {"url": "https://example.com/data", "content_selectors": [], "cleanup_patterns": [r"(?m)^REMOVE ME$"]}
    ]
    assert payload["news_sources"] == [
        {"url": "https://news.example.com/latest", "content_selectors": [], "cleanup_patterns": []}
    ]
    assert payload["recent_debate_dedup_enabled"] is False
    assert payload["recent_debate_lookback_hours"] == 36
    assert "trading_frequency" not in payload
    assert "trading_strategy" not in payload
    setting = (
        db_session.query(SystemSetting)
        .filter(SystemSetting.key == "market_watch.settings", SystemSetting.user_id == payload["user_id"])
        .one()
    )
    assert setting.value["scan_interval_seconds"] == 45
    assert setting.value["news_sources"] == [
        {"url": "https://news.example.com/latest", "content_selectors": [], "cleanup_patterns": []}
    ]
    assert setting.value["recent_debate_dedup_enabled"] is False
    assert setting.value["recent_debate_lookback_hours"] == 36
    assert refresh_calls == ["refresh"]


def test_market_watch_settings_are_isolated_by_user(client, db_session) -> None:
    owner, owner_headers = _create_authenticated_user(client, db_session)
    other, other_headers = _create_authenticated_user(client, db_session)

    update_response = client.put(
        "/api/v1/market-watch/settings",
        headers=owner_headers,
        json={
            "scan_interval_seconds": 45,
            "data_sources": ["https://example.com/data"],
            "news_sources": ["https://example.com/news"],
        },
    )
    other_response = client.get("/api/v1/market-watch/settings", headers=other_headers)

    assert update_response.status_code == 200
    assert update_response.json()["user_id"] == owner.id
    assert other_response.status_code == 200
    assert other_response.json()["user_id"] == other.id
    assert other_response.json()["scan_interval_seconds"] == DEFAULT_MARKET_WATCH_SCAN_INTERVAL_SECONDS


def test_market_watch_settings_update_allows_sources_to_be_configured_later(client, auth_headers) -> None:
    response = client.put(
        "/api/v1/market-watch/settings",
        headers=auth_headers,
        json={"scan_interval_seconds": 45},
    )

    assert response.status_code == 200
    assert response.json()["scan_interval_seconds"] == 45
    assert response.json()["data_sources"] == []
    assert response.json()["news_sources"] == []


def test_market_watch_scan_returns_skeleton_response(client, auth_headers) -> None:
    response = client.post("/api/v1/market-watch/scan", headers=auth_headers, json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["stock_count"] == 0
    assert payload["ai_evaluated"] is False
    assert payload["items"] == []


def test_market_watch_source_preview_reuses_source_fetcher(client, auth_headers, monkeypatch) -> None:
    from app.api.endpoints import market_watch

    captured = {}

    async def fake_fetch_market_watch_documents(sources, source_type):
        captured["sources"] = sources
        captured["source_type"] = source_type
        return [
            MarketWatchMarkdownDocument(
                id="data:0:preview",
                source_type="data",
                url="https://example.com/page",
                final_url="https://example.com/page",
                title="Preview",
                markdown="matched data",
                status=200,
                captured_at=datetime.now(),
            )
        ]

    monkeypatch.setattr(market_watch, "fetch_market_watch_documents", fake_fetch_market_watch_documents)

    response = client.post(
        "/api/v1/market-watch/source-preview",
        headers=auth_headers,
        json={"source_config": "https://example.com/page @@ main @@ .news", "cleanup_patterns": [r"(?m)^REMOVE ME$"]},
    )

    assert response.status_code == 200
    assert captured["sources"][0].url == "https://example.com/page"
    assert captured["sources"][0].content_selectors == ["main", ".news"]
    assert captured["sources"][0].cleanup_patterns == [r"(?m)^REMOVE ME$"]
    assert captured["source_type"] == "data"
    assert response.json()["markdown"] == "matched data"


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
