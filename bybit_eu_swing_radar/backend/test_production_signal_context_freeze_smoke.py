from scripts.production_signal_context_freeze_smoke import (
    validate_capture,
    validate_spec,
    validate_status,
)


def test_validate_spec_accepts_research_contract() -> None:
    payload = {
        "version": "signal-context-freeze-v1",
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
    }
    assert validate_spec(payload) == (True, "ok")


def test_validate_capture_accepts_zero_signal_state() -> None:
    payload = {
        "spec_version": "signal-context-freeze-v1",
        "source_commit_sha": "abc",
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "signals_examined": 0,
        "inserted": 0,
        "recorder_symbols": ["BTCUSDC", "ETHUSDC", "SOLUSDC"],
    }
    assert validate_capture(payload, "abc") == (True, "ok")


def test_validate_capture_rejects_outcome_reading() -> None:
    payload = {
        "spec_version": "signal-context-freeze-v1",
        "source_commit_sha": "abc",
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "signals_examined": 0,
        "inserted": 0,
        "recorder_symbols": [],
    }
    assert validate_capture(payload, "abc") == (False, "capture_read_outcomes")


def test_validate_status_accepts_empty_forward_sample() -> None:
    payload = {
        "spec_version": "signal-context-freeze-v1",
        "source_commit_sha": "abc",
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "journal_signal_count": 0,
        "frozen_signal_count": 0,
        "future_effect_gate": {"ready_for_future_effect_test": False},
        "recorder_symbols": ["BTCUSDC"],
    }
    assert validate_status(payload, "abc") == (True, "ok")


def test_validate_status_rejects_more_freezes_than_signals() -> None:
    payload = {
        "spec_version": "signal-context-freeze-v1",
        "source_commit_sha": "abc",
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "journal_signal_count": 1,
        "frozen_signal_count": 2,
        "future_effect_gate": {"ready_for_future_effect_test": False},
        "recorder_symbols": [],
    }
    assert validate_status(payload, "abc") == (False, "invalid_status_counts")
