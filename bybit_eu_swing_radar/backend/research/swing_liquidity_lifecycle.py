"""Prospective lifecycle adoption for the swing-liquidity validation trial.

Research only. This module deliberately records only the first lifecycle event
for the already-running trial, and only when a brand-new forward capture is
persisted after this integration exists. It never backfills historical lifecycle
milestones and never changes live strategy, eligibility, scoring, or execution.
"""
from __future__ import annotations

from typing import Any

from research.research_governance import trial_fingerprint, trial_manifest
from research.research_lifecycle_ledger import lifecycle_status, record_trial_event

STUDY = "swing-liquidity-validation-v1"
ADOPTION_EVENT_ID = "swing-liquidity-lifecycle-adoption-v1-trial-registered"


async def record_lifecycle_on_capture_persistence(
    conn: Any,
    *,
    inserted_capture: bool,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Adopt the trial lifecycle on a genuinely new post-integration capture.

    Duplicate capture retries never advance lifecycle state. Existing lifecycle
    state is never reconstructed or moved forward here; later milestones require
    separate prospective integrations backed by fresh evidence.
    """
    if not inserted_capture:
        return {
            "attempted": False,
            "inserted": False,
            "event_type": None,
            "reason": "capture_not_inserted",
            "prospective_adoption": True,
            "historical_backfill": False,
            "research_only": True,
            "live_strategy_mutated": False,
            "production_eligibility_mutated": False,
            "execution_authorized": False,
        }

    manifest = trial_manifest(STUDY)
    trial_id = str(manifest["trial_id"])
    status = await lifecycle_status(
        conn,
        STUDY,
        entity_type="TRIAL",
        entity_id=trial_id,
    )
    if int(status.get("event_count") or 0) > 0:
        return {
            "attempted": True,
            "inserted": False,
            "event_type": status.get("current_event_type"),
            "reason": "lifecycle_already_adopted",
            "prospective_adoption": True,
            "historical_backfill": False,
            "research_only": True,
            "live_strategy_mutated": False,
            "production_eligibility_mutated": False,
            "execution_authorized": False,
        }

    fingerprint = trial_fingerprint(STUDY)
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
    return {
        "attempted": True,
        "inserted": bool(event.get("inserted")),
        "event_type": event.get("event_type"),
        "event_fingerprint": event.get("event_fingerprint"),
        "recorded_at": event.get("recorded_at"),
        "reason": "prospective_trial_registration",
        "prospective_adoption": True,
        "historical_backfill": False,
        "research_only": True,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
    }
