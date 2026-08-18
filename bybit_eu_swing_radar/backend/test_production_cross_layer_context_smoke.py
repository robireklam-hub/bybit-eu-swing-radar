from scripts.production_cross_layer_context_smoke import validate_capture


def _capture() -> dict:
    return {
        "source_commit_sha": "abc",
        "persisted": True,
        "research_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "composite_score_emitted": False,
        "execution_proof": False,
        "layer_count": 5,
        "layer_fresh_count": 5,
        "layers": {
            "market_regime": {"status": "FRESH"},
            "derivatives_positioning": {"status": "FRESH"},
            "relative_strength": {"status": "FRESH"},
            "event_tokenomics": {"status": "FRESH"},
            "btc_macro_cycle_etf": {"status": "FRESH"},
        },
        "symbol_count": 8,
        "symbols": [
            {"symbol": "BTCUSDC", "coverage": {}},
            {"symbol": "ETHUSDC", "coverage": {}},
            {"symbol": "SOLUSDC", "coverage": {}},
            {"symbol": "XRPUSDC", "coverage": {}},
            {"symbol": "ADAUSDC", "coverage": {}},
            {"symbol": "HYPEUSDC", "coverage": {}},
            {"symbol": "CRVUSDC", "coverage": {}},
            {"symbol": "XLMUSDC", "coverage": {}},
        ],
        "microstructure": {
            "joined": False,
            "policy": "SIGNAL_TIME_PRE_SIGNAL_ONLY_NOT_SNAPSHOT_JOINED",
        },
    }


def test_validate_capture_accepts_research_contract() -> None:
    assert validate_capture(_capture(), "abc") == (True, "ok")


def test_validate_capture_rejects_future_layer() -> None:
    payload = _capture()
    payload["layers"]["market_regime"]["status"] = "FUTURE_REJECTED"
    assert validate_capture(payload, "abc") == (False, "temporal_integrity_failed")


def test_validate_capture_rejects_composite_score() -> None:
    payload = _capture()
    payload["symbols"][0]["score"] = 99
    assert validate_capture(payload, "abc") == (False, "forbidden_composite_output")


def test_validate_capture_rejects_microstructure_snapshot_join() -> None:
    payload = _capture()
    payload["microstructure"]["joined"] = True
    assert validate_capture(payload, "abc") == (False, "microstructure_policy_failed")
