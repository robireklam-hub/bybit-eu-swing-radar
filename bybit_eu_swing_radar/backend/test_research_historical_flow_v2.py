from datetime import datetime, timezone

from research_historical_flow_v2 import (
    coverage_summary,
    enrich_opportunity,
    normalize_bybit_funding,
    normalize_bybit_oi,
)


def test_normalization_sorts_deduplicates_and_ignores_bad_rows():
    oi = normalize_bybit_oi(
        [
            {"timestamp": "2000", "openInterest": "110"},
            {"timestamp": "1000", "openInterest": "100"},
            {"timestamp": "2000", "openInterest": "120"},
            {"timestamp": "bad", "openInterest": "999"},
        ]
    )
    funding = normalize_bybit_funding(
        [
            {"fundingRateTimestamp": "2000", "fundingRate": "0.0002"},
            {"fundingRateTimestamp": "1000", "fundingRate": "0.0001"},
        ]
    )
    assert [(p.ts, p.value) for p in oi] == [(1000, 100.0), (2000, 120.0)]
    assert [(p.ts, p.rate) for p in funding] == [(1000, 0.0001), (2000, 0.0002)]


def test_enrichment_is_strictly_point_in_time_and_computes_oi_changes():
    opened = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    opened_ts = int(opened.timestamp())
    oi = normalize_bybit_oi(
        [
            {"timestamp": str((opened_ts - 4 * 3600) * 1000), "openInterest": "80"},
            {"timestamp": str((opened_ts - 3600) * 1000), "openInterest": "100"},
            {"timestamp": str((opened_ts - 5 * 60) * 1000), "openInterest": "120"},
            {"timestamp": str((opened_ts + 5 * 60) * 1000), "openInterest": "999"},
        ]
    )
    funding = normalize_bybit_funding(
        [
            {"fundingRateTimestamp": str((opened_ts - 8 * 3600) * 1000), "fundingRate": "0.0003"},
            {"fundingRateTimestamp": str((opened_ts + 8 * 3600) * 1000), "fundingRate": "0.9"},
        ]
    )
    result = enrich_opportunity(
        {"symbol": "BTCUSDC", "opened_at": opened.isoformat()},
        derivative_symbol="BTCUSDT",
        oi_points=oi,
        funding_points=funding,
    )
    assert result["oi_value"] == 120.0
    assert result["oi_age_seconds"] == 300
    assert round(result["oi_change_1h_pct"], 6) == 20.0
    assert round(result["oi_change_4h_pct"], 6) == 50.0
    assert result["funding_rate"] == 0.0003
    assert result["historical_flow_available"] is True


def test_stale_data_is_not_forward_filled_forever():
    opened = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)
    opened_ts = int(opened.timestamp())
    oi = normalize_bybit_oi(
        [{"timestamp": str((opened_ts - 3 * 3600) * 1000), "openInterest": "100"}]
    )
    funding = normalize_bybit_funding(
        [{"fundingRateTimestamp": str((opened_ts - 24 * 3600) * 1000), "fundingRate": "0.001"}]
    )
    result = enrich_opportunity(
        {"opened_at": opened},
        derivative_symbol="BTCUSDT",
        oi_points=oi,
        funding_points=funding,
    )
    assert result["oi_value"] is None
    assert result["funding_rate"] is None
    assert result["historical_flow_available"] is False


def test_coverage_summary_is_descriptive_not_a_gate():
    summary = coverage_summary(
        [
            {"oi_value": 1.0, "funding_rate": 0.001},
            {"oi_value": 2.0, "funding_rate": None},
            {"oi_value": None, "funding_rate": None},
        ]
    )
    assert summary["rows"] == 3
    assert summary["oi_rows"] == 2
    assert summary["funding_rows"] == 1
    assert summary["both_rows"] == 1
    assert summary["hard_gate_filtering"] is False
