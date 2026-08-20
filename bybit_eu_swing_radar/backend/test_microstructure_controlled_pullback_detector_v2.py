from datetime import datetime, timedelta, timezone

import pytest

from research.microstructure.controlled_pullback_detector_v2 import (
    detector_contract,
    detect_research_events,
)


BASE = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)


def _snapshot():
    thresholds = {
        symbol: {
            "momentum_mid_return_60s_abs_min": 0.005,
            "momentum_aggressive_flow_share_abs_min": 0.20,
            "reacceleration_aggressive_flow_share_abs_min": 0.15,
            "reacceleration_book_pressure_abs_min": 0.20,
        }
        for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC")
    }
    return {
        "calibration_id": "microstructure-controlled-pullback-calibration-v2",
        "experiment_id": "microstructure-controlled-pullback-reacceleration-v2",
        "strategy_version": "0.7.5",
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "threshold_recalibration_allowed": False,
        "forward_start_utc": (BASE + timedelta(seconds=120)).isoformat(),
        "thresholds_by_symbol": thresholds,
        "structural_thresholds": {
            "pullback_retracement_fraction_min": 0.20,
            "pullback_retracement_fraction_max": 0.60,
            "spread_ratio_to_pre_impulse_max": 1.10,
            "top5_depth_ratio_to_pre_impulse_min": 0.90,
            "opposite_structure_break_allowed": False,
        },
    }


def _row(
    symbol: str,
    seconds: int,
    *,
    mid: float = 100.0,
    signed: float = 0.0,
    pressure: str = "neutral",
    spread: float = 2.0,
    depth: float = 1000.0,
):
    if pressure == "positive":
        bid_added, bid_removed, ask_added, ask_removed = 50.0, 10.0, 10.0, 30.0
    elif pressure == "negative":
        bid_added, bid_removed, ask_added, ask_removed = 10.0, 30.0, 50.0, 10.0
    else:
        bid_added = bid_removed = ask_added = ask_removed = 25.0
    return {
        "symbol": symbol,
        "bucket_start": (BASE + timedelta(seconds=seconds)).isoformat(),
        "bucket_seconds": 5,
        "mid": mid,
        "spread_bps": spread,
        "bid_depth_5_quote": depth / 2,
        "ask_depth_5_quote": depth / 2,
        "signed_quote_flow": signed,
        "total_quote_volume": 100.0,
        "bid_added_quote": bid_added,
        "bid_removed_quote": bid_removed,
        "ask_added_quote": ask_added,
        "ask_removed_quote": ask_removed,
        "book_ready": True,
    }


def _long_rows(symbol="BTCUSDC"):
    rows = []
    # Exact 60s pre-impulse baseline: 0..55s.
    for second in range(0, 60, 5):
        rows.append(_row(symbol, second, mid=100.0))
    # Momentum origin and otherwise-flat impulse path: 60..115s.
    for second in range(60, 120, 5):
        rows.append(_row(symbol, second, mid=100.0))
    # Momentum closes at 120s: +1%, aggressive flow agrees.
    rows.append(_row(symbol, 120, mid=101.0, signed=30.0, pressure="positive"))
    # First valid controlled pullback: 30% retracement.
    rows.append(_row(symbol, 125, mid=100.7, signed=0.0))
    # Pre-trigger reacceleration: flow and pressure realign long.
    rows.append(_row(symbol, 130, mid=100.8, signed=25.0, pressure="positive"))
    return rows


def _short_rows(symbol="ETHUSDC"):
    rows = []
    for second in range(0, 60, 5):
        rows.append(_row(symbol, second, mid=100.0))
    for second in range(60, 120, 5):
        rows.append(_row(symbol, second, mid=100.0))
    rows.append(_row(symbol, 120, mid=99.0, signed=-30.0, pressure="negative"))
    rows.append(_row(symbol, 125, mid=99.3, signed=0.0))
    rows.append(_row(symbol, 130, mid=99.2, signed=-25.0, pressure="negative"))
    return rows


