from datetime import datetime, timedelta, timezone

import pytest

from scripts.swing_liquidity_capture_due import capture_due


NOW = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)


def _status(last_capture_at: str | None):
    return {
        "research_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "last_capture_at": last_capture_at,
    }


def test_primary_hourly_capture_due_after_45_minutes():
    last = (NOW - timedelta(minutes=60)).isoformat()
    due, age = capture_due(_status(last), now=NOW, min_age_seconds=2700)
    assert due is True
    assert age == 3600


def test_primary_skips_recent_backup_capture():
    last = (NOW - timedelta(minutes=30)).isoformat()
    due, age = capture_due(_status(last), now=NOW, min_age_seconds=2700)
    assert due is False
    assert age == 1800


def test_backup_skips_normal_hourly_capture():
    last = (NOW - timedelta(minutes=60)).isoformat()
    due, age = capture_due(_status(last), now=NOW, min_age_seconds=4500)
    assert due is False
    assert age == 3600


def test_backup_runs_when_primary_capture_is_stale():
    last = (NOW - timedelta(minutes=90)).isoformat()
    due, age = capture_due(_status(last), now=NOW, min_age_seconds=4500)
    assert due is True
    assert age == 5400


def test_missing_capture_is_due():
    due, age = capture_due(_status(None), now=NOW, min_age_seconds=4500)
    assert due is True
    assert age is None


def test_future_capture_fails_closed():
    last = (NOW + timedelta(minutes=5)).isoformat()
    with pytest.raises(ValueError, match="future"):
        capture_due(_status(last), now=NOW, min_age_seconds=2700)


def test_research_guards_are_required():
    status = _status((NOW - timedelta(hours=1)).isoformat())
    status["promotion_allowed"] = True
    with pytest.raises(ValueError, match="promotion"):
        capture_due(status, now=NOW, min_age_seconds=2700)
