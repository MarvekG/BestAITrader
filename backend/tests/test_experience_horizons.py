import pytest

from app.ai.experience.horizons import (
    HORIZON_REQUIRED_MARKET_DAYS,
    eligible_horizons,
    highest_horizon,
    horizon_gap,
    normalize_review_horizon,
    review_status_for_candidate,
)


def test_eligible_horizons_use_decision_day_in_market_day_count():
    assert HORIZON_REQUIRED_MARKET_DAYS == {"5d": 6, "20d": 21, "60d": 61}
    assert eligible_horizons(0) == []
    assert eligible_horizons(5) == []
    assert eligible_horizons(6) == ["5d"]
    assert eligible_horizons(21) == ["5d", "20d"]
    assert eligible_horizons(61) == ["5d", "20d", "60d"]


def test_highest_horizon_returns_longest_available_horizon():
    assert highest_horizon([]) is None
    assert highest_horizon(["5d"]) == "5d"
    assert highest_horizon(["5d", "20d"]) == "20d"
    assert highest_horizon(["5d", "20d", "60d"]) == "60d"


def test_horizon_gap_reports_missing_market_days():
    assert horizon_gap(0, "5d") == 6
    assert horizon_gap(6, "5d") == 0
    assert horizon_gap(20, "20d") == 1
    assert horizon_gap(61, "60d") == 0


def test_normalize_review_horizon_rejects_unknown_values():
    assert normalize_review_horizon("5D") == "5d"
    assert normalize_review_horizon("20d") == "20d"
    assert normalize_review_horizon(None) is None
    with pytest.raises(ValueError, match="Unsupported review horizon"):
        normalize_review_horizon("10d")


def test_review_status_prefers_unreviewed_ready_horizon():
    status = review_status_for_candidate(
        eligible=["5d", "20d", "60d"],
        completed=["5d", "20d"],
        active=[],
        failed=[],
    )
    assert status == "ready_60d"


def test_review_status_handles_active_review_before_ready_state():
    status = review_status_for_candidate(
        eligible=["5d", "20d"],
        completed=[],
        active=["20d"],
        failed=[],
    )
    assert status == "reviewing"


def test_review_status_reports_reviewed_when_all_eligible_horizons_completed():
    status = review_status_for_candidate(
        eligible=["5d", "20d"],
        completed=["5d", "20d"],
        active=[],
        failed=[],
    )
    assert status == "reviewed"
