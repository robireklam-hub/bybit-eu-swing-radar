"""Frozen sample partition for the day barrier-clear rearm study.

Pure research governance only. This module never reads outcomes and never mutates
live scores, ranking, eligibility, thresholds, journal state, or execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Any, Mapping, Sequence

STUDY_ID = "day-barrier-clear-rearm-v1"
PARTITION_SPEC_VERSION = "day-barrier-clear-partition-v1"
DEVELOPMENT_TARGET = 60
VALIDATION_TARGET = 40
MIN_CLEAR_DEVELOPMENT = 15
MIN_NONCLEAR_DEVELOPMENT = 15
MIN_LONG_DEVELOPMENT = 10
MIN_SHORT_DEVELOPMENT = 10
TERMINAL_STATUSES = {"cleared", "invalidated_boundary", "invalidated_structure"}
BOUNDARY_ORDER = ["resolved_at", "event_id"]
FORBIDDEN_OUTCOME_KEYS = {
    "outcome", "label", "forward_return", "future_return", "mfe", "mae", "pnl",
    "profit", "win", "loss", "tp_hit", "stop_hit", "net_r", "realized_r",
}


def _contains_outcome(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_OUTCOME_KEYS or _contains_outcome(nested):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
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
    """Return one stable UTC representation for cohort identity/fingerprints."""
    return _parse_time(value).astimezone(timezone.utc).isoformat()


def _canonical_event(row: Mapping[str, Any]) -> dict[str, str]:
    if _contains_outcome(row):
        raise ValueError("outcome-bearing field is forbidden before partition freeze")
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
        raise ValueError("only terminal label-blind observer events may enter the partition")
    return {
        "event_id": event_id,
        "symbol": symbol,
        "side": side,
        "terminal_status": terminal_status,
        "resolved_at": _canonical_time(resolved_at),
    }


def _fingerprint(rows: Sequence[Mapping[str, str]]) -> str:
    encoded = json.dumps(list(rows), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _boundary(row: Mapping[str, str] | None) -> dict[str, str] | None:
    if row is None:
        return None
    return {"resolved_at": row["resolved_at"], "event_id": row["event_id"]}


def freeze_partition(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Freeze first-60 DEVELOPMENT and next-40 untouched VALIDATION identities.

    Ordering is deterministic by (resolved_at, event_id). The DEVELOPMENT cohort
    never expands after 60, preventing optional stopping based on later results.
    The exact composite boundary is published so later validation selection cannot
    silently degrade to timestamp-only ordering when terminal events share a time.
    Equivalent timezone representations are normalized to UTC before fingerprinting,
    so semantically identical event times cannot create cohort identity drift.
    """
    canonical = [_canonical_event(row) for row in events]
    ids = [row["event_id"] for row in canonical]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate event_id")
    canonical.sort(key=lambda row: (_parse_time(row["resolved_at"]), row["event_id"]))

    development = canonical[:DEVELOPMENT_TARGET] if len(canonical) >= DEVELOPMENT_TARGET else []
    validation = (
        canonical[DEVELOPMENT_TARGET:DEVELOPMENT_TARGET + VALIDATION_TARGET]
        if len(canonical) >= DEVELOPMENT_TARGET + VALIDATION_TARGET
        else []
    )

    clear_count = sum(row["terminal_status"] == "cleared" for row in development)
    nonclear_count = len(development) - clear_count
    long_count = sum(row["side"] == "long" for row in development)
    short_count = len(development) - long_count
    development_ready = len(development) == DEVELOPMENT_TARGET
    balance_ready = development_ready and all((
        clear_count >= MIN_CLEAR_DEVELOPMENT,
        nonclear_count >= MIN_NONCLEAR_DEVELOPMENT,
        long_count >= MIN_LONG_DEVELOPMENT,
        short_count >= MIN_SHORT_DEVELOPMENT,
    ))

    reasons: list[str] = []
    if not development_ready:
        reasons.append("insufficient_terminal_events_for_fixed_development_cohort")
    elif not balance_ready:
        reasons.append("fixed_development_cohort_failed_preregistered_group_balance")

    return {
        "study": STUDY_ID,
        "partition_spec_version": PARTITION_SPEC_VERSION,
        "research_only": True,
        "label_blind_partition": True,
        "outcome_fields_used": False,
        "development_target": DEVELOPMENT_TARGET,
        "validation_target": VALIDATION_TARGET,
        "terminal_event_count": len(canonical),
        "partition_boundary_order": list(BOUNDARY_ORDER),
        "development_partition_ready": development_ready,
        "development_analysis_eligible": balance_ready,
        "development_event_ids": [row["event_id"] for row in development],
        "development_partition_fingerprint": _fingerprint(development) if development_ready else None,
        "development_boundary": _boundary(development[-1] if development_ready else None),
        "development_balance": {
            "cleared": clear_count,
            "noncleared": nonclear_count,
            "long": long_count,
            "short": short_count,
            "minimum_cleared": MIN_CLEAR_DEVELOPMENT,
            "minimum_noncleared": MIN_NONCLEAR_DEVELOPMENT,
            "minimum_long": MIN_LONG_DEVELOPMENT,
            "minimum_short": MIN_SHORT_DEVELOPMENT,
        },
        "validation_partition_ready": len(validation) == VALIDATION_TARGET,
        "validation_event_ids": [row["event_id"] for row in validation],
        "validation_partition_fingerprint": _fingerprint(validation) if len(validation) == VALIDATION_TARGET else None,
        "validation_boundary": _boundary(validation[-1] if len(validation) == VALIDATION_TARGET else None),
        "reasons": reasons,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "live_strategy_mutation": False,
        "execution_authorized": False,
    }
