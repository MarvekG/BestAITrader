from __future__ import annotations

from datetime import datetime, time
import re
from typing import Any, Literal
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DEFAULT_MARKET_WATCH_SCAN_START_TIME = "09:30"
DEFAULT_MARKET_WATCH_SCAN_END_TIME = "15:00"
DEFAULT_MARKET_WATCH_TRADING_FREQUENCY = "中长线持有 (Position Trading)"
DEFAULT_MARKET_WATCH_TRADING_STRATEGY = "价值投资 (Value Investing)"
MARKET_WATCH_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
MAX_MARKET_WATCH_SOURCE_URLS = 20
MARKET_WATCH_SOURCE_SELECTOR_SEPARATOR = "@@"
MIN_MARKET_WATCH_SCAN_INTERVAL_SECONDS = 30
DEFAULT_MARKET_WATCH_SCAN_INTERVAL_SECONDS = 300
MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS = 20
MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERN_LENGTH = 500
MAX_MARKET_WATCH_SOURCE_SELECTOR_LENGTH = 500
TradingFrequencyCode = Literal["day", "swing", "position"]
TradingStrategyCode = Literal["value", "trend"]
MarketWatchSourceType = Literal["data", "news"]
DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS = [
    r"(?m)^\s*\*\s*\|\s*$",
    r"(?m)^\s*(?:\[!\[[^\]\n]*\]\([^)\n]*\)\]\([^)\n]*\)\s*)+$",
    r"\\?!\[[^\]\n]*\]\s*\(data:image[^\s)\n]*\)?",
    r"\\?!\[[^\]\n]*\]\s*\([\s\S]*?\)",
    r"(?m)^\s*(?:[-*+]\s*)?(?:\[[^\]\n]*\]\([^)\n]+\)\s*)+$",
    r"\((?://|https?://)[^)\s]+\)",
]


TRADING_FREQUENCY_CODE_MAP: dict[TradingFrequencyCode, str] = {
    "day": "日内交易 (Day Trading)",
    "swing": "波段交易 (Swing Trading)",
    "position": "中长线持有 (Position Trading)",
}
TRADING_STRATEGY_CODE_MAP: dict[TradingStrategyCode, str] = {
    "value": "价值投资 (Value Investing)",
    "trend": "趋势追踪 (Trend Following)",
}


def parse_market_watch_time(value: str) -> time:
    """Parse a market watch HH:mm time string."""
    return time.fromisoformat(value)


def trading_frequency_to_code(value: str | None) -> TradingFrequencyCode:
    """
    Map a localized trading frequency label to the short code used by Watch AI.

    Args:
        value: User-facing trading frequency label or short code.

    Returns:
        Short trading frequency code.
    """
    text = str(value or "").strip().lower()
    if any(token in text for token in ("day", "日内", "短线", "short")):
        return "day"
    if any(token in text for token in ("swing", "波段")):
        return "swing"
    return "position"


def trading_strategy_to_code(value: str | None) -> TradingStrategyCode:
    """
    Map a localized trading strategy label to the short code used by Watch AI.

    Args:
        value: User-facing trading strategy label or short code.

    Returns:
        Short trading strategy code.
    """
    text = str(value or "").strip().lower()
    if any(token in text for token in ("trend", "趋势", "动量", "momentum")):
        return "trend"
    return "value"


def trading_frequency_label(code: TradingFrequencyCode) -> str:
    """
    Return the existing debate label for a short trading frequency code.

    Args:
        code: Short trading frequency code.

    Returns:
        User-facing trading frequency label.
    """
    return TRADING_FREQUENCY_CODE_MAP[code]


def trading_strategy_label(code: TradingStrategyCode) -> str:
    """
    Return the existing debate label for a short trading strategy code.

    Args:
        code: Short trading strategy code.

    Returns:
        User-facing trading strategy label.
    """
    return TRADING_STRATEGY_CODE_MAP[code]


class MarketWatchSourceConfig(BaseModel):
    """One configured web page and optional content selectors."""

    url: str
    content_selectors: list[str] = Field(default_factory=list)


def _split_source_config_values(value: str) -> list[str]:
    if MARKET_WATCH_SOURCE_SELECTOR_SEPARATOR in value:
        return [line for line in value.splitlines() if line.strip()]
    return value.replace("\n", ",").split(",")


def parse_market_watch_source_config(raw_value: Any) -> MarketWatchSourceConfig:
    """
    Parse one market-watch source config entry.

    Args:
        raw_value: A URL, or ``URL @@ selector1 @@ selector2``.

    Returns:
        Normalized URL and optional CSS selectors.
    """
    parts = [part.strip() for part in str(raw_value).split(MARKET_WATCH_SOURCE_SELECTOR_SEPARATOR)]
    text = parts[0] if parts else ""
    if not text:
        raise ValueError("source URLs must include a URL")

    normalized_input = text if "://" in text else f"https://{text}"
    parsed = urlparse(normalized_input)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError("source URLs must use http or https and include a hostname")
    normalized_url = urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

    selectors: list[str] = []
    for selector in parts[1:]:
        if not selector:
            continue
        if len(selector) > MAX_MARKET_WATCH_SOURCE_SELECTOR_LENGTH:
            raise ValueError(f"source selectors must be at most {MAX_MARKET_WATCH_SOURCE_SELECTOR_LENGTH} characters")
        selectors.append(selector)
    return MarketWatchSourceConfig(url=normalized_url, content_selectors=selectors)


