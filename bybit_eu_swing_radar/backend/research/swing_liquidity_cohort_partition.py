"""Deterministic label-blind cohort partition for swing-liquidity validation v1.

Research only. This module freezes the identity boundary between the first 60
matured DEVELOPMENT events and all later VALIDATION events without reading any
trade outcome. It does not mutate live strategy, liquidity gates, eligibility,
scoring, ranking, or execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from research.research_lifecycle_ledger import canonical_fingerprint
from research.swing_liquidity_event_contract import maturity_at

DEVELOPMENT_TARGET_MATURED_EVENTS = 60
VALIDATION_TARGET_MATURED_EVENTS = 40
PARTITION_SPEC_VERSION = "swing-liquidity-cohort-partition-v1"

_FORBIDDEN_OUTCOME_KEYS = {
    "outcome",
    "outcomes",
    "result",
    "exit",
    "exit_price",
    "gross_r",
    "net_r",
    "mfe",
    "mfe_r",
    "mae",
    "mae_r",
    "pnl",
    "profit",
    "future_return",
}


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise ValueError(f"{field}_missing")
    if parsed.tzinfo is None:
        raise ValueError(f"{field}_timezone_missing")
    return parsed.astimezone(timezone.utc)


def _safe_event_identity(event: dict[str, Any], index: int) -> dict[str, str]:
    forbidden = sorted(key for key in _FORBIDDEN_OUTCOME_KEYS if key in event)
    if forbidden:
        raise ValueError(f"event_{index}_contains_outcome_fields:{','.join(forbidden)}")
    if event.get("label_blind") is not True:
        raise ValueError(f"event_{index}_label_blind_not_true")
    if event.get("outcome_visible") is not False:
        raise ValueError(f"event_{index}_outcome_visible_not_false")

    event_id = str(event.get("event_id") or "").strip()
    symbol = str(event.get("symbol") or "").strip().upper()
    side = str(event.get("side") or "").strip().lower()
    if not event_id:
        raise ValueError(f"event_{index}_event_id_missing")
    if not symbol:
        raise ValueError(f"event_{index}_symbol_missing")
    if side not in {"long", "short"}:
        raise ValueError(f"event_{index}_side_invalid")
    trigger_close = _timestamp(event.get("trigger_close_at"), f"event_{index}_trigger_close_at")
    return {
        "event_id": event_id,
        "symbol": symbol,
        "side": side,
        "trigger_close_at": trigger_close.isoformat(),
    }


def build_label_blind_cohort_partition(
    events: Iterable[dict[str, Any]],
    *,
    checked_at: datetime | str,
) -> dict[str, Any]:
    """Return a deterministic cohort boundary without reading outcome labels.

    The partition is not considered frozen until at least 60 independent events
    have matured under the preregistered 10-day clock. Once ready, the first 60
    matured events by trigger time (event_id as deterministic tie-breaker) form
    DEVELOPMENT. Every event after the 60th trigger boundary is VALIDATION,
    regardless of whether that later event has matured yet.
    """
    now = _timestamp(checked_at, "checked_at")
    normalized: list[tuple[datetime, str, dict[str, str], bool]] = []
    seen_ids: set[str] = set()
    seen_trigger_identity: set[tuple[str, str, str]] = set()

    for index, raw in enumerate(events):
        if not isinstance(raw, dict):
            raise ValueError(f"event_{index}_not_object")
        identity = _safe_event_identity(raw, index)
        event_id = identity["event_id"]
        if event_id in seen_ids:
            raise ValueError(f"duplicate_event_id:{event_id}")
        seen_ids.add(event_id)
        trigger_identity = (
            identity["symbol"],
            identity["side"],
            identity["trigger_close_at"],
        )
        if trigger_identity in seen_trigger_identity:
            raise ValueError(
                "duplicate_symbol_side_trigger_bar:"
                + ":".join(trigger_identity)
            )
        seen_trigger_identity.add(trigger_identity)
        trigger_close = _timestamp(identity["trigger_close_at"], f"event_{index}_trigger_close_at")
        matured = maturity_at(trigger_close) <= now
        normalized.append((trigger_close, event_id, identity, matured))

    normalized.sort(key=lambda item: (item[0], item[1]))
    matured_rows = [item for item in normalized if item[3]]
    ready = len(matured_rows) >= DEVELOPMENT_TARGET_MATURED_EVENTS

    base = {
        "partition_spec_version": PARTITION_SPEC_VERSION,
        "research_only": True,
        "label_blind": True,
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "checked_at": now.isoformat(),
        "observed_event_count": len(normalized),
        "matured_event_count": len(matured_rows),
        "development_target_matured_events": DEVELOPMENT_TARGET_MATURED_EVENTS,
        "validation_target_matured_events": VALIDATION_TARGET_MATURED_EVENTS,
        "development_partition_ready": ready,
        "development_event_count": DEVELOPMENT_TARGET_MATURED_EVENTS if ready else 0,
        "validation_observed_event_count": 0,
        "development_boundary_trigger_close_at": None,
        "development_event_ids": [],
        "validation_event_ids": [],
        "partition_fingerprint": None,
    }
    if not ready:
        return base

    development_rows = normalized[:DEVELOPMENT_TARGET_MATURED_EVENTS]
    # Readiness is based on matured count, and chronological ordering guarantees
    # every row before the 60th matured event is also matured.
    if any(not item[3] for item in development_rows):
        raise ValueError("development_partition_contains_unmatured_event")
    validation_rows = normalized[DEVELOPMENT_TARGET_MATURED_EVENTS:]
    development_ids = [item[1] for item in development_rows]
    validation_ids = [item[1] for item in validation_rows]
    boundary = development_rows[-1][0].isoformat()
    fingerprint_payload = {
        "partition_spec_version": PARTITION_SPEC_VERSION,
        "development_target_matured_events": DEVELOPMENT_TARGET_MATURED_EVENTS,
        "development_boundary_trigger_close_at": boundary,
        "development_events": [item[2] for item in development_rows],
    }
    base.update(
        {
            "development_event_ids": development_ids,
            "validation_event_ids": validation_ids,
            "validation_observed_event_count": len(validation_ids),
            "development_boundary_trigger_close_at": boundary,
            "partition_fingerprint": canonical_fingerprint(fingerprint_payload),
        }
    )
    return base
