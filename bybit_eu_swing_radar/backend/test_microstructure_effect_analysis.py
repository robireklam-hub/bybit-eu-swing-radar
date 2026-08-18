from datetime import datetime, timedelta, timezone

from research.microstructure.effect_analysis import (
    analyze_preregistered_effects,
    select_earliest_ready_cohort,
    spearman,
)


def _feature(signal_id: int, symbol: str, opened_at: datetime, value: float) -> dict:
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "opened_at": opened_at.isoformat(),
        "flow_book_concordance_60s": value,
        "side_microprice_displacement_bps_15s": value,
        "side_book_pressure_ratio_60s": value,
        "spread_bps_mean_15s": -value,
    }


def test_selects_earliest_prefix_without_outcomes():
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    symbols = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
    rows = []
    for i in range(60):
        rows.append(_feature(i + 1, symbols[i % 3], start + timedelta(minutes=i), float(i)))
    cohort, gate = select_earliest_ready_cohort(rows, symbols)
    assert gate["cohort_frozen"] is True
    assert len(cohort) == 60
    assert gate["total_signals"] == 60
    assert gate["per_symbol"] == {"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}


def test_cohort_stays_closed_below_sample_gate():
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    rows = [_feature(i + 1, "BTCUSDC", start + timedelta(minutes=i), float(i)) for i in range(59)]
    cohort, gate = select_earliest_ready_cohort(rows, ["BTCUSDC", "ETHUSDC", "SOLUSDC"])
    assert cohort == []
    assert gate["cohort_frozen"] is False
    assert "insufficient_total_signals" in gate["reasons"]
    assert "insufficient_per_symbol_signals" in gate["reasons"]


def test_effect_analysis_waits_until_every_frozen_outcome_is_closed():
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    symbols = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
    cohort = [
        _feature(i + 1, symbols[i % 3], start + timedelta(minutes=i), float(i))
        for i in range(60)
    ]
    outcomes = [{"signal_id": row["signal_id"], "net_r": 1.0} for row in cohort[:-1]]
    result = analyze_preregistered_effects(cohort, outcomes)
    assert result["status"] == "WAITING_FOR_CLOSED_OUTCOMES"
    assert result["missing_outcomes"] == 1
    assert result["promotion_allowed"] is False


def test_preregistered_directions_are_detected_on_stable_synthetic_blocks():
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    symbols = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]
    cohort = []
    outcomes = []
    signal_id = 1
    # Ten 6-hour blocks, six observations each. Feature ranks and net-R ranks are
    # perfectly concordant; spread is perfectly inverse by construction.
    for block in range(10):
        block_start = start + timedelta(hours=6 * block)
        for j in range(6):
            value = float(block * 10 + j + 1)
            symbol = symbols[signal_id % 3]
            cohort.append(_feature(signal_id, symbol, block_start + timedelta(minutes=5 * j), value))
            outcomes.append({"signal_id": signal_id, "net_r": value})
            signal_id += 1
    result = analyze_preregistered_effects(cohort, outcomes)
    assert result["status"] == "COMPLETE"
    assert len(result["results"]) == 4
    assert all(item["verdict"] == "SUPPORTED" for item in result["results"])
    assert result["promotion_decision"] == "NO_PROMOTION_REQUIRES_SUBSEQUENT_UNTOUCHED_VALIDATION"


def test_spearman_handles_ties_without_scipy_dependency():
    assert round(spearman([1, 2, 2, 4], [1, 2, 2, 4]), 12) == 1.0
