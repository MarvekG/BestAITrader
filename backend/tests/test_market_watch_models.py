from app.core.database import Base
from app.models.market_watch import MarketWatchEvent


def test_market_watch_event_model_columns_exist() -> None:
    columns = MarketWatchEvent.__table__.columns

    assert "event_id" in columns
    assert "user_id" in columns
    assert "event_type" in columns
    assert "news_fingerprints" not in columns
    assert "target_stock_code" not in columns
    assert "target_stock_name" not in columns
    assert "summary" not in columns
    assert "watch_ai_decision" in columns
    assert "created_at" in columns


def test_market_watch_models_registered_by_models_package_import() -> None:
    import app.models

    assert "MarketWatchEvent" in app.models.__all__
    assert app.models.MarketWatchEvent is MarketWatchEvent
    assert "market_watch_events" in Base.metadata.tables
