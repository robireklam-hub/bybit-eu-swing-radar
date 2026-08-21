"""Outcome-bearing missed-move audit for blocked day-trade setups.

RESEARCH/OFFLINE ONLY. This module is deliberately not imported by the live worker.
It quantifies false-negative opportunity cost after the fact without changing any
live gate. The blocker classification is taken from the candidate snapshot frozen
at decision time; future bars are used only by this offline evaluator.
"""
from __future__ import annotations

from typing import Any, Iterable

SPEC_VERSION = "day-missed-move-audit-v1"
STRATEGY_VERSION = "0.7.6"
FAVORABLE_PCT_THRESHOLDS = (1.0, 2.0, 3.0)


def _value(row: Any, field: str) -> float:
    if isinstance(row, dict):
        value = row.get(field)
    else:
        value = getattr(row, field, None)
    return float(value or 0.0)


def _blocker(candidate: dict[str, Any]) -> str:
    state = str(candidate.get("entry_state") or "UNKNOWN")
    bucket = str(candidate.get("watch_bucket") or "")
    if state != "UNKNOWN":
        return state
    return bucket or "UNKNOWN"


def audit_blocked_candidate(
    candidate: dict[str, Any],
    future_bars: Iterable[Any],
) -> dict[str, Any]:
    if candidate.get("decision") == "TRADE":
        raise ValueError("missed-move audit accepts only non-TRADE snapshots")
    side = str(candidate.get("side") or "")
    if side not in {"long", "short"}:
        raise ValueError("candidate side must be long or short")
    entry = float(candidate.get("reference_entry") or candidate.get("last_price") or 0.0)
    stop = float(candidate.get("stop") or 0.0)
    if entry <= 0 or stop <= 0:
        raise ValueError("candidate requires positive reference_entry/stop")
    risk = abs(entry - stop)
    if risk <= 0:
        raise ValueError("candidate risk must be positive")

    rows = list(future_bars)
    if side == "long":
        best = max((_value(row, "high") for row in rows), default=entry)
        worst = min((_value(row, "low") for row in rows), default=entry)
        favorable = max(0.0, best - entry)
        adverse = max(0.0, entry - worst)
    else:
        best = min((_value(row, "low") for row in rows), default=entry)
        worst = max((_value(row, "high") for row in rows), default=entry)
        favorable = max(0.0, entry - best)
        adverse = max(0.0, worst - entry)

    favorable_pct = favorable / entry * 100.0
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "symbol": candidate.get("symbol"),
        "side": side,
        "setup_state": candidate.get("setup_state"),
        "entry_state_at_decision": candidate.get("entry_state"),
        "blocker": _blocker(candidate),
        "reference_entry": entry,
        "risk_per_unit": risk,
        "future_bars_observed": len(rows),
        "mfe_r": favorable / risk,
        "mae_r": adverse / risk,
        "favorable_move_pct": favorable_pct,
        "reached_favorable_pct": {
            f"{threshold:.0f}%": favorable_pct >= threshold
            for threshold in FAVORABLE_PCT_THRESHOLDS
        },
    }


def audit_spec() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "research_only": True,
        "live_import_allowed": False,
        "outcome_bearing": True,
        "blocker_frozen_at_decision_time": True,
        "favorable_pct_thresholds": list(FAVORABLE_PCT_THRESHOLDS),
        "purpose": "QUANTIFY_FALSE_NEGATIVE_COST_BY_BLOCKER_NOT_RETUNE_LIVE_GATES",
    }
