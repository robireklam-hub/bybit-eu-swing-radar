from datetime import datetime, timedelta, timezone

import pytest

from research.swing_liquidity_event_contract import (
    close_satisfies_frozen_trigger,
    eligibility_reasons,
    is_event_eligible,
    is_matured,
    maturity_at,
    pretrigger_snapshot_age_seconds,
    safe_event_metadata,
)


def candidate(side: str = "long") -> dict:
    if side == "long":
        return {
            "symbol": "TESTUSDC",
            "side": "long",
            "expansion_score": 60,
            "direction_score": 40,
            "shortable": False,
            "trigger": {"timeframe": "4H", "price": 100, "requires_close": True},
            "entry_zone": {"low": 100, "high": 102},
            "stop": 95,
            "targets": [110, 116, 122],
        }
    return {
        "symbol": "TESTUSDC",
        "side": "short",
        "expansion_score": 60,
        "direction_score": -40,
        "shortable": True,
        "trigger": {"timeframe": "4H", "price": 100, "requires_close": True},
        "entry_zone": {"low": 98, "high": 100},
        "stop": 105,
        "targets": [90, 84, 78],
    }


def test_preregistered_long_and_short_geometry_are_eligible():
    assert is_event_eligible(candidate("long"))
    assert is_event_eligible(candidate("short"))
    assert close_satisfies_frozen_trigger(candidate("long"), 100.01)
    assert not close_satisfies_frozen_trigger(candidate("long"), 100)
    assert close_satisfies_frozen_trigger(candidate("short"), 99.99)
    assert not close_satisfies_frozen_trigger(candidate("short"), 100)


def test_short_requires_verified_borrowability_and_direction_alignment():
    row = candidate("short")
    row["shortable"] = False
    assert "short_not_verified_borrowable" in eligibility_reasons(row)
    assert not is_event_eligible(row)

    row = candidate("long")
    row["direction_score"] = -40
    assert "direction_not_long_aligned" in eligibility_reasons(row)
    assert not is_event_eligible(row)


def test_score_and_geometry_gates_fail_closed():
    row = candidate("long")
    row["expansion_score"] = 54.99
    row["targets"] = [110]
    reasons = eligibility_reasons(row)
    assert "expansion_below_55" in reasons
    assert "invalid_entry_stop_tp2_geometry" in reasons


def test_snapshot_must_be_strictly_before_trigger_and_within_90_minutes():
    trigger = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)
    assert pretrigger_snapshot_age_seconds(trigger - timedelta(minutes=90), trigger) == 5400
    assert pretrigger_snapshot_age_seconds(trigger - timedelta(minutes=90, seconds=1), trigger) is None
    assert pretrigger_snapshot_age_seconds(trigger, trigger) is None
    assert pretrigger_snapshot_age_seconds(trigger + timedelta(seconds=1), trigger) is None


def test_maturity_is_exactly_ten_days_after_trigger_close():
    trigger = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)
    assert maturity_at(trigger) == trigger + timedelta(days=10)
    assert not is_matured(trigger, trigger + timedelta(days=10) - timedelta(seconds=1))
    assert is_matured(trigger, trigger + timedelta(days=10))


def test_safe_event_metadata_is_label_blind_and_contains_no_outcome():
    trigger_close = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)
    metadata = safe_event_metadata(
        candidate("long"),
        captured_at=trigger_close - timedelta(minutes=30),
        trigger_bar_start_at=trigger_close - timedelta(hours=4),
        trigger_close_at=trigger_close,
    )
    assert metadata["research_only"] is True
    assert metadata["label_blind"] is True
    assert metadata["outcome_visible"] is False
    assert metadata["promotion_allowed"] is False
    assert metadata["pretrigger_snapshot_age_seconds"] == 1800
    assert metadata["entry_midpoint"] == 101
    assert metadata["tp2"] == 116
    for forbidden in ("outcome", "gross_r", "net_r", "mfe_r", "mae_r", "exit_price"):
        assert forbidden not in metadata


def test_safe_event_metadata_rejects_stale_snapshot_and_non_4h_bar():
    trigger_close = datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="snapshot_not_strictly_pretrigger_within_90m"):
        safe_event_metadata(
            candidate("long"),
            captured_at=trigger_close - timedelta(minutes=91),
            trigger_bar_start_at=trigger_close - timedelta(hours=4),
            trigger_close_at=trigger_close,
        )
    with pytest.raises(ValueError, match="exactly 4 hours"):
        safe_event_metadata(
            candidate("long"),
            captured_at=trigger_close - timedelta(minutes=30),
            trigger_bar_start_at=trigger_close - timedelta(hours=3),
            trigger_close_at=trigger_close,
        )
