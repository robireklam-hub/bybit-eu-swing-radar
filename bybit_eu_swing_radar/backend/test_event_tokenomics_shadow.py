from datetime import datetime, timezone

from research.event_tokenomics_shadow import (
    build_snapshot,
    event_window,
    severity_from_impact,
    severity_from_unlock_pct_market_cap,
)

NOW = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)


def test_event_windows_are_forward_safe() -> None:
    assert event_window("2026-08-18T05:30:00Z", NOW) == "PAST_24H"
    assert event_window("2026-08-18T12:00:00Z", NOW) == "NEXT_24H"
    assert event_window("2026-08-20T06:00:00Z", NOW) == "NEXT_3D"
    assert event_window("2026-08-23T06:00:00Z", NOW) == "NEXT_7D"
    assert event_window("2026-09-10T06:00:00Z", NOW) == "NEXT_30D"


def test_frozen_severity_mappings() -> None:
    assert severity_from_impact(9.1) == "CRITICAL"
    assert severity_from_impact(8.2) == "HIGH"
    assert severity_from_impact(6.7) == "MEDIUM_HIGH"
    assert severity_from_unlock_pct_market_cap(5.1) == "CRITICAL"
    assert severity_from_unlock_pct_market_cap(2.1) == "HIGH"
    assert severity_from_unlock_pct_market_cap(0.7) == "MEDIUM_HIGH"


def test_snapshot_deduplicates_and_keeps_missing_key_explicit() -> None:
    event = {
        "event_id": "test:1",
        "event_type": "TOKEN_UNLOCK",
        "title": "Test unlock",
        "event_at": "2026-08-19T06:00:00Z",
        "severity": "HIGH",
        "symbols": ["BTCUSDC"],
        "source": {"name": "test"},
    }
    snapshot = build_snapshot(
        [event, event],
        {
            "official": {"status": "LIVE", "events": 1},
            "optional": {"status": "MISSING_KEY", "events": 0},
        },
        ["BTCUSDC", "ETHUSDC"],
        captured_at=NOW,
        source_commit_sha="abc",
    )
    assert snapshot["event_count"] == 1
    assert snapshot["events"][0]["window"] == "NEXT_3D"
    assert snapshot["coverage"]["live_sources"] == ["official"]
    assert snapshot["coverage"]["missing_key_sources"] == ["optional"]
    assert snapshot["promotion_allowed"] is False
    assert snapshot["live_strategy_mutated"] is False
