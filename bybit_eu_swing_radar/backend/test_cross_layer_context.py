from datetime import datetime, timezone

from research.cross_layer_context import build_context_snapshot, spec


NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)


def _record(captured_at: str, payload: dict, sha: str = "source") -> dict:
    return {
        "captured_at": captured_at,
        "source_commit_sha": sha,
        "payload": payload,
    }


def _records() -> dict:
    return {
        "market_regime": _record(
            "2026-08-18T07:30:00+00:00",
            {
                "global_regime": "RANGE",
                "dominant_direction": "NEUTRAL",
                "btc_anchor": {"regime": "RANGE", "direction": "NEUTRAL"},
                "symbols": [
                    {"symbol": "BTCUSDC", "regime": "RANGE", "direction": "NEUTRAL", "metrics": {"atr_ratio": 1.0}},
                    {"symbol": "SOLUSDC", "regime": "COMPRESSION", "direction": "NEUTRAL", "metrics": {"atr_ratio": 0.8}},
                ],
            },
        ),
        "derivatives_positioning": _record(
            "2026-08-18T07:40:00+00:00",
            {
                "symbols": {
                    "BTCUSDC": {"symbol": "BTCUSDC", "positioning_state": "MIXED", "funding_crowding": "NEUTRAL", "liquidations": {"state": "UNAVAILABLE"}, "coverage": {"flow": True}},
                    "SOLUSDC": {"symbol": "SOLUSDC", "positioning_state": "LONG_DELEVERAGING", "funding_crowding": "NEUTRAL", "liquidations": {"state": "UNAVAILABLE"}, "coverage": {"flow": True}},
                },
                "positioning_counts": {"MIXED": 1, "LONG_DELEVERAGING": 1},
            },
        ),
        "relative_strength": _record(
            "2026-08-18T07:35:00+00:00",
            {
                "symbols": [
                    {"symbol": "SOLUSDC", "rank": 1, "state": "LEADER", "rotation_context": "ACCELERATING", "rs_score": 90.0, "relative_to_btc_30d_pct": 5.0},
                    {"symbol": "BTCUSDC", "rank": 2, "state": "NEUTRAL", "rotation_context": "STABLE", "rs_score": 50.0, "relative_to_btc_30d_pct": 0.0},
                ],
                "leaders": ["SOLUSDC"],
                "laggards": ["BTCUSDC"],
                "breadth": {"positive_7d_pct": 50.0},
            },
        ),
        "event_tokenomics": _record(
            "2026-08-18T07:45:00+00:00",
            {
                "tracked_symbols": ["BTCUSDC", "SOLUSDC"],
                "event_count": 2,
                "events": [
                    {"event_id": "global", "event_type": "MACRO_FOMC_MINUTES", "title": "Minutes", "symbols": [], "scope": "GLOBAL", "window": "NEXT_24H", "severity": "HIGH"},
                    {"event_id": "sol", "event_type": "PROTOCOL_UPGRADE", "title": "Upgrade", "symbols": ["SOLUSDC"], "scope": "SYMBOL", "window": "NEXT_7D", "severity": "MEDIUM"},
                ],
            },
        ),
        "btc_macro_cycle_etf": _record(
            "2026-08-18T07:20:00+00:00",
            {
                "btc_price": {"close": 65000.0},
                "cycle": {"cycle_quartile": "Q3"},
                "etf": {"flow_5d_usd": -100000000.0},
                "macro": {"us_10y_yield": {"latest": 4.68}},
            },
        ),
    }


def test_spec_forbids_composite_score_and_microstructure_snapshot_join() -> None:
    payload = spec()
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["promotion_allowed"] is False
    assert payload["composite_score_emitted"] is False
    assert payload["microstructure_join_policy"] == "SIGNAL_TIME_PRE_SIGNAL_ONLY_NOT_SNAPSHOT_JOINED"


def test_build_context_joins_fresh_layers_without_scoring() -> None:
    snapshot = build_context_snapshot(_records(), captured_at=NOW, source_commit_sha="main")
    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["layer_fresh_count"] == 5
    assert snapshot["source_commit_sha"] == "main"
    assert snapshot["composite_score_emitted"] is False
    assert snapshot["execution_proof"] is False
    assert snapshot["microstructure"]["joined"] is False
    assert snapshot["symbol_count"] == 2
    assert [row["symbol"] for row in snapshot["symbols"]] == ["SOLUSDC", "BTCUSDC"]
    sol = snapshot["symbols"][0]
    assert sol["market_regime"]["regime"] == "COMPRESSION"
    assert sol["derivatives_positioning"]["positioning_state"] == "LONG_DELEVERAGING"
    assert sol["relative_strength"]["state"] == "LEADER"
    assert sol["events"][0]["event_id"] == "sol"
    assert "score" not in sol


def test_future_layer_is_explicitly_rejected_not_treated_as_neutral() -> None:
    records = _records()
    records["market_regime"] = _record(
        "2026-08-18T08:01:00+00:00",
        {"symbols": [{"symbol": "BTCUSDC", "regime": "TREND", "direction": "BULL"}]},
    )
    snapshot = build_context_snapshot(records, captured_at=NOW)
    assert snapshot["layers"]["market_regime"]["status"] == "FUTURE_REJECTED"
    assert snapshot["data_quality"] == "PARTIAL"


def test_missing_layer_remains_missing() -> None:
    records = _records()
    records["derivatives_positioning"] = None
    snapshot = build_context_snapshot(records, captured_at=NOW)
    assert snapshot["layers"]["derivatives_positioning"]["status"] == "MISSING"
    btc = next(row for row in snapshot["symbols"] if row["symbol"] == "BTCUSDC")
    assert btc["derivatives_positioning"] is None
    assert snapshot["data_quality"] == "PARTIAL"
