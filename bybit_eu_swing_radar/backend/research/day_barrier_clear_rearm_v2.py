"""Independent, outcome-blind barrier-clear v2 cohort contract.

Research only. This module does not activate v2, score candidates, change live
eligibility, authorize execution, or expose outcomes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

TRIAL_ID = "day-barrier-clear-rearm-v2"
DEVELOPMENT_PER_SIDE = 30
VALIDATION_PER_SIDE = 20
DEVELOPMENT_TOTAL = 60
VALIDATION_TOTAL = 40
SIDE_ORDER = ("long", "short")
OUTCOME_FIELDS = {
    "forward_return",
    "future_return",
    "mfe",
    "mae",
    "pnl",
    "net_r",
    "realized_r",
    "win",
    "loss",
    "target_hit",
    "stop_hit",
    "outcome",
}


def _utc(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field} is required")
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _canonical_event(event: dict[str, Any], *, activation_boundary: datetime) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("event must be a mapping")
    leaked = sorted(OUTCOME_FIELDS.intersection(event))
    if leaked:
        raise ValueError(f"outcome-bearing fields are prohibited: {','.join(leaked)}")
    event_id = event.get("event_id")
    if event_id in (None, ""):
        raise ValueError("event_id is required")
    side = str(event.get("side") or "").lower()
    if side not in SIDE_ORDER:
        raise ValueError("side must be long or short")
    if event.get("terminal") is not True:
        raise ValueError("only terminal events may enter the v2 partition")
    captured_at = _utc(event.get("captured_at"), "captured_at")
    resolved_at = _utc(event.get("resolved_at"), "resolved_at")
    if captured_at <= activation_boundary:
        raise ValueError("v2 parents must be captured strictly after the frozen activation boundary")
    if resolved_at <= activation_boundary:
        raise ValueError("v2 events must resolve strictly after the frozen activation boundary")
    if resolved_at < captured_at:
        raise ValueError("resolved_at must not precede captured_at")
    return {
        "event_id": str(event_id),
        "side": side,
        "resolved_at": resolved_at.isoformat(),
    }


def _fingerprint(events: list[dict[str, Any]]) -> str:
    payload = json.dumps(events, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_side_stratified_partition(
    events: list[dict[str, Any]],
    *,
    activation_boundary: datetime | str,
) -> dict[str, Any]:
    """Build frozen v2 DEVELOPMENT/VALIDATION identities without outcomes.

    The activation boundary is intentionally supplied by a later activation step;
    preregistration alone never starts the cohort. Every event must also carry the
    originating parent capture timestamp so the no-v1-reuse rule is enforced by
    this central cohort contract rather than only by individual callers.
    """
    boundary = _utc(activation_boundary, "activation_boundary")
    canonical = [_canonical_event(event, activation_boundary=boundary) for event in events]
    ids = [row["event_id"] for row in canonical]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate event_id in v2 partition input")

    by_side = {
        side: sorted(
            (row for row in canonical if row["side"] == side),
            key=lambda row: (_utc(row["resolved_at"], "resolved_at"), row["event_id"]),
        )
        for side in SIDE_ORDER
    }

    development_ready = all(len(by_side[side]) >= DEVELOPMENT_PER_SIDE for side in SIDE_ORDER)
    development = []
    if development_ready:
        for side in SIDE_ORDER:
            development.extend(by_side[side][:DEVELOPMENT_PER_SIDE])
        development.sort(key=lambda row: (_utc(row["resolved_at"], "resolved_at"), row["event_id"]))

    validation_ready = development_ready and all(
        len(by_side[side]) >= DEVELOPMENT_PER_SIDE + VALIDATION_PER_SIDE for side in SIDE_ORDER
    )
    validation = []
    if validation_ready:
        for side in SIDE_ORDER:
            start = DEVELOPMENT_PER_SIDE
            stop = DEVELOPMENT_PER_SIDE + VALIDATION_PER_SIDE
            validation.extend(by_side[side][start:stop])
        validation.sort(key=lambda row: (_utc(row["resolved_at"], "resolved_at"), row["event_id"]))

    return {
        "trial_id": TRIAL_ID,
        "activated": True,
        "activation_boundary": boundary.isoformat(),
        "research_only": True,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "execution_authorized": False,
        "live_strategy_mutated": False,
        "development_target": DEVELOPMENT_TOTAL,
        "development_per_side": DEVELOPMENT_PER_SIDE,
        "development_ready": development_ready,
        "development_event_count": len(development),
        "development_long_count": sum(row["side"] == "long" for row in development),
        "development_short_count": sum(row["side"] == "short" for row in development),
        "development_fingerprint": _fingerprint(development) if development_ready else None,
        "validation_target": VALIDATION_TOTAL,
        "validation_per_side": VALIDATION_PER_SIDE,
        "validation_ready": validation_ready,
        "validation_event_count": len(validation),
        "validation_long_count": sum(row["side"] == "long" for row in validation),
        "validation_short_count": sum(row["side"] == "short" for row in validation),
        "validation_fingerprint": _fingerprint(validation) if validation_ready else None,
        "development_events": development,
        "validation_events": validation,
    }


def preregistration_status() -> dict[str, Any]:
    return {
        "trial_id": TRIAL_ID,
        "status": "PREREGISTERED_NOT_ACTIVATED",
        "activation_required": True,
        "activation_rule": "EXPLICIT_POST_MERGE_UTC_BOUNDARY_STRICTLY_BEFORE_ALL_V2_EVENTS",
        "historical_backfill_allowed": False,
        "v1_event_reuse_allowed": False,
        "development_target": DEVELOPMENT_TOTAL,
        "development_per_side": DEVELOPMENT_PER_SIDE,
        "validation_target": VALIDATION_TOTAL,
        "validation_per_side": VALIDATION_PER_SIDE,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "execution_authorized": False,
        "live_strategy_mutated": False,
    }
