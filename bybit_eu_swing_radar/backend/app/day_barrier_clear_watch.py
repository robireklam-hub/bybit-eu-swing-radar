"""Non-executing barrier-clear watch for day-trade HTTP responses.

A strong, already-triggered day setup can be WATCH_ONLY solely because a confirmed
15m structural barrier sits before the strict TP2 path.  The live strategy is
correct to block execution while that barrier remains ahead, but a plain
``NO_TRADE`` response loses an important near-term state: a closed 5m break of the
barrier may create a *new* continuation opportunity that requires a fresh
entry/stop/target/RR calculation.

This module only enriches copied HTTP response payloads.  It never changes cached
candidates, scores, eligibility, target-path gates, journal state or execution.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

WATCH_VERSION = "day-barrier-clear-watch-v1"
MIN_SETUP_SCORE = 70.0
MIN_DIRECTION_SCORE = 35.0
MIN_EXPANSION_SCORE = 55.0
MIN_QUALITY_SCORE = 65.0
MIN_RR_WITHOUT_BARRIER = 1.8


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _passes_if_present(candidate: Mapping[str, Any], field: str, minimum: float) -> bool:
    if field not in candidate or candidate.get(field) is None:
        return True
    value = _number(candidate.get(field))
    return value is not None and value >= minimum


def _candidate_watch(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    side = str(candidate.get("side") or "").lower()
    if side not in {"long", "short"}:
        return None
    if str(candidate.get("category") or "") != "WATCH_ONLY":
        return None
    if str(candidate.get("decision") or "") != "NO_TRADE":
        return None

    trigger = candidate.get("trigger") or {}
    metrics = candidate.get("metrics") or candidate
    if not isinstance(trigger, Mapping) or not isinstance(metrics, Mapping):
        return None
    if trigger.get("triggered") is not True:
        return None
    if str(trigger.get("route") or "NONE") == "NONE":
        return None
    if metrics.get("barrier_before_tp2") is not True:
        return None
    if metrics.get("target_path_valid") is not False:
        return None

    barrier = _number(metrics.get("nearest_structural_barrier"))
    rr_without_barrier = _number(metrics.get("expected_rr_without_barrier"))
    if barrier is None or barrier <= 0:
        return None
    if rr_without_barrier is None or rr_without_barrier + 1e-9 < MIN_RR_WITHOUT_BARRIER:
        return None

    if not _passes_if_present(candidate, "setup_score", MIN_SETUP_SCORE):
        return None
    if not _passes_if_present(candidate, "side_direction_score", MIN_DIRECTION_SCORE):
        return None
    if not _passes_if_present(candidate, "expansion_score", MIN_EXPANSION_SCORE):
        return None
    if not _passes_if_present(candidate, "quality_score", MIN_QUALITY_SCORE):
        return None

    tradeable = candidate.get("tradeable") is True or candidate.get("execution_status") == "DAY_TRADE_EXECUTABLE"
    if not tradeable:
        return None
    if side == "short" and candidate.get("shortable") is not True:
        return None

    comparison = "above" if side == "long" else "below"
    operator = ">" if side == "long" else "<"
    barrier_text = f"{barrier:g}"
    confirmation = f"closed 5m {operator} {barrier_text}"
    return {
        "version": WATCH_VERSION,
        "status": "ARMED_BARRIER_CLEAR",
        "side": side,
        "barrier_price": barrier,
        "confirmation_timeframe": "5m",
        "confirmation_requires_close": True,
        "confirmation_condition": confirmation,
        "barrier_relation": f"price must close {comparison} the confirmed structural barrier",
        "original_trigger_route": trigger.get("route"),
        "original_trigger_price": trigger.get("price"),
        "original_trigger_event_bar_time": trigger.get("event_bar_time"),
        "original_entry_zone": candidate.get("entry_zone"),
        "rr_without_barrier": rr_without_barrier,
        "execution_authorized": False,
        "fresh_recalculation_required": True,
        "recalculate": ["entry", "stop", "targets", "target_path", "net_rr"],
        "live_strategy_mutation": False,
        "note": (
            "This is a conditional continuation watch, not a trade authorization. "
            "After a closed 5m barrier clear, rerun the setup and use only a fresh "
            "entry/stop/target-path/RR result; never execute the stale original entry zone."
        ),
    }


def _append_unique_list(container: dict[str, Any], field: str, text: str) -> None:
    values = container.get(field)
    if isinstance(values, list) and text not in values:
        values.append(text)


def _enrich_candidate(candidate: dict[str, Any]) -> None:
    watch = _candidate_watch(candidate)
    if watch is None:
        return
    candidate["barrier_clear_watch"] = watch
    warning = (
        f"[ARMED_BARRIER_CLEAR] NO_TRADE now; conditional {watch['side']} watch: "
        f"{watch['confirmation_condition']}. If confirmed, rerun entry/stop/targets/target-path/net-R; "
        "the original entry zone is not executable by inheritance."
    )
    _append_unique_list(candidate, "why_now", warning)
    _append_unique_list(candidate, "risks", warning)

    # ``trigger.condition`` is already part of the legacy GPT Action schema, so
    # mirror the conditional watch there as well.  Keep triggered/decision/category
    # untouched: this text cannot authorize execution.
    trigger = candidate.get("trigger")
    if isinstance(trigger, dict):
        current = str(trigger.get("condition") or "").strip()
        suffix = f"CONDITIONAL WATCH ONLY: {watch['confirmation_condition']} then fresh setup recalculation required"
        if suffix not in current:
            trigger["condition"] = f"{current} | {suffix}" if current else suffix


def _walk(value: Any) -> None:
    if isinstance(value, dict):
        if "side" in value and "trigger" in value and ("metrics" in value or "target_path_valid" in value):
            _enrich_candidate(value)
        for nested in list(value.values()):
            _walk(nested)
    elif isinstance(value, list):
        for nested in value:
            _walk(nested)


def enrich_barrier_clear_watch(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copied response with non-executing barrier-clear watch metadata."""
    copied = deepcopy(dict(payload))
    _walk(copied)
    return copied
