from scripts.production_microstructure_alignment_status import validate_alignment_status


def _payload(**overrides):
    payload = {
        "research_only": True,
        "live_strategy_mutated": False,
        "label_blind": True,
        "post_signal_data_used": False,
        "promotion_allowed": False,
        "spec": {"spec_version": "microstructure-forward-alignment-v1"},
        "sample": {
            "ready_for_preregistered_effect_test": False,
            "reasons": ["insufficient_total_signals"],
            "total_signals": 12,
            "per_symbol": {"BTCUSDC": 6, "ETHUSDC": 4, "SOLUSDC": 2},
            "minimum_total": 60,
            "minimum_per_symbol": 10,
        },
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