def format_market_watch_source_config(config: MarketWatchSourceConfig) -> str:
    """
    Format a source config back to the persisted settings representation.

    Args:
        config: Normalized source config.

    Returns:
        URL-only string, or URL and selectors joined by the configured separator.
    """
    if not config.content_selectors:
        return config.url
    return f" {MARKET_WATCH_SOURCE_SELECTOR_SEPARATOR} ".join([config.url, *config.content_selectors])


def normalize_market_watch_source_urls(value: Any) -> list[str]:
    """
    Normalize user-configured market-watch source URLs.

    Args:
        value: List-like value, or a comma/newline separated string.

    Returns:
        Normalized HTTP(S) URLs with duplicate entries removed.
    """
    if value is None:
        return []
    if isinstance(value, str):
        raw_values = _split_source_config_values(value)
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raise ValueError("source URLs must be a list of HTTP(S) URLs")

    normalized_configs: list[str] = []
    seen_configs: set[str] = set()
    for raw_value in raw_values:
        text = str(raw_value).strip()
        if not text:
            continue
        normalized = format_market_watch_source_config(parse_market_watch_source_config(text))
        if normalized not in seen_configs:
            normalized_configs.append(normalized)
            seen_configs.add(normalized)

    if len(normalized_configs) > MAX_MARKET_WATCH_SOURCE_URLS:
        raise ValueError(f"at most {MAX_MARKET_WATCH_SOURCE_URLS} source URLs are allowed")
    return normalized_configs


def normalize_markdown_cleanup_patterns(value: Any, *, allow_none: bool = False) -> list[str] | None:
    """
    Normalize user-configured Markdown cleanup regex patterns.

    Args:
        value: List-like regex pattern value.
        allow_none: Whether ``None`` should be preserved for partial updates.

    Returns:
        A validated list of regex pattern strings, or ``None`` when allowed.
    """
    if value is None:
        if allow_none:
            return None
        return list(DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS)
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("markdown cleanup patterns must be a list of regex strings")

    patterns: list[str] = []
    for raw_pattern in value:
        pattern = str(raw_pattern).strip()
        if not pattern:
            continue
        if len(pattern) > MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERN_LENGTH:
            raise ValueError(
                f"markdown cleanup patterns must be at most "
                f"{MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERN_LENGTH} characters"
            )
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid markdown cleanup regex: {exc}") from exc
        patterns.append(pattern)

    if len(patterns) > MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS:
        raise ValueError(f"at most {MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS} markdown cleanup patterns are allowed")
    if not patterns:
        return list(DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS)
    return patterns


def clean_market_watch_markdown(markdown: str, patterns: list[str] | None = None) -> str:
    """
    Apply configured cleanup regexes to rendered market-watch Markdown.

    Args:
        markdown: Rendered Markdown returned by the browser tool.
        patterns: Regex patterns to apply in order. Callers should pass the configured setting value.

    Returns:
        Markdown after configured cleanup substitutions.
    """
    cleaned = markdown
    for pattern in patterns or []:
        cleaned = re.compile(pattern).sub("", cleaned)
    return cleaned


class MarketWatchSettingsResponse(BaseModel):
    """Runtime settings returned for a user's market watch automation."""

    user_id: int
    auto_scan_enabled: bool = True
    scan_interval_seconds: int = Field(
        DEFAULT_MARKET_WATCH_SCAN_INTERVAL_SECONDS,
        ge=MIN_MARKET_WATCH_SCAN_INTERVAL_SECONDS,
        le=3600,
    )
    scan_non_trading_days: bool = False
    scan_start_time: str = Field(DEFAULT_MARKET_WATCH_SCAN_START_TIME, pattern=MARKET_WATCH_TIME_PATTERN)
    scan_end_time: str = Field(DEFAULT_MARKET_WATCH_SCAN_END_TIME, pattern=MARKET_WATCH_TIME_PATTERN)
    auto_launch_debate: bool = True
    recent_debate_dedup_enabled: bool = True
    cooldown_minutes: int = Field(60, ge=0, le=1440)
    cooldown_break_confidence: float = Field(0.85, ge=0, le=1)
    data_source_urls: list[str] = Field(default_factory=list, max_length=MAX_MARKET_WATCH_SOURCE_URLS)
    news_source_urls: list[str] = Field(default_factory=list, max_length=MAX_MARKET_WATCH_SOURCE_URLS)
    clean_source_markdown: bool = True
    markdown_cleanup_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS),
        max_length=MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS,
    )
    trading_frequency: str = Field(DEFAULT_MARKET_WATCH_TRADING_FREQUENCY, min_length=1, max_length=50)
    trading_strategy: str = Field(DEFAULT_MARKET_WATCH_TRADING_STRATEGY, min_length=1, max_length=50)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _validate_scan_time_window(self) -> "MarketWatchSettingsResponse":
        if parse_market_watch_time(self.scan_start_time) >= parse_market_watch_time(self.scan_end_time):
            raise ValueError("scan_start_time must be earlier than scan_end_time")
        return self

    @field_validator("data_source_urls", "news_source_urls", mode="before")
    @classmethod
    def _normalize_source_urls(cls, value: Any) -> list[str]:
        return normalize_market_watch_source_urls(value)

    @field_validator("markdown_cleanup_patterns", mode="before")
    @classmethod
    def _normalize_markdown_cleanup_patterns(cls, value: Any) -> list[str]:
        patterns = normalize_markdown_cleanup_patterns(value)
        if patterns is None:
            return []
        return patterns


