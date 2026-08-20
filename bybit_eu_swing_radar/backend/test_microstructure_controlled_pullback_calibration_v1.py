from datetime import datetime, timedelta, timezone

import pytest

from research.microstructure.controlled_pullback_calibration_v1 import (
    CALIBRATION_ID,
    MIN_ROWS_PER_SYMBOL,
    calibration_contract,
    derive_thresholds,
)


START = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
CUTOFF = START - timedelta(seconds=5)


def rows_per_symbol(count: int = MIN_ROWS_PER_SYMBOL):
    rows = []
    for symbol_index, symbol in enumerate(("BTCUSDC", "ETHUSDC", "SOLUSDC"), start=1):
        for index in range(count):
            rows.append(
                {
                    "symbol": symbol,
                    "bucket_start": CUTOFF - timedelta(seconds=5 * (index + 1)),
                    "mid_return_60s_abs": symbol_index * 0.001 + index * 0.00001,
                    "aggressive_flow_share_abs": symbol_index * 0.01 + index * 0.0001,
                    "book_pressure_abs": symbol_index * 0.02 + index * 0.0002,
                }
            )
    return rows


def test_calibration_contract_is_research_only_and_outcome_blind():
    contract = calibration_contract()
    assert contract["calibration_id"] == CALIBRATION_ID
    assert contract["strategy_version"] == "0.7.4"
    assert contract["research_only"] is True
    assert contract["label_blind"] is True
    assert contract["outcomes_permitted"] is False
    assert contract["governance"]["outcome_conditioned_threshold_search"] is False
    assert contract["governance"]["threshold_recalibration_after_activation"] is False
    assert contract["governance"]["live_strategy_mutation"] is False


def test_threshold_derivation_is_deterministic_and_symbol_specific():
    rows = rows_per_symbol()
    first = derive_thresholds(
        rows,
        calibration_until_utc=CUTOFF,
        forward_start_utc=START,
    )
    second = derive_thresholds(
        list(reversed(rows)),
        calibration_until_utc=CUTOFF.isoformat(),
        forward_start_utc=START.isoformat(),
    )
    assert first["thresholds_by_symbol"] == second["thresholds_by_symbol"]
    assert first["sample_rows_per_symbol"] == {
        "BTCUSDC": MIN_ROWS_PER_SYMBOL,
        "ETHUSDC": MIN_ROWS_PER_SYMBOL,
        "SOLUSDC": MIN_ROWS_PER_SYMBOL,
    }
    assert first["thresholds_by_symbol"]["BTCUSDC"] != first["thresholds_by_symbol"]["SOLUSDC"]
    assert first["outcome_visible"] is False
    assert first["promotion_allowed"] is False
    assert first["threshold_recalibration_allowed"] is False
    assert first["live_strategy_mutation"] is False


def test_outcome_or_unknown_fields_fail_closed():
    rows = rows_per_symbol()
    rows[0]["direction_normalized_return_15m"] = 0.05
    with pytest.raises(ValueError, match="non-feature fields"):
        derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)


def test_rows_at_or_after_cutoff_fail_closed():
    rows = rows_per_symbol()
    rows[0]["bucket_start"] = CUTOFF
    with pytest.raises(ValueError, match="not strictly pre-activation"):
        derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)


def test_calibration_cutoff_must_precede_forward_start():
    with pytest.raises(ValueError, match="strictly before forward start"):
        derive_thresholds([], calibration_until_utc=START, forward_start_utc=START)


def test_each_symbol_requires_minimum_pre_activation_sample():
    rows = rows_per_symbol(MIN_ROWS_PER_SYMBOL)
    rows = [row for row in rows if not (row["symbol"] == "BTCUSDC" and row["bucket_start"] == CUTOFF - timedelta(seconds=5))]
    with pytest.raises(ValueError, match="insufficient pre-activation calibration rows for BTCUSDC"):
        derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)
