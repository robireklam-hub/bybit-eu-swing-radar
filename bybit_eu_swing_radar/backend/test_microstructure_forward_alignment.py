from __future__ import annotations

from datetime import datetime, timedelta, timezone

from research.microstructure.alignment import (
    ALIGNMENT_SQL,
    HYPOTHESES,
    SPEC_VERSION,
    alignment_spec,
    build_feature_rows,
    sample_readiness,
)


def _bucket(signal_id: int, opened_at: datetime, seconds_before: int, *, side: str = "long",
            signed_flow: float = 100.0, volume: float = 200.0,
            imbalance: float = 0.2, spread: float = 2.0,
            microprice_offset_bps: float = 1.0):
    mid = 100.0
    return {
        "signal_id": signal_id,
        "signal_key": f"sig-{signal_id}",
        "strategy_version": "0.7.3",
        "signal_class": "SHADOW",
        "symbol": "BTCUSDC",
        "side": side,
        "opened_at": opened_at,
        "setup_type": "LIQUIDITY_SWEEP",
        "bucket_start": opened_at - timedelta(seconds=seconds_before),
        "bucket_seconds": 5,
        "signed_quote_flow": signed_flow,
        "total_quote_volume": volume,
        "spread_bps": spread,
        "mid": mid,
        "microprice": mid * (1 + microprice_offset_bps / 10_000.0),
        "imbalance_10": imbalance,
        "imbalance_50": imbalance / 2,
        "bid_added_quote": 50.0,
        "bid_removed_quote": 10.0,
        "ask_added_quote": 5.0,
        "ask_removed_quote": 40.0,
        "book_ready": True,
        "book_message_count": 3,
    }


def test_spec_is_preregistered_label_blind_and_directional() -> None:
    spec = alignment_spec()
    assert spec["spec_version"] == SPEC_VERSION
    assert spec["research_only"] is True
    assert spec["live_strategy_mutated"] is False
    assert spec["label_blind"] is True
    assert spec["post_signal_data_used"] is False
    assert spec["windows_seconds"] == [5, 15, 60]
    assert {item["id"] for item in HYPOTHESES} == {
        "H1_FLOW_BOOK_CONCORDANCE",
        "H2_MICROPRICE_DISPLACEMENT",
        "H3_BOOK_CHURN_PRESSURE",
        "H4_SPREAD_COST",
    }


def test_alignment_sql_excludes_outcome_and_post_signal_columns() -> None:
    lowered = ALIGNMENT_SQL.lower()
    for forbidden in ("net_r", "gross_r", "exit_reason", "closed_at", "outcome"):
        assert forbidden not in lowered
    assert "b.bucket_start < j.opened_at" in ALIGNMENT_SQL
    assert "INTERVAL '60 seconds'" in ALIGNMENT_SQL


def test_feature_windows_are_strictly_pre_signal_and_fixed() -> None:
    opened_at = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    rows = [
        _bucket(1, opened_at, 5),
        _bucket(1, opened_at, 10),
        _bucket(1, opened_at, 15),
        _bucket(1, opened_at, 30),
        _bucket(1, opened_at, 60),  # included in 60s window
        _bucket(1, opened_at, -5),  # post-signal row must be ignored defensively
    ]
    feature = build_feature_rows(rows)[0]

    assert feature["feature_cutoff_at"] == opened_at.isoformat()
    assert feature["label_blind"] is True
    assert feature["bucket_count_5s"] == 1
    assert feature["bucket_count_15s"] == 3
    assert feature["bucket_count_60s"] == 5
    assert feature["coverage_ratio_5s"] == 1.0
    assert feature["coverage_ratio_15s"] == 1.0


def test_side_adjustment_flips_direction_for_short() -> None:
    opened_at = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    long_feature = build_feature_rows([_bucket(1, opened_at, 5, side="long")])[0]
    short_feature = build_feature_rows([_bucket(2, opened_at, 5, side="short")])[0]

    assert long_feature["side_flow_ratio_5s"] == -short_feature["side_flow_ratio_5s"]
    assert long_feature["side_imbalance_10_mean_5s"] == -short_feature["side_imbalance_10_mean_5s"]
    assert long_feature["side_microprice_displacement_bps_5s"] == -short_feature["side_microprice_displacement_bps_5s"]
    assert long_feature["side_book_pressure_ratio_5s"] == -short_feature["side_book_pressure_ratio_5s"]


def test_sample_gate_requires_total_and_per_symbol_counts_without_tuning() -> None:
    sparse = [
        {"symbol": "BTCUSDC"},
        {"symbol": "ETHUSDC"},
        {"symbol": "SOLUSDC"},
    ]
    report = sample_readiness(sparse, ("BTCUSDC", "ETHUSDC", "SOLUSDC"))
    assert report["ready_for_preregistered_effect_test"] is False
    assert report["reasons"] == ["insufficient_total_signals", "insufficient_per_symbol_signals"]

    enough = []
    for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC"):
        enough.extend({"symbol": symbol} for _ in range(20))
    report = sample_readiness(enough, ("BTCUSDC", "ETHUSDC", "SOLUSDC"))
    assert report["ready_for_preregistered_effect_test"] is True
    assert report["total_signals"] == 60
