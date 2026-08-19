from scripts.production_microstructure_alignment_status import validate_alignment_status


def _payload(**overrides):
    payload = {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "post_signal_data_used": False,
        "promotion_allowed": False,
        "spec": {
            "spec_version": "microstructure-forward-alignment-v1",
            "preregistered_strategy_version": "0.7.3",
            "strategy_version_isolated": True,
        },
        "alignment_coverage_ready": True,
        "alignment_coverage": {
            "status": "ALIGNED",
            "reason": "all_forward_signals_aligned",
            "journal_signal_count": 12,
            "aligned_signal_count": 12,
            "unaligned_signal_count": 0,
            "per_symbol": {
                "BTCUSDC": {"journal_signals": 6, "aligned_signals": 6, "unaligned_signals": 0},
                "ETHUSDC": {"journal_signals": 4, "aligned_signals": 4, "unaligned_signals": 0},
                "SOLUSDC": {"journal_signals": 2, "aligned_signals": 2, "unaligned_signals": 0},
            },
        },
        "sample": {
            "ready_for_preregistered_effect_test": False,
            "reasons": ["insufficient_total_signals"],
            "total_signals": 12,
            "per_symbol": {"BTCUSDC": 6, "ETHUSDC": 4, "SOLUSDC": 2},
            "minimum_total": 60,
            "minimum_per_symbol": 10,
        },
        "ready_for_preregistered_effect_test": False,
    }
    payload.update(overrides)
    return payload


def test_alignment_status_contract_allows_not_yet_sample_ready():
    assert validate_alignment_status(_payload()) == (True, "ok")


def test_alignment_status_contract_rejects_label_leakage_or_gate_mutation():
    assert validate_alignment_status(_payload(label_blind=False))[1] == "label_blind_not_true"
    assert validate_alignment_status(_payload(post_signal_data_used=True))[1] == "post_signal_data_used_not_false"
    mutated = _payload()
    mutated["sample"] = dict(mutated["sample"], minimum_total=50)
    assert validate_alignment_status(mutated)[1] == "sample_gate_mutated"


def test_alignment_status_contract_rejects_strategy_version_contamination():
    wrong_version = _payload()
    wrong_version["spec"] = dict(
        wrong_version["spec"],
        preregistered_strategy_version="0.7.4",
    )
    assert validate_alignment_status(wrong_version)[1] == "unexpected_preregistered_strategy_version"

    not_isolated = _payload()
    not_isolated["spec"] = dict(not_isolated["spec"], strategy_version_isolated=False)
    assert validate_alignment_status(not_isolated)[1] == "strategy_version_isolation_not_true"


def test_alignment_status_contract_accepts_waiting_for_forward_signals():
    payload = _payload(
        alignment_coverage_ready=False,
        alignment_coverage={
            "status": "WAITING_FOR_FORWARD_SIGNALS",
            "reason": "waiting_for_forward_signals",
            "journal_signal_count": 0,
            "aligned_signal_count": 0,
            "unaligned_signal_count": 0,
            "per_symbol": {
                symbol: {"journal_signals": 0, "aligned_signals": 0, "unaligned_signals": 0}
                for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC")
            },
        },
    )
    assert validate_alignment_status(payload) == (True, "ok")


def test_alignment_status_contract_accepts_coverage_failure_only_when_effect_gate_is_closed():
    coverage = {
        "status": "COVERAGE_FAILURE",
        "reason": "alignment_coverage_failure",
        "journal_signal_count": 13,
        "aligned_signal_count": 12,
        "unaligned_signal_count": 1,
        "per_symbol": {
            "BTCUSDC": {"journal_signals": 7, "aligned_signals": 6, "unaligned_signals": 1},
            "ETHUSDC": {"journal_signals": 4, "aligned_signals": 4, "unaligned_signals": 0},
            "SOLUSDC": {"journal_signals": 2, "aligned_signals": 2, "unaligned_signals": 0},
        },
    }
    assert validate_alignment_status(
        _payload(alignment_coverage_ready=False, alignment_coverage=coverage)
    ) == (True, "ok")
    assert validate_alignment_status(
        _payload(
            alignment_coverage_ready=False,
            alignment_coverage=coverage,
            ready_for_preregistered_effect_test=True,
        )
    )[1] == "coverage_failure_did_not_close_effect_gate"


def test_alignment_status_contract_rejects_inconsistent_coverage_counts():
    payload = _payload()
    payload["alignment_coverage"] = dict(
        payload["alignment_coverage"],
        journal_signal_count=13,
    )
    assert validate_alignment_status(payload)[1] == "alignment_coverage_counts_inconsistent"
