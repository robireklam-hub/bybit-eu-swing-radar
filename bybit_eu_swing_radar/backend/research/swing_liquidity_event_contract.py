"""Fail-closed contract helpers for swing-liquidity forward event construction.

Research only. This module deliberately does *not* read future outcomes, mutate
live liquidity gates, or decide promotion. It encodes the preregistered boundary
between label-blind hourly liquidity covariates and a later independent closed-4H
trigger event.

The production study contract requires:
- side long/short only;
- expansion_score >= 55;
- side-aligned abs(direction_score) >= 35;
- valid trigger, entry zone, stop and TP2 geometry;
- short events must be verified shortable at the pre-trigger snapshot;
- the chosen snapshot must be strictly before the trigger close and no more than
  90 minutes old;
- outcome maturity is 10 days after the trigger close.

Repeated hourly snapshots are covariates, not independent outcome events. Event
construction and outcome calculation are separate stages so validation labels can
remain untouched until the preregistered development freeze.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

MIN_EXPANSION_SCORE = 55.0
MIN_DIRECTION_SCORE = 35.0
MAX_PRETRIGGER_SNAPSHOT_AGE = timedelta(minutes=90)
OUTCOME_HORIZON = timedelta(days=10)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp is required")
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def entry_midpoint(candidate: dict[str, Any]) -> float | None:
    zone = candidate.get("entry_zone")
    if not isinstance(zone, dict):
        return None
    low = _number(zone.get("low"))
    high = _number(zone.get("high"))
    if low is None or high is None or low <= 0 or high <= 0 or low > high:
        return None
    return (low + high) / 2.0


def tp2(candidate: dict[str, Any]) -> float | None:
    targets = candidate.get("targets")
    if not isinstance(targets, list) or len(targets) < 2:
        return None
    value = _number(targets[1])
    return value if value is not None and value > 0 else None


def trigger_price(candidate: dict[str, Any]) -> float | None:
    trigger = candidate.get("trigger")
    if not isinstance(trigger, dict):
        return None
    if str(trigger.get("timeframe") or "").upper() != "4H":
        return None
    if trigger.get("requires_close") is not True:
        return None
    value = _number(trigger.get("price"))
    return value if value is not None and value > 0 else None


def eligibility_reasons(candidate: dict[str, Any]) -> list[str]:
    """Return preregistered event-eligibility failures without mutating candidate."""
    reasons: list[str] = []
    side = str(candidate.get("side") or "").lower()
    if side not in {"long", "short"}:
        reasons.append("invalid_side")

    expansion = _number(candidate.get("expansion_score"))
    direction = _number(candidate.get("direction_score"))
    if expansion is None or expansion < MIN_EXPANSION_SCORE:
        reasons.append("expansion_below_55")
    if direction is None or abs(direction) < MIN_DIRECTION_SCORE:
        reasons.append("direction_below_35")
    elif side == "long" and direction <= 0:
        reasons.append("direction_not_long_aligned")
    elif side == "short" and direction >= 0:
        reasons.append("direction_not_short_aligned")

    if trigger_price(candidate) is None:
        reasons.append("invalid_trigger_geometry")
    midpoint = entry_midpoint(candidate)
    stop = _number(candidate.get("stop"))
    second_target = tp2(candidate)
    if midpoint is None or stop is None or stop <= 0 or second_target is None:
        reasons.append("invalid_entry_stop_tp2_geometry")
    elif side == "long" and not (stop < midpoint < second_target):
        reasons.append("invalid_long_risk_geometry")
    elif side == "short" and not (second_target < midpoint < stop):
        reasons.append("invalid_short_risk_geometry")

    if side == "short" and candidate.get("shortable") is not True:
        reasons.append("short_not_verified_borrowable")
    return reasons


def is_event_eligible(candidate: dict[str, Any]) -> bool:
    return not eligibility_reasons(candidate)


def pretrigger_snapshot_age_seconds(
    captured_at: datetime | str,
    trigger_close_at: datetime | str,
) -> float | None:
    """Return age only for a strictly pre-trigger snapshot within the 90m window."""
    captured = _timestamp(captured_at)
    trigger_close = _timestamp(trigger_close_at)
    age = trigger_close - captured
    if age <= timedelta(0) or age > MAX_PRETRIGGER_SNAPSHOT_AGE:
        return None
    return age.total_seconds()


def close_satisfies_frozen_trigger(candidate: dict[str, Any], close: Any) -> bool:
    """Evaluate only the frozen pre-trigger 4H close condition.

    This helper does not establish that a bar is the *first* qualifying bar; the
    event builder must enforce chronological first-trigger deduplication.
    """
    side = str(candidate.get("side") or "").lower()
    threshold = trigger_price(candidate)
    closing = _number(close)
    if threshold is None or closing is None or not is_event_eligible(candidate):
        return False
    if side == "long":
        return closing > threshold
    if side == "short":
        return closing < threshold
    return False


def maturity_at(trigger_close_at: datetime | str) -> datetime:
    return _timestamp(trigger_close_at) + OUTCOME_HORIZON


def is_matured(trigger_close_at: datetime | str, now: datetime | str) -> bool:
    return _timestamp(now) >= maturity_at(trigger_close_at)


def safe_event_metadata(
    candidate: dict[str, Any],
    *,
    captured_at: datetime | str,
    trigger_bar_start_at: datetime | str,
    trigger_close_at: datetime | str,
) -> dict[str, Any]:
    """Build label-free event metadata after all fail-closed contract checks.

    No high/low path after trigger, exit, R, MFE/MAE, or future return is accepted
    or emitted here. Outcome calculation belongs to a later gated stage.
    """
    reasons = eligibility_reasons(candidate)
    age_seconds = pretrigger_snapshot_age_seconds(captured_at, trigger_close_at)
    if age_seconds is None:
        reasons.append("snapshot_not_strictly_pretrigger_within_90m")
    if reasons:
        raise ValueError("ineligible swing liquidity event: " + ",".join(reasons))

    side = str(candidate["side"]).lower()
    symbol = str(candidate.get("symbol") or "").upper()
    trigger_start = _timestamp(trigger_bar_start_at)
    trigger_close = _timestamp(trigger_close_at)
    if trigger_close - trigger_start != timedelta(hours=4):
        raise ValueError("trigger bar must be exactly 4 hours")

    return {
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "promotion_allowed": False,
        "symbol": symbol,
        "side": side,
        "pretrigger_captured_at": _timestamp(captured_at).isoformat(),
        "pretrigger_snapshot_age_seconds": age_seconds,
        "trigger_bar_start_at": trigger_start.isoformat(),
        "trigger_close_at": trigger_close.isoformat(),
        "matures_at": maturity_at(trigger_close).isoformat(),
        "trigger_price": trigger_price(candidate),
        "entry_midpoint": entry_midpoint(candidate),
        "stop": _number(candidate.get("stop")),
        "tp2": tp2(candidate),
    }
