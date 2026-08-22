"""Production-facing, outcome-blind status builder for barrier-clear v2.

Research only. Reads only event identity/side/timestamps/terminal state from the
existing barrier parent store and excludes every parent captured at or before
the frozen v2 activation boundary. No outcome fields are queried or exposed.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Iterable, Mapping

from research.day_barrier_clear_rearm_v1 import STUDY_ID as V1_STUDY_ID
from research.day_barrier_clear_rearm_v2 import build_side_stratified_partition
from research.day_barrier_clear_rearm_v2_activation import ACTIVATION_BOUNDARY, activation_status

TERMINAL = {"CLEARED", "INVALIDATED_BOUNDARY", "INVALIDATED_STRUCTURE"}
STATUS_SPEC_VERSION = "day-barrier-clear-rearm-v2-status-v1"


def build_v2_status_from_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    source_commit_sha: str | None,
    captured_at: datetime,
) -> dict[str, Any]:
    activation = activation_status()
    events: list[dict[str, Any]] = []
    excluded_pre_activation_parent = 0
    excluded_nonterminal = 0

    for row in rows:
        captured = row.get("captured_at")
        resolved = row.get("resolved_at")
        status = str(row.get("resolution_status") or "")
        if status not in TERMINAL or resolved is None:
            excluded_nonterminal += 1
            continue
        if captured is None or captured <= datetime.fromisoformat(ACTIVATION_BOUNDARY):
            excluded_pre_activation_parent += 1
            continue
        events.append(
            {
                "event_id": str(row.get("event_key") or ""),
                "side": str(row.get("side") or "").lower(),
                "terminal": True,
                "resolved_at": resolved,
            }
        )

    partition = build_side_stratified_partition(events, activation_boundary=ACTIVATION_BOUNDARY)
    sides = Counter(event["side"] for event in events)
    return {
        "status_spec_version": STATUS_SPEC_VERSION,
        "trial_id": partition["trial_id"],
        "activated": True,
        "activation_boundary": activation["activation_boundary"],
        "activation_rule": activation["activation_rule"],
        "source_commit_sha": source_commit_sha,
        "captured_at": captured_at.isoformat(),
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "historical_backfill_allowed": False,
        "v1_event_reuse_allowed": False,
        "pre_activation_parent_reuse_allowed": False,
        "eligible_terminal_event_count": len(events),
        "eligible_long_count": int(sides.get("long", 0)),
        "eligible_short_count": int(sides.get("short", 0)),
        "excluded_pre_activation_parent_count": excluded_pre_activation_parent,
        "excluded_nonterminal_count": excluded_nonterminal,
        "development_target": partition["development_target"],
        "development_per_side": partition["development_per_side"],
        "development_ready": partition["development_ready"],
        "development_event_count": partition["development_event_count"],
        "development_long_count": partition["development_long_count"],
        "development_short_count": partition["development_short_count"],
        "development_fingerprint": partition["development_fingerprint"],
        "validation_target": partition["validation_target"],
        "validation_per_side": partition["validation_per_side"],
        "validation_ready": partition["validation_ready"],
        "validation_event_count": partition["validation_event_count"],
        "validation_long_count": partition["validation_long_count"],
        "validation_short_count": partition["validation_short_count"],
        "validation_fingerprint": partition["validation_fingerprint"],
        "outcome_visible": False,
        "threshold_search_allowed": False,
        "promotion_allowed": False,
        "execution_authorized": False,
        "live_strategy_mutated": False,
    }


async def load_v2_status(
    connection: Any,
    *,
    source_commit_sha: str | None,
    captured_at: datetime,
) -> dict[str, Any]:
    rows = await connection.fetch(
        """
        SELECT event_key,captured_at,side,resolved_at,resolution_status
        FROM day_barrier_clear_rearm_v1_parent
        WHERE study=$1
        ORDER BY captured_at,event_key
        """,
        V1_STUDY_ID,
    )
    return build_v2_status_from_rows(
        rows,
        source_commit_sha=source_commit_sha,
        captured_at=captured_at,
    )
