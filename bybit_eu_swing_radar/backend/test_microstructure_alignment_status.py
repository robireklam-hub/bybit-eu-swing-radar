from app.microstructure_research import build_alignment_status


SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC"]


def _features(counts):
    rows = []
    signal_id = 1
    for symbol, count in counts.items():
        for _ in range(count):
            rows.append({"signal_id": signal_id, "symbol": symbol})
            signal_id += 1
    return rows


def test_alignment_status_requires_data_quality_and_preregistered_sample():
    payload = build_alignment_status(
        {"ready_for_forward_feature_analysis": True},
        _features({"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}),
        SYMBOLS,
    )
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["label_blind"] is True
    assert payload["post_signal_data_used"] is False
    assert payload["promotion_allowed"] is False
    assert payload["spec"]["spec_version"] == "microstructure-forward-alignment-v1"
    assert payload["sample"]["total_signals"] == 60
    assert payload["sample"]["per_symbol"] == {
        "BTCUSDC": 20,
        "ETHUSDC": 20,
        "SOLUSDC": 20,
    }
    assert payload["ready_for_preregistered_effect_test"] is True


def test_alignment_status_stays_closed_when_one_symbol_is_under_sample_gate():
    payload = build_alignment_status(
        {"ready_for_forward_feature_analysis": True},
        _features({"BTCUSDC": 30, "ETHUSDC": 25, "SOLUSDC": 5}),
        SYMBOLS,
    )
    assert payload["sample"]["total_signals"] == 60
    assert payload["sample"]["ready_for_preregistered_effect_test"] is False
    assert "insufficient_per_symbol_signals" in payload["sample"]["reasons"]
    assert payload["ready_for_preregistered_effect_test"] is False


def test_alignment_status_stays_closed_before_data_quality_gate():
    payload = build_alignment_status(
        {"ready_for_forward_feature_analysis": False},
        _features({"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}),
        SYMBOLS,
    )
    assert payload["sample"]["ready_for_preregistered_effect_test"] is True
    assert payload["data_quality_ready"] is False
    assert payload["ready_for_preregistered_effect_test"] is False
