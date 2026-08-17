from __future__ import annotations

from scripts.production_swing_api_consistency_smoke import evaluate


def _candidate(symbol: str = "AAAUSDC") -> dict:
    return {
        "symbol": symbol,
        "derivatives_status": "PARTIAL",
        "derivatives_status_reason": (
            "Unavailable Coinalyze coverage: liquidations. "
            "Missing derivatives fields: oi_change_24h_pct, "
            "long_liquidations_24h_usd, short_liquidations_24h_usd."
        ),
        "derivatives": {
            "open_interest_usd": 1000.0,
            "oi_change_1h_pct": 1.0,
            "oi_change_4h_pct": 2.0,
            "oi_change_24h_pct": None,
            "funding_rate": 0.0001,
            "long_liquidations_24h_usd": None,
            "short_liquidations_24h_usd": None,
        },
    }


def _top() -> dict:
    return {
        "data_as_of": "2026-08-17T12:30:00Z",
        "strict_longs": [_candidate()],
        "strict_shorts": [],
        "watch_only_longs": [],
        "watch_only_shorts": [],
    }


def _status() -> dict:
    return {
        "worker": {
            "source_commit_sha": "abc",
            "extended_watchlist_items": 1,
            "liquidity_blocked_items": 1,
        }
    }


def _compact() -> dict:
    return {
        "data_as_of": "2026-08-17T12:30:00Z",
        "longs": [{"symbol": "AAAUSDC"}],
        "shorts": [],
        "extended_watchlist": [],
        "liquidity_blocked": [],
        "momentum_radar": [],
        "exclusions": [],
    }


def _full() -> dict:
    return {
        "data_as_of": "2026-08-17T12:30:00Z",
        "longs": [{"symbol": "AAAUSDC"}],
        "shorts": [],
        "extended_watchlist": [{"symbol": "BBBUSDCC"}],
        "liquidity_blocked": [{"symbol": "CCCUSDC"}],
    }


def test_evaluate_accepts_compact_agent_view_and_full_research_view() -> None:
    setups = {
        "AAAUSDC": {
            "symbol": "AAAUSDC",
            "data_as_of": "2026-08-17T12:30:00+00:00",
        }
    }
    assert evaluate(_compact(), _full(), _top(), setups, _status(), "abc") == []


def test_evaluate_rejects_stale_symbol_setup_and_incomplete_partial_reason() -> None:
    top = _top()
    top["strict_longs"][0]["derivatives_status_reason"] = "liquidations missing"
    setups = {
        "AAAUSDC": {
            "symbol": "AAAUSDC",
            "data_as_of": "2026-08-17T10:30:00Z",
        }
    }
    failures = evaluate(_compact(), _full(), top, setups, _status(), "abc")
    assert any("not from current latest_scan" in item for item in failures)
    assert any("PARTIAL reason omits oi_change_24h_pct" in item for item in failures)
