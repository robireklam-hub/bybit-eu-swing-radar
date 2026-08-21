"""Prospective lifecycle adoption for the swing-liquidity validation trial.

Research only. Lifecycle milestones are recorded only from genuinely new forward
captures after each integration exists. Historical milestones are never backfilled,
and no lifecycle record changes live strategy, eligibility, scoring, or execution.
"""
from __future__ import annotations

from typing import Any

from research.research_governance import PIT_VERSION, trial_fingerprint, trial_manifest
from research.research_lifecycle_ledger import (
    canonical_fingerprint,
    lifecycle_status,
    record_trial_event,
)

STUDY = "swing-liquidity-validation-v1"
ADOPTION_EVENT_ID = "swing-liquidity-lifecycle-adoption-v1-trial-registered"
PIT_AUDIT_EVENT_ID = "swing-liquidity-lifecycle-v1-pit-audit-recorded"


def _base_result(**overrides: Any) -> dict[str, Any]:
    result = {
        "attempted": True,
        "inserted": False,
        "event_type": None,
        "reason": None,
        "prospective_adoption": True,
        "historical_backfill": False,
        "research_only": True,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
    }
    result.update(overrides)
    return result


async def _load_post_adoption_pit_evidence(
    conn: Any,
    *,
    trial_id: str,
    trial_fp: str,
) -> dict[str, Any] | None:
    """Return one fresh PIT-v1 capture created after lifecycle adoption.

    This deliberately keys freshness from the immutable TRIAL_REGISTERED ledger
    timestamp. A capture that predates adoption can therefore never be used to
    reconstruct the PIT milestone retroactively.
    """
    row = await conn.fetchrow(
        """
        SELECT c.captured_at, c.inserted_at, c.feature_available_at,
               c.provenance_version, c.trial_id, c.trial_fingerprint,
               c.source_commit_sha
        FROM swing_liquidity_forward_captures AS c
        JOIN research_lifecycle_events AS e
          ON e.trial_id=$1
         AND e.entity_type='TRIAL'
         AND e.entity_id=$1
         AND e.event_id=$3
        WHERE c.inserted_at > e.recorded_at
          AND c.provenance_version=$4
          AND c.feature_available_at IS NOT NULL
          AND c.trial_id=$1
          AND c.trial_fingerprint=$2
        ORDER BY c.inserted_at DESC, c.captured_at DESC
        LIMIT 1
        """,
        trial_id,
        trial_fp,
        ADOPTION_EVENT_ID,
        PIT_VERSION,
    )
    if row is None:
        return None
    record = dict(row)
    evidence = {
        "captured_at": str(record.get("captured_at")),
        "inserted_at": str(record.get("inserted_at")),
        "feature_available_at": str(record.get("feature_available_at")),
        "provenance_version": str(record.get("provenance_version") or ""),
        "trial_id": str(record.get("trial_id") or ""),
        "trial_fingerprint": str(record.get("trial_fingerprint") or ""),
        "source_commit_sha": record.get("source_commit_sha"),
    }
    if evidence["provenance_version"] != PIT_VERSION:
        raise RuntimeError("post-adoption PIT evidence provenance version mismatch")
    if evidence["trial_id"] != trial_id or evidence["trial_fingerprint"] != trial_fp:
        raise RuntimeError("post-adoption PIT evidence trial identity mismatch")
    return evidence


async def record_lifecycle_on_capture_persistence(
    conn: Any,
    *,
    inserted_capture: bool,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Advance only the next prospectively evidenced lifecycle milestone.

    Duplicate capture retries never advance lifecycle state. The first fresh
    post-integration capture adopts the trial with TRIAL_REGISTERED. A later,
    separately persisted PIT-v1 capture may record PIT_AUDIT_RECORDED. No later
    milestone is handled by this helper.
    """
    if not inserted_capture:
        return _base_result(
            attempted=False,
            reason="capture_not_inserted",
        )

    manifest = trial_manifest(STUDY)
    trial_id = str(manifest["trial_id"])
    fingerprint = trial_fingerprint(STUDY)
    status = await lifecycle_status(
        conn,
        STUDY,
        entity_type="TRIAL",
        entity_id=trial_id,
    )
    event_count = int(status.get("event_count") or 0)
    current_event_type = status.get("current_event_type")

    if event_count == 0:
        event = await record_trial_event(
            conn,
            STUDY,
            event_id=ADOPTION_EVENT_ID,
            event_type="TRIAL_REGISTERED",
            event_payload={
                "summary": (
                    "Prospective lifecycle adoption recorded on a newly persisted "
                    "swing-liquidity forward capture."
                ),
                "evidence_refs": [fingerprint],
                "trial_registered": True,
                "prospective_adoption": True,
                "historical_backfill": False,
            },
            source_commit_sha=source_commit_sha,
        )
        return _base_result(
            inserted=bool(event.get("inserted")),
            event_type=event.get("event_type"),
            event_fingerprint=event.get("event_fingerprint"),
            recorded_at=event.get("recorded_at"),
            reason="prospective_trial_registration",
        )

    if current_event_type == "TRIAL_REGISTERED":
        evidence = await _load_post_adoption_pit_evidence(
            conn,
            trial_id=trial_id,
            trial_fp=fingerprint,
        )
        if evidence is None:
            return _base_result(
                event_type="TRIAL_REGISTERED",
                reason="waiting_for_fresh_post_adoption_pit_capture",
            )
        evidence_fp = canonical_fingerprint(evidence)
        event = await record_trial_event(
            conn,
            STUDY,
            event_id=PIT_AUDIT_EVENT_ID,
            event_type="PIT_AUDIT_RECORDED",
            event_payload={
                "summary": (
                    "Point-in-time audit recorded from a fresh PIT-v1 swing-liquidity "
                    "capture persisted after prospective lifecycle adoption."
                ),
                "evidence_refs": [fingerprint, evidence_fp],
                "point_in_time_verified": True,
                "provenance_version": PIT_VERSION,
                "prospective_adoption": True,
                "historical_backfill": False,
                "evidence_capture_fingerprint": evidence_fp,
            },
            source_commit_sha=source_commit_sha,
        )
        return _base_result(
            inserted=bool(event.get("inserted")),
            event_type=event.get("event_type"),
            event_fingerprint=event.get("event_fingerprint"),
            recorded_at=event.get("recorded_at"),
            evidence_capture_fingerprint=evidence_fp,
            reason="prospective_pit_audit",
        )

    return _base_result(
        event_type=current_event_type,
        reason="lifecycle_already_beyond_pit_adoption",
    )
