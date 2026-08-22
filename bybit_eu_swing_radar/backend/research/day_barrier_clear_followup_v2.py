"""Label-blind balanced cohort selector for barrier-clear follow-up V2.

Research governance only. This module never reads outcomes and never mutates live
scores, ranking, eligibility, thresholds, shortability, journal state, or execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

STUDY_ID = "day-barrier-clear-rearm-followup-v2"
PARTITION_SPEC_VERSION = "day-barrier-clear-followup-partition-v2"
DEVELOPMENT_PER_SIDE = 30
VALIDATION_PER_SIDE = 20
MIN_CLEAR_DEVELOPMENT = 15
MIN_NONCLEAR_DEVELOPMENT = 15
TERMINAL_STATUSES = {"cleared", "invalidated_boundary", "invalidated_structure"}
FORBIDDEN_OUTCOME_KEYS = {
    "outcome", "label", "forward_return", "future_return", "mfe", "mae", "pnl",
    "profit", "win", "loss", "tp_hit", "stop_hit", "net_r", "realized_r",
}


def _contains_outcome(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).lower() in FORBIDDEN_OUTCOME_KEYS or _contains_outcome(nested)
            for key, nested in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_outcome(item) for item in value)
    return False


def _parse_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("resolved_at is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("resolved_at must be timezone-aware")
    return parsed


def _canonical_time(value: Any) -> str:
    return _parse_time(value).astimezone(timezone.utc).isoformat()


def _canonical_boundary(boundary: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(boundary, Mapping):
        raise ValueError("fresh V2 start_boundary is required")
    event_id = str(boundary.get("event_id") or "").strip()
    resolved_at = str(boundary.get("resolved_at") or "").strip()
    if not event_id or not resolved_at:
        raise ValueError("start_boundary requires resolved_at and event_id")
    return {"resolved_at": _canonical_time(resolved_at), "event_id": event_id}


def _canonical_event(row: Mapping[str, Any]) -> dict[str, str]:
    if _contains_outcome(row):
        raise ValueError("outcome-bearing field is forbidden before cohort freeze")
    event_id = str(row.get("event_id") or "").strip()
    symbol = str(row.get("symbol") or "").upper().strip()
    side = str(row.get("side") or "").lower().strip()
    terminal_status = str(row.get("terminal_status") or "").lower().strip()
    resolved_at = str(row.get("resolved_at") or "").strip()
    if not event_id or not symbol.endswith("USDC"):
        raise ValueError("event_id and USDC symbol are required")
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    if terminal_status not in TERMINAL_STATUSES:
        raise ValueError("only terminal label-blind observer events may enter V2")
    return {
        "event_id": event_id,
        "symbol": symbol,
        "side": side,
        "terminal_status": terminal_status,
        "resolved_at": _canonical_time(resolved_at),
    }


def _key(row: Mapping[str, str]) -> tuple[datetime, str]:
    return (_parse_time(row["resolved_at"]), row["event_id"])


def _fingerprint(rows: Sequence[Mapping[str, str]]) -> str:
    encoded = json.dumps(list(rows), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _boundary(row: Mapping[str, str] | None) -> dict[str, str] | None:
    if row is None:
        return None
    return {"resolved_at": row["resolved_at"], "event_id": row["event_id"]}


def freeze_balanced_followup(
    events: Sequence[Mapping[str, Any]],
    *,
    start_boundary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Freeze deterministic side-balanced DEVELOPMENT/VALIDATION identities.

    The start boundary is mandatory and external: this function never infers a
    favorable retrospective cutoff. Every event at or before that composite
    boundary is excluded. Within each side, earliest terminal events win.
    """
    start = _canonical_boundary(start_boundary)
    start_key = _key(start)
    canonical = [_canonical_event(row) for row in events]
    ids = [row["event_id"] for row in canonical]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate event_id")

    eligible = [row for row in canonical if _key(row) > start_key]
    longs = sorted((row for row in eligible if row["side"] == "long"), key=_key)
    shorts = sorted((row for row in eligible if row["side"] == "short"), key=_key)

    long_dev = longs[:DEVELOPMENT_PER_SIDE]
    short_dev = shorts[:DEVELOPMENT_PER_SIDE]
    development_ready = (
        len(long_dev) == DEVELOPMENT_PER_SIDE and len(short_dev) == DEVELOPMENT_PER_SIDE
    )
    development = sorted(long_dev + short_dev, key=_key) if development_ready else []

    long_val = longs[DEVELOPMENT_PER_SIDE:DEVELOPMENT_PER_SIDE + VALIDATION_PER_SIDE]
    short_val = shorts[DEVELOPMENT_PER_SIDE:DEVELOPMENT_PER_SIDE + VALIDATION_PER_SIDE]
    validation_ready = development_ready and (
        len(long_val) == VALIDATION_PER_SIDE and len(short_val) == VALIDATION_PER_SIDE
    )
    validation = sorted(long_val + short_val, key=_key) if validation_ready else []

    clear_count = sum(row["terminal_status"] == "cleared" for row in development)
    nonclear_count = len(development) - clear_count
    terminal_balance_ready = development_ready and (
        clear_count >= MIN_CLEAR_DEVELOPMENT and nonclear_count >= MIN_NONCLEAR_DEVELOPMENT
    )

    reasons: list[str] = []
    if not development_ready:
        reasons.append("insufficient_per_side_terminal_events_for_fixed_development_cohort")
    elif not terminal_balance_ready:
        reasons.append("fixed_development_cohort_failed_preregistered_terminal_state_balance")

    return {
        "study": STUDY_ID,
        "partition_spec_version": PARTITION_SPEC_VERSION,
        "research_only": True,
        "label_blind_partition": True,
        "outcome_fields_used": False,
        "start_boundary": start,
        "source_terminal_event_count": len(canonical),
        "eligible_post_boundary_event_count": len(eligible),
        "eligible_long_count": len(longs),
        "eligible_short_count": len(shorts),
        "development_per_side": DEVELOPMENT_PER_SIDE,
        "validation_per_side": VALIDATION_PER_SIDE,
        "development_partition_ready": development_ready,
        "development_analysis_eligible": terminal_balance_ready,
        "development_event_ids": [row["event_id"] for row in development],
        "development_partition_fingerprint": _fingerprint(development) if development_ready else None,
        "development_long_boundary": _boundary(long_dev[-1] if len(long_dev) == DEVELOPMENT_PER_SIDE else None),
        "development_short_boundary": _boundary(short_dev[-1] if len(short_dev) == DEVELOPMENT_PER_SIDE else None),
        "development_balance": {
            "cleared": clear_count,
            "noncleared": nonclear_count,
            "long": len(long_dev) if development_ready else 0,
            "short": len(short_dev) if development_ready else 0,
            "minimum_cleared": MIN_CLEAR_DEVELOPMENT,
            "minimum_noncleared": MIN_NONCLEAR_DEVELOPMENT,
        },
        "validation_partition_ready": validation_ready,
        "validation_event_ids": [row["event_id"] for row in validation],
        "validation_partition_fingerprint": _fingerprint(validation) if validation_ready else None,
        "validation_long_boundary": _boundary(long_val[-1] if len(long_val) == VALIDATION_PER_SIDE else None),
        "validation_short_boundary": _boundary(short_val[-1] if len(short_val) == VALIDATION_PER_SIDE else None),
        "reasons": reasons,
        "outcome_visible": False,
        "validation_outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "execution_authorized": False,
    }
