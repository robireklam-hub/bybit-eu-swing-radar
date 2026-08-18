from __future__ import annotations

from scripts.production_derivatives_positioning_shadow_smoke import validate_capture


def test_validate_capture_accepts_partial_liquidation_coverage() -> None:
    payload = {
        "research_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec_version": "derivatives-positioning-shadow-v1",
        "source_commit_sha": "abc",
        "persisted": True,
        "coverage": {"flow": 5, "market_regime": 8, "liquidations": 0, "total": 8},
        "symbols": {
            f"C{i}USDC": {
                "positioning_state": "MIXED",
                "derivatives_context_only": True,
                "execution_proof": False,
            }
            for i in range(8)
        },
    }
    assert validate_capture(payload, "abc") == (True, "ok")


def test_validate_capture_rejects_execution_contract_mutation() -> None:
    payload = {
        "research_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec_version": "derivatives-positioning-shadow-v1",
        "source_commit_sha": "abc",
        "persisted": True,
        "coverage": {"flow": 5, "market_regime": 8, "liquidations": 1, "total": 8},
        "symbols": {
            f"C{i}USDC": {
                "positioning_state": "MIXED",
                "derivatives_context_only": True,
                "execution_proof": i == 0,
            }
            for i in range(8)
        },
    }
    assert validate_capture(payload, "abc")[0] is False
