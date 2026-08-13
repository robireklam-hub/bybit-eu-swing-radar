from datetime import datetime, timedelta, timezone

import pytest

from flow_context import build_flow_payload


NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
INSTRUMENT = {
    "symbol": "BTCUSDT",
    "baseCoin": "BTC",
    "quoteCoin": "USDT",
    "contractType": "LinearPerpetual",
}
TICKER = {"openInterest": "100", "fundingRate": "0.0001"}
OI_HISTORY = [
    {"timestamp": str(int((NOW - timedelta(minutes=15)).timestamp() * 1000)), "openInterest": "90"},
    {"timestamp": str(int(NOW.timestamp() * 1000)), "openInterest": "100"},
]


def build(data_as_of):
    return build_flow_payload(
        spot_symbol="BTCUSDC",
        setup_payload={"data_as_of": data_as_of, "metrics": {}},
        derivative_instrument=INSTRUMENT,
        derivative_ticker=TICKER,
        oi_history=OI_HISTORY,
        generated_at=NOW,
    )


def test_fresh_spot_context_remains_good():
    payload = build((NOW - timedelta(seconds=300)).isoformat())

    assert payload["spot_snapshot_age_seconds"] == pytest.approx(300)
    assert payload["data_quality"] == "GOOD"
    assert payload["coverage_status"] == "GOOD"


def test_spot_context_older_than_five_minutes_is_stale():
    payload = build((NOW - timedelta(seconds=301)).isoformat())

    assert payload["spot_snapshot_age_seconds"] == pytest.approx(301)
    assert payload["data_quality"] == "DEGRADED"
    assert payload["coverage_status"] == "STALE_SPOT_CONTEXT"


@pytest.mark.parametrize("data_as_of", [None, "", "not-a-timestamp"])
def test_unknown_spot_freshness_is_stale(data_as_of):
    payload = build(data_as_of)

    assert payload["spot_snapshot_age_seconds"] is None
    assert payload["data_quality"] == "DEGRADED"
    assert payload["coverage_status"] == "STALE_SPOT_CONTEXT"