def test_detector_contract_is_frozen_label_blind_and_research_only():
    contract = detector_contract()
    assert contract["experiment_id"] == "microstructure-controlled-pullback-reacceleration-v2"
    assert contract["strategy_version"] == "0.7.5"
    assert contract["calibration_id"] == "microstructure-controlled-pullback-calibration-v2"
    assert contract["research_only"] is True
    assert contract["label_blind"] is True
    assert contract["outcome_fields_read"] is False
    assert contract["post_trigger_data_used_for_trigger"] is False
    assert contract["promotion_allowed"] is False
    assert contract["live_strategy_mutation"] is False
    assert contract["momentum_lookback_seconds"] == 60
    assert contract["pre_impulse_baseline_seconds"] == 60
    assert contract["pullback_search_seconds"] == 60
    assert contract["reacceleration_search_seconds"] == 30
    assert contract["event_cooldown_seconds"] == 60
    assert contract["threshold_search_allowed"] is False


def test_long_sequence_emits_one_pretrigger_event_and_momentum_comparator():
    result = detect_research_events(_long_rows(), _snapshot())
    assert len(result["momentum_candidates"]) == 1
    assert len(result["controlled_pullback_events"]) == 1
    event = result["controlled_pullback_events"][0]
    assert event["symbol"] == "BTCUSDC"
    assert event["direction"] == "long"
    assert event["strategy_version"] == "0.7.5"
    assert event["pullback_retracement_fraction"] == pytest.approx(0.30)
    assert event["reacceleration_flow_share"] == pytest.approx(0.25)
    assert event["reacceleration_book_pressure"] == pytest.approx(0.60)
    assert event["trigger_at"] == (BASE + timedelta(seconds=135)).isoformat()
    assert event["outcome_visible"] is False
    assert event["promotion_allowed"] is False
    comparator = result["momentum_candidates"][0]
    assert comparator["comparator_class"] == "MOMENTUM_ONLY_SAME_DIRECTION_SAME_SYMBOL"
    assert comparator["direction"] == "long"


def test_short_sequence_is_direction_symmetric():
    result = detect_research_events(_short_rows(), _snapshot())
    event = result["controlled_pullback_events"][0]
    assert event["symbol"] == "ETHUSDC"
    assert event["direction"] == "short"
    assert event["pullback_retracement_fraction"] == pytest.approx(0.30)
    assert event["reacceleration_flow_share"] == pytest.approx(-0.25)
    assert event["reacceleration_book_pressure"] == pytest.approx(-0.60)


def test_bad_pullback_quality_blocks_event_without_changing_momentum_comparator():
    rows = _long_rows()
    # 2.3 bps > 2.0 * 1.10 frozen spread-quality ceiling.
    rows[-2]["spread_bps"] = 2.3
    result = detect_research_events(rows, _snapshot())
    assert len(result["momentum_candidates"]) == 1
    assert result["controlled_pullback_events"] == []


def test_opposite_origin_break_invalidates_sequence_even_if_price_recovers():
    rows = _long_rows()[:-2]
    rows.append(_row("BTCUSDC", 125, mid=99.9))
    rows.append(_row("BTCUSDC", 130, mid=100.7))
    rows.append(_row("BTCUSDC", 135, mid=100.8, signed=25.0, pressure="positive"))
    result = detect_research_events(rows, _snapshot())
    assert len(result["momentum_candidates"]) == 1
    assert result["controlled_pullback_events"] == []


def test_missing_exact_preimpulse_baseline_fails_closed_for_candidate():
    rows = [row for row in _long_rows() if row["bucket_start"] != (BASE + timedelta(seconds=25)).isoformat()]
    result = detect_research_events(rows, _snapshot())
    assert result["momentum_candidates"] == []
    assert result["controlled_pullback_events"] == []


def test_event_does_not_depend_on_rows_after_trigger():
    base_result = detect_research_events(_long_rows(), _snapshot())
    future_rows = _long_rows() + [
        _row("BTCUSDC", 135, mid=90.0, signed=-100.0, pressure="negative"),
        _row("BTCUSDC", 140, mid=80.0, signed=-100.0, pressure="negative"),
    ]
    future_result = detect_research_events(future_rows, _snapshot())
    assert future_result["controlled_pullback_events"][0] == base_result["controlled_pullback_events"][0]


def test_outcome_fields_and_wrong_calibration_identity_fail_closed():
    rows = _long_rows()
    rows[-1]["net_r"] = 1.0
    with pytest.raises(ValueError, match="forbidden outcome fields"):
        detect_research_events(rows, _snapshot())

    snapshot = _snapshot()
    snapshot["strategy_version"] = "0.7.4"
    with pytest.raises(ValueError, match="strategy_version"):
        detect_research_events(_long_rows(), snapshot)
