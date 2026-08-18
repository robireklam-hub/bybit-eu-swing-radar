from scripts.production_relative_strength_shadow_smoke import validate_capture


def _row(rank: int, symbol: str) -> dict:
    return {
        "rank": rank,
        "symbol": symbol,
        "state": "NEUTRAL",
        "rotation_context": "STABLE",
        "rs_score": 50.0,
        "return_7d_pct": 1.0,
        "return_30d_pct": 2.0,
        "return_90d_pct": 3.0,
        "percentile_7d": 50.0,
        "percentile_30d": 50.0,
        "percentile_90d": 50.0,
        "relative_to_btc_7d_pct": 0.0,
        "relative_to_btc_30d_pct": 0.0,
        "relative_to_btc_90d_pct": 0.0,
        "relative_to_universe_7d_pct": 0.0,
        "relative_to_universe_30d_pct": 0.0,
        "relative_to_universe_90d_pct": 0.0,
    }


def _capture() -> dict:
    symbols = [_row(1, "BTCUSDC")]
    symbols.extend(_row(index, f"C{index}USDC") for index in range(2, 13))
    analyzed = [item["symbol"] for item in symbols]
    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "persisted": True,
        "source_commit_sha": "abc",
        "spec": {"version": "relative-strength-shadow-v1"},
        "sector_rotation_available": False,
        "sector_metadata_status": "NOT_INCLUDED_UNSOURCED",
        "universe_size": 12,
        "symbols": symbols,
        "requested_symbols": analyzed,
        "analyzed_symbols": analyzed,
        "coverage_pct": 100.0,
    }


def test_validate_capture_accepts_research_contract() -> None:
    assert validate_capture(_capture(), "abc") == (True, "ok")


def test_validate_capture_rejects_unsourced_sector_rotation() -> None:
    payload = _capture()
    payload["sector_rotation_available"] = True
    assert validate_capture(payload, "abc") == (
        False,
        "unsourced_sector_rotation_enabled",
    )


def test_validate_capture_rejects_missing_relative_fields() -> None:
    payload = _capture()
    del payload["symbols"][0]["relative_to_btc_90d_pct"]
    assert validate_capture(payload, "abc") == (
        False,
        "missing_relative_strength_field",
    )
