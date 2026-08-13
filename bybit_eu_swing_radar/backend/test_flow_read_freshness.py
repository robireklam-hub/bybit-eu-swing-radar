from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.flow_freshness import apply_flow_freshness, summarize_flow_payloads


NOW = datetime.now(timezone.utc)


def payload(symbol="PENGUUSDC", age=0, coverage="GOOD", timestamp=True, batch="batch-a"):
    return {
        "strategy_mode": "DAY_TRADE", "strategy_version": "0.7.2",
        "feature_version": "0.7.2.2", "symbol": symbol,
        "data_as_of": (NOW - timedelta(seconds=age)).isoformat() if timestamp else None,
        "data_as_of_budapest": NOW.isoformat(), "data_quality": "GOOD",
        "coverage_status": coverage,
        "flow_batch_id": batch,
    }


@pytest.mark.parametrize("age", [299.999, 300])
def test_read_freshness_keeps_boundary_good(age):
    cached = payload(age=age)
    assert apply_flow_freshness(cached, reference_time=NOW) == cached


def test_read_freshness_degrades_just_past_boundary_without_mutation():
    cached = payload(age=300.001)
    original = deepcopy(cached)
    result = apply_flow_freshness(cached, reference_time=NOW)
    assert result["data_quality"] == "DEGRADED"
    assert result["coverage_status"] == "STALE_FLOW_CONTEXT"
    assert cached == original


@pytest.mark.parametrize("value", [None, "bad-timestamp"])
def test_read_freshness_rejects_unknown_canonical_timestamp(value):
    cached = payload()
    cached["data_as_of"] = value
    assert apply_flow_freshness(cached, reference_time=NOW)["coverage_status"] != "GOOD"


@pytest.mark.parametrize("coverage", [
    "NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH",
    "PARTIAL_OI_HISTORY_ERROR",
])
def test_stale_specific_coverage_reason_is_preserved(coverage):
    result = apply_flow_freshness(
        payload(age=301, coverage=coverage), reference_time=NOW
    )
    assert result["data_quality"] == "DEGRADED"
    assert result["coverage_status"] == coverage


def test_context_read_degrades_cached_good_without_cache_write():
    cached = payload(age=301)
    original = deepcopy(cached)
    result = apply_flow_freshness(cached, reference_time=NOW)
    assert result["data_quality"] == "DEGRADED" and result["coverage_status"] != "GOOD"
    assert cached == original


def test_context_read_keeps_fresh_wif_good():
    result = apply_flow_freshness(payload("WIFUSDC", age=0), reference_time=NOW)
    assert result["data_quality"] == "GOOD" and result["coverage_status"] == "GOOD"


def test_status_recounts_only_current_batch():
    result = summarize_flow_payloads(
        [
            payload("WIFUSDC", age=0),
            payload("PENGUUSDC", age=301),
            payload("NOMATCHUSDC", age=301, coverage="NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH"),
        ],
        flow_batch_id="batch-a",
        reference_time=NOW,
    )
    assert result["good"] == 1
    assert result["partial"] == 1
    assert result["no_derivative_match"] == 1
    assert 3 == sum(result[key] for key in ("good", "partial", "no_derivative_match"))
