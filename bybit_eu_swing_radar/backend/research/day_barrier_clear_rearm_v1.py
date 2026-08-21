"""Research-only barrier-clear rearm cohort primitives.

This module observes v0.7.5 barrier-blocked day setups without changing live
strategy, scores, eligibility, thresholds, ranking, journal state, or execution.
Outcome labels are deliberately absent from the capture contract.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

STUDY_ID = "day-barrier-clear-rearm-v1"
PARENT_STRATEGY_VERSION = "0.7.5"
MIN_SETUP_SCORE = 70.0
MIN_EXPANSION_SCORE = 55.0
MIN_DIRECTION_SCORE = 35.0
MIN_QUALITY_SCORE = 65.0
MIN_RR_WITHOUT_BARRIER = 1.8


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _field(candidate: Mapping[str, Any], name: str) -> Any:
    metrics = candidate.get("metrics")
    if isinstance(metrics, Mapping) and name in metrics:
        return metrics.get(name)
    return candidate.get(name)


def parent_event_eligibility(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a frozen v0.7.5 barrier-blocked parent event.

    Derivatives are intentionally ignored. Missing OI/funding/liquidations can
    never alter this eligibility result.
    """
    side = str(candidate.get("side") or "").lower()
    symbol = str(candidate.get("symbol") or "").upper()
    strategy_version = str(candidate.get("strategy_version") or "")
    trigger = candidate.get("trigger") or {}
    if not isinstance(trigger, Mapping):
        trigger = {}

    checks = {
        "parent_version_v075": strategy_version == PARENT_STRATEGY_VERSION,
        "usdc_only": symbol.endswith("USDC") and len(symbol) > 4,
        "side_valid": side in {"long", "short"},
        "triggered_parent": trigger.get("triggered") is True and str(trigger.get("route") or "NONE") != "NONE",
        "execution_valid": candidate.get("tradeable") is True or candidate.get("execution_status") == "DAY_TRADE_EXECUTABLE",
        "short_borrowable_if_needed": side != "short" or candidate.get("shortable") is True,
        "setup_score": (_number(candidate.get("setup_score")) or -1.0) >= MIN_SETUP_SCORE,
        "expansion_score": (_number(candidate.get("expansion_score")) or -1.0) >= MIN_EXPANSION_SCORE,
        "direction_score": (_number(candidate.get("side_direction_score")) or -1.0) >= MIN_DIRECTION_SCORE,
        "quality_score": (_number(candidate.get("quality_score")) or -1.0) >= MIN_QUALITY_SCORE,
        "barrier_before_tp2": _field(candidate, "barrier_before_tp2") is True,
        "target_path_blocked": _field(candidate, "target_path_valid") is False,
        "rr_without_barrier": (_number(_field(candidate, "expected_rr_without_barrier")) or -1.0) + 1e-9 >= MIN_RR_WITHOUT_BARRIER,
        "barrier_present": (_number(_field(candidate, "nearest_structural_barrier")) or 0.0) > 0.0,
    }
    eligible = all(checks.values())
    return {
        "study": STUDY_ID,
        "eligible": eligible,
        "checks": checks,
        "research_only": True,
        "execution_authorized": False,
        "derivatives_context_only": True,
        "hard_gate_from_derivatives": False,
    }


def observe_closed_5m_barrier_clear(
    candidate: Mapping[str, Any],
    bars: Sequence[Mapping[str, Any]],
    *,
    original_boundary_held: bool,
    atr_5m: float | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe the first qualifying CLOSED 5m barrier clear.

    `bars` must be ordered strictly after the parent event. Only rows explicitly
    marked closed=True may clear the barrier. No stale parent entry/stop/target
    geometry is copied into the returned record.
    """
    eligibility = parent_event_eligibility(candidate)
    side = str(candidate.get("side") or "").lower()
    barrier = _number(_field(candidate, "nearest_structural_barrier"))
    clear_index: int | None = None
    clear_close: float | None = None
    clear_time: Any = None

    if eligibility["eligible"] and original_boundary_held and barrier is not None:
        for index, bar in enumerate(bars, start=1):
            if bar.get("closed") is not True:
                continue
            close = _number(bar.get("close"))
            if close is None:
                continue
            cleared = close > barrier if side == "long" else close < barrier
            if cleared:
                clear_index = index
                clear_close = close
                clear_time = bar.get("time") or bar.get("timestamp")
                break

    clearance = None
    clearance_atr = None
    if clear_close is not None and barrier is not None:
        clearance = abs(clear_close - barrier)
        atr = _number(atr_5m)
        if atr is not None and atr > 0:
            clearance_atr = clearance / atr

    safe_context = deepcopy(dict(context or {}))
    # Outcome/forward-return fields are forbidden in the preregistered capture.
    for forbidden in (
        "outcome", "label", "forward_return", "mfe", "mae", "pnl", "profit",
        "win", "loss", "tp_hit", "stop_hit",
    ):
        safe_context.pop(forbidden, None)

    return {
        "study": STUDY_ID,
        "parent_strategy_version": PARENT_STRATEGY_VERSION,
        "symbol": str(candidate.get("symbol") or "").upper(),
        "side": side,
        "parent_eligible": eligibility["eligible"],
        "parent_checks": eligibility["checks"],
        "original_boundary_held": bool(original_boundary_held),
        "barrier_price": barrier,
        "barrier_cleared": clear_index is not None,
        "bars_to_clear": clear_index,
        "clear_bar_time": clear_time,
        "clear_close": clear_close,
        "clearance_distance": clearance,
        "clearance_atr_5m": clearance_atr,
        "context": safe_context,
        "fresh_geometry_required": True,
        "fresh_geometry_fields": ["entry", "stop", "targets", "target_path", "net_rr"],
        "inherited_parent_geometry": None,
        "research_only": True,
        "execution_authorized": False,
        "live_strategy_mutation": False,
        "derivatives_context_only": True,
        "outcome_visibility": "LOCKED_UNTIL_PREREGISTERED_DEVELOPMENT_GATE",
    }
