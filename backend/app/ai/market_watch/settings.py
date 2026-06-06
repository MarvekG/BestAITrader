from __future__ import annotations

from app.ai.market_watch.schemas import (
    MarketWatchSettingsResponse,
    MarketWatchSettingsUpdate,
)
from app.core import database as database_module
from app.crud.system_setting import system_setting
from app.models.system_setting import SystemSetting


MARKET_WATCH_SETTINGS_KEY = "market_watch.settings"
MARKET_WATCH_SETTINGS_DESCRIPTION = "Per-user market watch automation settings"


def market_watch_settings_key(user_id: int | None = None) -> str:
    """
    Return the system_settings key used for one user's market watch settings.

    Args:
        user_id: Current authenticated user id. Kept for caller compatibility; ownership is stored in user_id.

    Returns:
        Stable system setting key.
    """
    return MARKET_WATCH_SETTINGS_KEY


def merge_market_watch_settings(
    existing: MarketWatchSettingsResponse,
    update: MarketWatchSettingsUpdate,
) -> MarketWatchSettingsResponse:
    """
    Merge a partial settings update into an existing settings response.

    Args:
        existing: Current fully materialized settings.
        update: Partial settings payload supplied by a caller.

    Returns:
        A new response object with scalar fields preserved when omitted.
    """
    data = existing.model_dump()
    update_data = update.model_dump(exclude_none=True, exclude_unset=True)

    data.update(update_data)
    return MarketWatchSettingsResponse(**data)


def _materialize_market_watch_settings(
    user_id: int,
    row: SystemSetting | None,
) -> MarketWatchSettingsResponse:
    data = MarketWatchSettingsResponse(user_id=user_id).model_dump()
    if row is not None and isinstance(row.value, dict):
        data.update(row.value)
        if "data_sources" not in row.value and row.value.get("data_source_urls"):
            data["data_sources"] = row.value["data_source_urls"]
        if "news_sources" not in row.value and row.value.get("news_source_urls"):
            data["news_sources"] = row.value["news_source_urls"]
        data.pop("data_source_urls", None)
        data.pop("news_source_urls", None)
        data["created_at"] = row.created_at
        data["updated_at"] = row.updated_at
    data["user_id"] = user_id
    return MarketWatchSettingsResponse(**data)


def get_market_watch_settings(user_id: int) -> MarketWatchSettingsResponse:
    """
    Return persisted market watch settings for a user, or defaults when absent.

    Args:
        user_id: Current authenticated user id.

    Returns:
        Fully materialized settings response.
    """
    with database_module.SessionLocal() as db:
        row = system_setting.get_by_key(db, market_watch_settings_key(user_id), user_id=user_id)
        return _materialize_market_watch_settings(user_id, row)


def upsert_market_watch_settings(
    user_id: int,
    update: MarketWatchSettingsUpdate,
) -> MarketWatchSettingsResponse:
    """
    Create or update persisted market watch settings for a user.

    Args:
        user_id: Current authenticated user id.
        update: Partial settings update.

    Returns:
        Persisted settings after merge.
    """
    with database_module.SessionLocal() as db:
        existing_row = system_setting.get_by_key(db, market_watch_settings_key(user_id), user_id=user_id)
        existing = _materialize_market_watch_settings(user_id, existing_row)
        merged = merge_market_watch_settings(existing, update)
        value = merged.model_dump(mode="json", exclude={"created_at", "updated_at"})
        row = system_setting.set_value(
            db,
            key=market_watch_settings_key(user_id),
            value=value,
            description=MARKET_WATCH_SETTINGS_DESCRIPTION,
            user_id=user_id,
        )
        data = value | {"created_at": row.created_at, "updated_at": row.updated_at}
        return MarketWatchSettingsResponse(**data)