class MarketWatchSettingsUpdate(BaseModel):
    """Partial update payload for market watch settings."""

    auto_scan_enabled: bool | None = None
    scan_interval_seconds: int | None = Field(None, ge=MIN_MARKET_WATCH_SCAN_INTERVAL_SECONDS, le=3600)
    scan_non_trading_days: bool | None = None
    scan_start_time: str | None = Field(None, pattern=MARKET_WATCH_TIME_PATTERN)
    scan_end_time: str | None = Field(None, pattern=MARKET_WATCH_TIME_PATTERN)
    auto_launch_debate: bool | None = None
    recent_debate_dedup_enabled: bool | None = None
    cooldown_minutes: int | None = Field(None, ge=0, le=1440)
    cooldown_break_confidence: float | None = Field(None, ge=0, le=1)
    data_source_urls: list[str] | None = Field(None, max_length=MAX_MARKET_WATCH_SOURCE_URLS)
    news_source_urls: list[str] | None = Field(None, max_length=MAX_MARKET_WATCH_SOURCE_URLS)
    clean_source_markdown: bool | None = None
    markdown_cleanup_patterns: list[str] | None = Field(None, max_length=MAX_MARKET_WATCH_MARKDOWN_CLEANUP_PATTERNS)
    trading_frequency: str | None = Field(None, min_length=1, max_length=50)
    trading_strategy: str | None = Field(None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def _validate_scan_time_window(self) -> "MarketWatchSettingsUpdate":
        if self.scan_start_time is None or self.scan_end_time is None:
            return self
        if parse_market_watch_time(self.scan_start_time) >= parse_market_watch_time(self.scan_end_time):
            raise ValueError("scan_start_time must be earlier than scan_end_time")
        return self

    @field_validator("data_source_urls", "news_source_urls", mode="before")
    @classmethod
    def _normalize_source_urls(cls, value: Any) -> list[str] | None:
        if value is None:
            return None
        normalized = normalize_market_watch_source_urls(value)
        if not normalized:
            raise ValueError("source URLs must include at least one URL")
        return normalized

    @field_validator("markdown_cleanup_patterns", mode="before")
    @classmethod
    def _normalize_markdown_cleanup_patterns(cls, value: Any) -> list[str] | None:
        return normalize_markdown_cleanup_patterns(value, allow_none=True)


class MarketWatchMarkdownDocument(BaseModel):
    """Rendered Markdown document fetched from a configured market-watch source URL."""

    id: str
    source_type: MarketWatchSourceType
    url: str
    final_url: str | None = None
    title: str | None = None
    markdown: str
    status: int | None = None
    error: str | None = None
    captured_at: datetime


class MarketWatchEventSchema(BaseModel):
    """Structured audit record for market watch scan and decision events."""

    event_id: str | None = None
    user_id: int
    event_type: Literal["scan", "ai_decision", "debate_launched", "debate_skipped", "error"]
    status: Literal["success", "skipped", "failed"]
    watch_ai_decision: dict[str, Any] | list[dict[str, Any]] | None = None
    debate_parameters: dict | None = None
    debate_session_id: str | None = None
    task_id: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DebateParameters(BaseModel):
    """Parameters generated by Watch AI for a full debate run."""

    trading_frequency: TradingFrequencyCode
    trading_strategy: TradingStrategyCode
    simplified: bool = False
    debate_focus: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class WatchAiDecision(BaseModel):
    """Structured JSON decision returned by Watch AI for one stock."""

    stock_code: str
    stock_name: str
    action: Literal["ignore", "monitor", "start_debate"]
    confidence: float = Field(ge=0, le=1)
    urgency: Literal["low", "medium", "high"]
    trigger_reason: str
    evidence_summary: str
    debate_parameters: DebateParameters | None = None


def merge_market_watch_settings(
    existing: MarketWatchSettingsResponse,
    update: MarketWatchSettingsUpdate,
) -> MarketWatchSettingsResponse:
    """
    Merge a partial settings update into an existing settings response.

    This compatibility wrapper keeps existing schema imports working while the
    implementation lives in app.ai.market_watch.settings.
    """
    from app.ai.market_watch.settings import merge_market_watch_settings as _merge_market_watch_settings

    return _merge_market_watch_settings(existing, update)
