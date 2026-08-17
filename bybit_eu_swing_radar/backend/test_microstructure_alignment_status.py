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
        {"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20},
    )
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["label_blind"] is True
    assert payload["post_signal_data_used"] is False
    assert payload["promotion_allowed"] is False
    assert payload["spec"]["spec_version"] == "microstructure-forward-alignment-v1"
    assert payload["alignment_coverage_ready"] is True
    assert payload["alignment_coverage"]["status"] == "ALIGNED"
    assert payload["alignment_coverage"]["journal_signal_count"] == 60
    assert payload["alignment_coverage"]["aligned_signal_count"] == 60
    assert payload["alignment_coverage"]["unaligned_signal_count"] == 0
    assert payload["sample"]["total_signals"] == 60
    assert payload["sample"]["per_symbol"] == {
        "BTCUSDC": 20,
        "ETHUSDC": 20,
        "SOLUSDC": 20,
    }
    assert payload["ready_for_preregistered_effect_test"] is True


def test_alignment_status_stays_closed_when_one_symbol_is_under_sample_gate():
    counts = {"BTCUSDC": 30, "ETHUSDC": 25, "SOLUSDC": 5}
    payload = build_alignment_status(
        {"ready_for_forward_feature_analysis": True},
        _features(counts),
        SYMBOLS,
        counts,
    )
    assert payload["sample"]["total_signals"] == 60
    assert payload["sample"]["ready_for_preregistered_effect_test"] is False
    assert "insufficient_per_symbol_signals" in payload["sample"]["reasons"]
    assert payload["alignment_coverage_ready"] is True
    assert payload["ready_for_preregistered_effect_test"] is False


def test_alignment_status_stays_closed_before_data_quality_gate():
    counts = {"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}
    payload = build_alignment_status(
        {"ready_for_forward_feature_analysis": False},
        _features(counts),
        SYMBOLS,
        counts,
    )
    assert payload["sample"]["ready_for_preregistered_effect_test"] is True
    assert payload["alignment_coverage_ready"] is True
    assert payload["data_quality_ready"] is False
    assert payload["ready_for_preregistered_effect_test"] is False


def test_alignment_status_explains_waiting_for_forward_signals():
    payload = build_alignment_status(
        {"ready_for_forward_feature_analysis": True},
        [],
        SYMBOLS,
        {"BTCUSDC": 0, "ETHUSDC": 0, "SOLUSDC": 0},
    )
    coverage = payload["alignment_coverage"]
    assert coverage["status"] == "WAITING_FOR_FORWARD_SIGNALS"
    assert coverage["reason"] == "waiting_for_forward_signals"
    assert coverage["journal_signal_count"] == 0
    assert coverage["aligned_signal_count"] == 0
    assert coverage["unaligned_signal_count"] == 0
    assert payload["alignment_coverage_ready"] is False
    assert payload["ready_for_preregistered_effect_test"] is False


def test_alignment_coverage_failure_blocks_effect_test_even_when_aligned_sample_is_large_enough():
    payload = build_alignment_status(
        {"ready_for_forward_feature_analysis": True},
        _features({"BTCUSDC": 20, "ETHUSDC": 20, "SOLUSDC": 20}),
        SYMBOLS,
        {"BTCUSDC": 21, "ETHUSDC": 20, "SOLUSDC": 20},
    )
    coverage = payload["alignment_coverage"]
    assert payload["sample"]["ready_for_preregistered_effect_test"] is True
    assert coverage["status"] == "COVERAGE_FAILURE"
    assert coverage["reason"] == "alignment_coverage_failure"
    assert coverage["journal_signal_count"] == 61
    assert coverage["aligned_signal_count"] == 60
    assert coverage["unaligned_signal_count"] == 1
    assert coverage["per_symbol"]["BTCUSDC"] == {
        "journal_signals": 21,
        "aligned_signals": 20,
        "unaligned_signals": 1,
    }
    assert payload["alignment_coverage_ready"] is False
    assert payload["ready_for_preregistered_effect_test"] is False
