from __future__ import annotations

from copy import deepcopy

import pytest

from app import market_context_alerts as alerts


def _external(policy_state: str) -> dict:
    return {
        "geopolitical": {
            "state": "NORMAL",
            "mandatory_warning": False,
            "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
        },
        "macro_liquidity": {
            "state": "AVAILABLE",
            "intraday_causality_supported": False,
        },
        "policy_catalyst": {
            "state": policy_state,
            "context_only": True,
            "hard_gate": False,
            "score_mutation": False,
            "ranking_mutation": False,
            "eligibility_mutation": False,
            "execution_mutation": False,
            "causal_attribution": "UNCONFIRMED_CONTEXT_ONLY",
        },
        "external_context_error": None,
    }


async def _run(monkeypatch: pytest.MonkeyPatch, policy_state: str, volume_ratio: float) -> dict:
    payload = _external(policy_state)

    async def fake_external_context() -> dict:
        return deepcopy(payload)

    monkeypatch.setattr(alerts, "_external_context", fake_external_context)
    return await alerts.get_market_context_alerts({"volume_ratio_5m": volume_ratio})


def _assert_non_mutating(result: dict) -> None:
    assert result["context_only"] is True
    assert result["hard_gate"] is False
    assert result["score_mutation"] is False
    assert result["eligibility_mutation"] is False
    assert result["execution_mutation"] is False
    assert result["causal_attribution"] == "UNCONFIRMED_UNLESS_INDEPENDENTLY_CORROBORATED"
    assert result["reporting_policy"]["must_not_be_suppressed_by_trade_score"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("impulse_ratio", [1.5, 2.5])
async def test_active_policy_and_elevated_or_high_impulse_is_temporal_coincidence_only(
    monkeypatch: pytest.MonkeyPatch,
    impulse_ratio: float,
) -> None:
    result = await _run(monkeypatch, "ACTIVE", impulse_ratio)

    _assert_non_mutating(result)
    assert result["market_impulse"]["state"] in {"ELEVATED", "HIGH"}
    assert result["policy_catalyst"]["state"] == "ACTIVE"
    assert result["mandatory_user_warning"] is True
    assert "időben egybeesik" in result["headline"]
    assert "okság nincs bizonyítva" in result["headline"]


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_state", ["UNAVAILABLE", "STALE"])
@pytest.mark.parametrize("impulse_ratio", [1.5, 2.5])
async def test_missing_policy_context_during_impulse_is_explicitly_uncheckable(
    monkeypatch: pytest.MonkeyPatch,
    policy_state: str,
    impulse_ratio: float,
) -> None:
    result = await _run(monkeypatch, policy_state, impulse_ratio)

    _assert_non_mutating(result)
    assert result["market_impulse"]["state"] in {"ELEVATED", "HIGH"}
    assert result["policy_catalyst"]["state"] == policy_state
    assert result["mandatory_user_warning"] is True
    assert "nem teljes" in result["headline"]
    assert "nem ellenőrizhető" in result["headline"]
    assert result["reporting_policy"]["must_surface_policy_gap_during_elevated_impulse"] is True
    assert (
        result["reporting_policy"]["if_large_move_and_external_context_missing"]
        == "explicitly report attribution as not checkable"
    )


@pytest.mark.asyncio
async def test_active_policy_without_market_impulse_remains_context_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = await _run(monkeypatch, "ACTIVE", 1.0)

    _assert_non_mutating(result)
    assert result["market_impulse"]["state"] == "NORMAL"
    assert result["policy_catalyst"]["state"] == "ACTIVE"
    assert result["mandatory_user_warning"] is True
    assert "nem önálló trade-jel" in result["headline"]
