from datetime import datetime, timezone
from pathlib import Path

import pytest

from research.research_governance import (
    build_point_in_time_provenance,
    manifest_fingerprint,
    snapshot_governance_metadata,
    trial_fingerprint,
    trial_manifest,
    validate_point_in_time,
    validate_trial_registration,
)
from research.swing_liquidity_event_builder import build_trigger_events, select_pretrigger_snapshot
import research.swing_liquidity_shadow as shadow


STUDY = "swing-liquidity-validation-v1"


def _candidate() -> dict:
    return {
        "symbol": "BTCUSDC",
        "side": "long",
        "shortable": False,
        "expansion_score": 60.0,
        "direction_score": 40.0,
        "trigger": {"timeframe": "4H", "requires_close": True, "price": 100.0},
        "entry_zone": {"low": 101.0, "high": 103.0},
        "stop": 95.0,
        "targets": [110.0, 120.0],
    }


def _pit(
    start: str = "2026-08-19T11:58:00+00:00",
    available: str = "2026-08-19T11:59:00+00:00",
) -> dict:
    return build_point_in_time_provenance(
        collection_started_at=start,
        scan_received_at="2026-08-19T11:58:10+00:00",
        orderbooks_completed_at="2026-08-19T11:58:30+00:00",
        feature_computed_at=available,
        feature_available_at=available,
        # Deliberately future-dated: upstream clock skew must not define local PIT validity.
        scan_source_data_as_of="2099-01-01T00:00:00+00:00",
    )


def test_trial_fingerprint_is_deterministic_and_tamper_evident() -> None:
    manifest = trial_manifest(STUDY)
    assert manifest_fingerprint(manifest) == trial_fingerprint(STUDY)
    tampered = dict(manifest)
    tampered["development_target_matured_events"] = 61
    with pytest.raises(ValueError, match="frozen registry"):
        validate_trial_registration(STUDY, tampered, manifest_fingerprint(tampered))


def test_point_in_time_uses_local_stage_order_not_upstream_clock() -> None:
    provenance = _pit()
    available = validate_point_in_time(provenance, decision_time="2026-08-19T12:00:00+00:00")
    assert available == datetime(2026, 8, 19, 11, 59, tzinfo=timezone.utc)

    broken = dict(provenance)
    broken["orderbooks_completed_at"] = "2026-08-19T11:58:05+00:00"
    with pytest.raises(ValueError, match="out of order"):
        validate_point_in_time(broken)

    with pytest.raises(ValueError, match="not available"):
        validate_point_in_time(provenance, decision_time="2026-08-19T11:58:59+00:00")


def test_snapshot_governance_keeps_legacy_but_marks_it_unverified() -> None:
    legacy = snapshot_governance_metadata({"captured_at": "2026-08-19T11:59:00+00:00"})
    assert legacy["point_in_time_verified"] is False
    assert legacy["provenance_version"] == "legacy-captured-at-v0"

    manifest = trial_manifest(STUDY)
    pit = _pit()
    modern = snapshot_governance_metadata(
        {
            "study": STUDY,
            "captured_at": pit["collection_started_at"],
            "feature_available_at": pit["feature_available_at"],
            "point_in_time": pit,
            "trial_id": manifest["trial_id"],
            "trial_manifest": manifest,
            "trial_fingerprint": trial_fingerprint(STUDY),
        }
    )
    assert modern["point_in_time_verified"] is True
    assert modern["feature_available_at"].isoformat() == pit["feature_available_at"]


def test_pretrigger_selection_rejects_capture_that_was_not_yet_available() -> None:
    too_late = {
        "captured_at": "2026-08-19T11:59:00+00:00",
        "available_at": "2026-08-19T12:00:05+00:00",
        "symbol": "BTCUSDC",
        "side": "long",
        "candidate": _candidate(),
    }
    assert (
        select_pretrigger_snapshot(
            [too_late],
            symbol="BTCUSDC",
            side="long",
            trigger_close_at="2026-08-19T12:00:00+00:00",
        )
        is None
    )

    legacy = dict(too_late)
    legacy.pop("available_at")
    assert (
        select_pretrigger_snapshot(
            [legacy],
            symbol="BTCUSDC",
            side="long",
            trigger_close_at="2026-08-19T12:00:00+00:00",
        )
        is legacy
    )


def test_event_metadata_preserves_capture_but_ages_from_availability() -> None:
    snapshot = {
        "captured_at": "2026-08-19T11:58:00+00:00",
        "available_at": "2026-08-19T11:59:00+00:00",
        "feature_available_at": "2026-08-19T11:59:00+00:00",
        "point_in_time_verified": True,
        "provenance_version": "pit-v1",
        "trial_id": STUDY,
        "trial_fingerprint": trial_fingerprint(STUDY),
        "symbol": "BTCUSDC",
        "side": "long",
        "candidate": _candidate(),
    }
    events = build_trigger_events(
        [snapshot],
        [
            {
                "start_at": "2026-08-19T08:00:00+00:00",
                "close_at": "2026-08-19T12:00:00+00:00",
                "close": 105.0,
            }
        ],
        symbol="BTCUSDC",
        side="long",
    )
    assert len(events) == 1
    event = events[0]
    assert event["pretrigger_captured_at"] == "2026-08-19T11:58:00+00:00"
    assert event["pretrigger_available_at"] == "2026-08-19T11:59:00+00:00"
    assert event["pretrigger_snapshot_age_seconds"] == 60.0
    assert event["point_in_time_verified"] is True
    assert event["trial_id"] == STUDY


def test_collector_emits_pit_v1_and_registered_trial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        shadow,
        "_get_json",
        lambda *args, **kwargs: {
            "data_as_of": "2026-08-19T10:00:00+00:00",
            "data_quality": "GOOD",
            "longs": [],
            "shorts": [],
            "extended_watchlist": [],
            "liquidity_blocked": [],
        },
    )
    snapshot = shadow.collect_snapshot("https://example.invalid", "secret")
    assert snapshot["point_in_time"]["version"] == "pit-v1"
    assert snapshot["feature_available_at"] == snapshot["point_in_time"]["feature_available_at"]
    assert snapshot["trial_id"] == STUDY
    assert snapshot["trial_fingerprint"] == trial_fingerprint(STUDY)
    assert snapshot["candidate_count"] == 0


def test_persistence_schema_has_backward_compatible_pit_columns() -> None:
    source = Path("app/research_swing_liquidity_api.py").read_text()
    for column in ("feature_available_at", "provenance_version", "trial_id", "trial_fingerprint"):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in source
    assert "COALESCE(c.feature_available_at, o.captured_at) AS available_at" in source
    assert "point_in_time_verified_captures" in source
