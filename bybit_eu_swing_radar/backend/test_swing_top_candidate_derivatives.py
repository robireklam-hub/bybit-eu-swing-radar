from __future__ import annotations

from datetime import datetime, timezone

from app.models import TopCandidatesResponse
from app.swing_candidate_context import attach_swing_candidate_derivatives


NOW = datetime(2026, 8, 17, 9, 0, tzinfo=timezone.utc)


def compact_response() -> TopCandidatesResponse:
    return TopCandidatesResponse.model_validate(
        {
            "data_as_of": NOW,
            "data_as_of_budapest": "2026-08-17T11:00:00+02:00",
            "data_quality": "GOOD",
            "market_regime": {},
            "requested_limit": 3,
            "strict_long_count": 1,
            "strict_short_count": 0,
            "strict_longs": [
                {
                    "symbol": "BTCUSDC",
                    "side": "long",
                    "category": "STRICT",
                    "state": "ARMED",
                    "grade": "B",
                    "decision": "WAIT",
                    "last_price": 100.0,
                    "setup_score": 75.0,
                    "expansion_score": 70.0,
                    "direction_score": 60.0,
                    "quality_score": 75.0,
                    "tradeable": True,
                    "execution_status": "EXECUTION_ELIGIBLE",
                    "data_quality": "GOOD",
                }
            ],
            "strict_shorts": [],
            "watch_only_longs": [],
            "watch_only_shorts": [],
            "notes": [],
        }
    )


def scan_payload(derivatives: dict) -> dict:
    setup = {
        "symbol": "BTCUSDC",
        "side": "long",
        "state": "ARMED",
        "grade": "B",
        "confidence": "MEDIUM",
        "last_price": 100.0,
        "shortable": True,
        "execution_modes": ["spot"],
        "expansion_score": 70.0,
        "direction_score": 60.0,
        "quality_score": 75.0,
        "setup_score": 75.0,
        "expected_rr": 2.2,
        "metrics": {
            "tradeable": True,
            "execution_status": "EXECUTION_ELIGIBLE",
            "derivatives": derivatives,
        },
        "data_quality": "GOOD",
        "missing_data": [],
        "data_as_of": NOW,
    }
    regime = {
        "data_as_of": NOW,
        "data_quality": "GOOD",
        "btc_regime": "range",
        "volatility_regime": "normal",
        "preferred_side": "neutral",
    }
    return {
        "data_as_of": NOW,
        "data_as_of_budapest": "2026-08-17T11:00:00+02:00",
        "data_quality": "GOOD",
        "market_regime": regime,
        "longs": [setup],
        "shorts": [],
        "extended_watchlist": [],
        "liquidity_blocked": [],
    }


def test_compact_candidate_exposes_candidate_level_derivatives_context():
    derivatives = {
        "source": "Coinalyze",
        "market_symbol": "BTCUSDT_PERP.A",
        "exchange": "Binance",
        "open_interest_usd": 1_250_000.0,
        "oi_change_1h_pct": 1.2,
        "oi_change_4h_pct": 3.4,
        "oi_change_24h_pct": 8.9,
        "funding_rate": 0.0003,
        "long_liquidations_24h_usd": 10_000.0,
        "short_liquidations_24h_usd": 5_000.0,
        "availability": {
            "current_oi": True,
            "funding": True,
            "oi_history": True,
            "liquidations": True,
        },
        "strict_score_mutation_applied": False,
        "endpoint_errors": [],
    }

    result = attach_swing_candidate_derivatives(
        compact_response(), scan_payload(derivatives)
    )
    candidate = result["strict_longs"][0]

    assert candidate["derivatives"] == derivatives
    assert candidate["derivatives_status"] == "GOOD"
    assert candidate["derivatives_data_as_of"] == NOW.isoformat()
    assert candidate["derivatives_context_only"] is True
    assert candidate["setup_score"] == 75.0
    assert candidate["decision"] == "WAIT"
    assert candidate["tradeable"] is True


def test_missing_or_partial_derivatives_never_changes_candidate_eligibility():
    partial = {
        "source": "Coinalyze",
        "open_interest_usd": 1_250_000.0,
        "funding_rate": None,
        "availability": {
            "current_oi": True,
            "funding": False,
            "oi_history": True,
            "liquidations": True,
        },
        "strict_score_mutation_applied": False,
        "endpoint_errors": ["funding-rate: RuntimeError: unavailable"],
    }
    partial_result = attach_swing_candidate_derivatives(
        compact_response(), scan_payload(partial)
    )["strict_longs"][0]
    missing_result = attach_swing_candidate_derivatives(
        compact_response(), scan_payload({})
    )["strict_longs"][0]

    assert partial_result["derivatives_status"] == "PARTIAL"
    assert missing_result["derivatives_status"] == "UNAVAILABLE"
    assert missing_result["derivatives_data_as_of"] is None
    for candidate in (partial_result, missing_result):
        assert candidate["category"] == "STRICT"
        assert candidate["setup_score"] == 75.0
        assert candidate["decision"] == "WAIT"
        assert candidate["tradeable"] is True
