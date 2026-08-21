from dataclasses import dataclass

import pytest

from day_v076 import (
    active_structural_breakout_context,
    classify_entry_state,
    fresh_entry_zone,
    hard_stop_contract,
    technical_setup_valid,
)


@dataclass
class Bar:
    start_ms: int
    open: float
    high: float
    low: float
    close: float


def _bars_for_persistent_long() -> list[Bar]:
    # 12-bar range capped at 100, then breakout on bar 12. Four additional
    # closed bars stay above the original boundary. v0.7.5 would have expired;
    # v0.7.6 setup context must remain active because structure still holds.
    rows = []
    for i in range(12):
        rows.append(Bar(i * 300_000, 98.0, 100.0, 97.0, 99.0))
    rows.append(Bar(12 * 300_000, 99.0, 102.0, 98.5, 101.0))
    rows.extend(
        [
            Bar(13 * 300_000, 101.0, 103.0, 100.2, 102.0),
            Bar(14 * 300_000, 102.0, 104.0, 100.4, 103.0),
            Bar(15 * 300_000, 103.0, 104.5, 100.6, 102.5),
            Bar(16 * 300_000, 102.5, 105.0, 100.7, 104.0),
        ]
    )
    return rows


def test_structural_breakout_context_has_no_fixed_two_bar_ttl():
    context = active_structural_breakout_context(_bars_for_persistent_long(), "long")
    assert context is not None
    assert context["trigger_price"] == pytest.approx(100.0)
    assert context["age_bars"] == 4
    assert context["validity_bars"] is None
    assert context["boundary_held"] is True
    assert context["persistence_mode"] == "STRUCTURE_HELD_NO_FIXED_BAR_TTL"
    assert context["origin_policy"] == "FIRST_CROSSING_IN_UNINTERRUPTED_SEQUENCE_NO_RATCHET"


def test_continuation_cross_of_new_rolling_high_does_not_ratchet_origin():
    rows = [Bar(i * 300_000, 98.0, 100.0, 97.0, 99.0) for i in range(12)]
    rows[-1] = Bar(11 * 300_000, 98.0, 100.0, 97.0, 99.8)
    # First breakout origin: prior high = 100.0. Its high becomes 100.4.
    rows.append(Bar(12 * 300_000, 99.8, 100.4, 99.5, 100.2))
    # This next bar also crosses the newer rolling high 100.4. It must remain
    # continuation of the 100.0 origin, not become a new age=0 breakout.
    rows.append(Bar(13 * 300_000, 100.2, 100.9, 100.1, 100.7))

    context = active_structural_breakout_context(rows, "long")
    assert context is not None
    assert context["trigger_price"] == pytest.approx(100.0)
    assert context["event_bar_start_ms"] == 12 * 300_000
    assert context["age_bars"] == 1


def test_new_breakout_can_start_only_after_old_boundary_was_lost():
    rows = [Bar(i * 300_000, 98.0, 100.0, 97.0, 99.0) for i in range(12)]
    rows[-1] = Bar(11 * 300_000, 98.0, 100.0, 97.0, 99.8)
    rows.append(Bar(12 * 300_000, 99.8, 100.4, 99.5, 100.2))
    # Original 100.0 boundary is lost; first sequence must die.
    rows.append(Bar(13 * 300_000, 100.2, 100.3, 99.4, 99.7))
    # Build enough later bars below a lower local ceiling, then cross it.
    for i in range(14, 26):
        rows.append(Bar(i * 300_000, 99.0, 99.8, 98.5, 99.2))
    rows.append(Bar(26 * 300_000, 99.2, 100.2, 99.0, 100.0))

    context = active_structural_breakout_context(rows, "long")
    assert context is not None
    assert context["event_bar_start_ms"] == 26 * 300_000
    assert context["age_bars"] == 0
    assert context["trigger_price"] == pytest.approx(99.8)


def test_structural_breakout_dies_after_any_closed_boundary_loss():
    bars = _bars_for_persistent_long()
    bars[15] = Bar(15 * 300_000, 101.0, 102.0, 98.0, 99.5)
    bars[16] = Bar(16 * 300_000, 99.5, 105.0, 99.0, 104.0)
    assert active_structural_breakout_context(bars, "long") is None


def test_barrier_can_block_entry_without_erasing_valid_setup():
    setup_valid = technical_setup_valid(
        setup_score=71.88,
        expansion_score=62.05,
        side_direction_score=57.6,
        quality_score=99.99,
        minimum_setup_score=70.0,
        minimum_expansion_score=55.0,
        minimum_direction_score=35.0,
        minimum_quality_score=65.0,
    )
    assert setup_valid is True
    assert classify_entry_state(
        setup_valid=setup_valid,
        execution_valid=True,
        rr_valid=False,
        target_path_valid=False,
        barrier_blocked=True,
        confirmed_trigger=True,
        persistent_breakout_context=True,
        extension_atr=0.4,
    ) == "BLOCKED_BY_BARRIER"


def test_after_barrier_clear_persistent_setup_becomes_provisional_not_no_trade():
    assert classify_entry_state(
        setup_valid=True,
        execution_valid=True,
        rr_valid=True,
        target_path_valid=True,
        barrier_blocked=False,
        confirmed_trigger=False,
        persistent_breakout_context=True,
        extension_atr=0.45,
    ) == "ENTRY_PROVISIONAL"


def test_extended_persistent_setup_waits_for_retest():
    assert classify_entry_state(
        setup_valid=True,
        execution_valid=True,
        rr_valid=True,
        target_path_valid=True,
        barrier_blocked=False,
        confirmed_trigger=False,
        persistent_breakout_context=True,
        extension_atr=1.25,
    ) == "ENTRY_TOO_EXTENDED"


def test_fresh_entry_zone_uses_current_price_not_old_breakout_boundary():
    low, high = fresh_entry_zone(current_price=70_050.0, atr_5m=200.0, side="long")
    assert low == pytest.approx(70_050.0)
    assert high == pytest.approx(70_080.0)


def test_hard_stop_never_requires_five_minute_close():
    contract = hard_stop_contract(stop_price=76_526.0, side="long")
    assert contract == {
        "price": 76_526.0,
        "activation": "INTRABAR_TOUCH_OR_CROSS",
        "requires_candle_close": False,
        "condition": "price <= hard stop",
    }
