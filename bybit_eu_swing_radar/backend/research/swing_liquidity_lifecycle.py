"""Prospective lifecycle adoption for the swing-liquidity validation trial.

Research only. Lifecycle milestones are recorded only from genuinely new forward
captures after each integration exists. Historical milestones are never backfilled,
and no lifecycle record changes live strategy, eligibility, scoring, or execution.
"""
from __future__ import annotations

from typing import Any

from research.research_governance import PIT_VERSION, trial_fingerprint, trial_manifest
from research.research_lifecycle_ledger import canonical_fingerprint, lifecycle_status, record_trial_event
from research.swing_liquidity_data_quality import (
    DATA_QUALITY_FORWARD_START_UTC,
    DATA_QUALITY_SPEC_VERSION,
    MIN_CONSECUTIVE_CAPTURES,
    evaluate_capture_rows,
)
from research.swing_liquidity_lineage import (
    LINEAGE_FORWARD_START_UTC,
    LINEAGE_SPEC_VERSION,
    evaluate_lineage_capture,
)

STUDY = "swing-liquidity-validation-v1"
ADOPTION_EVENT_ID = "swing-liquidity-lifecycle-adoption-v1-trial-registered"
PIT_AUDIT_EVENT_ID = "swing-liquidity-lifecycle-v1-pit-audit-recorded"
DATA_QUALITY_EVENT_ID = "swing-liquidity-lifecycle-v1-data-quality-gate-recorded"
LINEAGE_EVENT_ID = "swing-liquidity-lifecycle-v1-lineage-recorded"
DEVELOPMENT_TARGET_MATURED_EVENTS = 60
VALIDATION_TARGET_MATURED_EVENTS = 40


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


async def _load_post_adoption_pit_evidence(conn: Any, *, trial_id: str, trial_fp: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        """
        SELECT c.captured_at, c.inserted_at, c.feature_available_at,
               c.provenance_version, c.trial_id, c.trial_fingerprint,
               c.source_commit_sha
        FROM swing_liquidity_forward_captures AS c
        JOIN research_lifecycle_events AS e
          ON e.trial_id=$1 AND e.entity_type='TRIAL' AND e.entity_id=$1 AND e.event_id=$3
        WHERE c.inserted_at > e.recorded_at
          AND c.provenance_version=$4
          AND c.feature_available_at IS NOT NULL
          AND c.trial_id=$1 AND c.trial_fingerprint=$2
        ORDER BY c.inserted_at DESC, c.captured_at DESC
        LIMIT 1
        """,
        trial_id, trial_fp, ADOPTION_EVENT_ID, PIT_VERSION,
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


async def _load_post_pit_data_quality_rows(conn: Any, *, trial_id: str, trial_fp: str) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT c.captured_at, c.inserted_at, c.feature_available_at,
               c.provenance_version, c.trial_id, c.trial_fingerprint,
               c.source_commit_sha, c.candidate_count, c.orderbook_count,
               c.orderbook_error_count
        FROM swing_liquidity_forward_captures AS c
        JOIN research_lifecycle_events AS e
          ON e.trial_id=$1 AND e.entity_type='TRIAL' AND e.entity_id=$1 AND e.event_id=$3
        WHERE c.inserted_at > e.recorded_at
          AND c.inserted_at >= $5
          AND c.trial_id=$1 AND c.trial_fingerprint=$2
        ORDER BY c.inserted_at DESC, c.captured_at DESC
        LIMIT $4
        """,
        trial_id, trial_fp, PIT_AUDIT_EVENT_ID, MIN_CONSECUTIVE_CAPTURES, DATA_QUALITY_FORWARD_START_UTC,
    )
    normalized: list[dict[str, Any]] = []
    for row in reversed(rows):
        record = dict(row)
        if str(record.get("trial_id") or "") != trial_id:
            raise RuntimeError("post-PIT data-quality evidence trial id mismatch")
        if str(record.get("trial_fingerprint") or "") != trial_fp:
            raise RuntimeError("post-PIT data-quality evidence trial fingerprint mismatch")
        normalized.append(record)
    return normalized


async def _load_post_data_quality_lineage_evidence(
    conn: Any, *, trial_id: str, trial_fp: str
) -> tuple[dict[str, Any] | None, str | None]:
    event = await conn.fetchrow(
        """
        SELECT event_fingerprint, recorded_at
        FROM research_lifecycle_events
        WHERE trial_id=$1 AND entity_type='TRIAL' AND entity_id=$1 AND event_id=$2
        LIMIT 1
        """,
        trial_id, DATA_QUALITY_EVENT_ID,
    )
    if event is None:
        return None, None
    event_record = dict(event)
    dq_fp = str(event_record.get("event_fingerprint") or "")
    row = await conn.fetchrow(
        """
        SELECT captured_at, inserted_at, feature_available_at, provenance_version,
               trial_id, trial_fingerprint, source_commit_sha, candidate_count,
               orderbook_count, orderbook_error_count
        FROM swing_liquidity_forward_captures
        WHERE trial_id=$1 AND trial_fingerprint=$2
          AND inserted_at > $3
          AND inserted_at >= $4
        ORDER BY inserted_at DESC, captured_at DESC
        LIMIT 1
        """,
        trial_id, trial_fp, event_record.get("recorded_at"), LINEAGE_FORWARD_START_UTC,
    )
    return (dict(row) if row is not None else None), dq_fp


async def record_lifecycle_on_capture_persistence(
    conn: Any, *, inserted_capture: bool, source_commit_sha: str | None = None
) -> dict[str, Any]:
    """Advance only the next prospectively evidenced lifecycle milestone."""
    if not inserted_capture:
        return _base_result(attempted=False, reason="capture_not_inserted")

    manifest = trial_manifest(STUDY)
    trial_id = str(manifest["trial_id"])
    fingerprint = trial_fingerprint(STUDY)
    status = await lifecycle_status(conn, STUDY, entity_type="TRIAL", entity_id=trial_id)
    event_count = int(status.get("event_count") or 0)
    current_event_type = status.get("current_event_type")

    if event_count == 0:
        event = await record_trial_event(
            conn, STUDY,
            event_id=ADOPTION_EVENT_ID,
            event_type="TRIAL_REGISTERED",
            event_payload={
                "summary": "Prospective lifecycle adoption recorded on a newly persisted swing-liquidity forward capture.",
                "evidence_refs": [fingerprint],
                "trial_registered": True,
                "prospective_adoption": True,
                "historical_backfill": False,
            },
            source_commit_sha=source_commit_sha,
        )
        return _base_result(inserted=bool(event.get("inserted")), event_type=event.get("event_type"), event_fingerprint=event.get("event_fingerprint"), recorded_at=event.get("recorded_at"), reason="prospective_trial_registration")

    if current_event_type == "TRIAL_REGISTERED":
        evidence = await _load_post_adoption_pit_evidence(conn, trial_id=trial_id, trial_fp=fingerprint)
        if evidence is None:
            return _base_result(event_type="TRIAL_REGISTERED", reason="waiting_for_fresh_post_adoption_pit_capture")
        evidence_fp = canonical_fingerprint(evidence)
        event = await record_trial_event(
            conn, STUDY,
            event_id=PIT_AUDIT_EVENT_ID,
            event_type="PIT_AUDIT_RECORDED",
            event_payload={
                "summary": "Point-in-time audit recorded from a fresh PIT-v1 swing-liquidity capture persisted after prospective lifecycle adoption.",
                "evidence_refs": [fingerprint, evidence_fp],
                "point_in_time_verified": True,
                "provenance_version": PIT_VERSION,
                "prospective_adoption": True,
                "historical_backfill": False,
                "evidence_capture_fingerprint": evidence_fp,
            },
            source_commit_sha=source_commit_sha,
        )
        return _base_result(inserted=bool(event.get("inserted")), event_type=event.get("event_type"), event_fingerprint=event.get("event_fingerprint"), recorded_at=event.get("recorded_at"), evidence_capture_fingerprint=evidence_fp, reason="prospective_pit_audit")

    if current_event_type == "PIT_AUDIT_RECORDED":
        rows = await _load_post_pit_data_quality_rows(conn, trial_id=trial_id, trial_fp=fingerprint)
        quality = evaluate_capture_rows(rows)
        if quality.get("ready") is not True:
            return _base_result(event_type="PIT_AUDIT_RECORDED", reason=str(quality.get("reason") or "waiting_for_data_quality_gate"), data_quality=quality)
        event = await record_trial_event(
            conn, STUDY,
            event_id=DATA_QUALITY_EVENT_ID,
            event_type="DATA_QUALITY_GATE_RECORDED",
            event_payload={
                "summary": "Data-quality gate recorded from three consecutive post-PIT label-blind captures with complete contemporaneous order-book coverage.",
                "evidence_refs": [fingerprint, *quality["evidence_fingerprints"]],
                "data_quality_gate_passed": True,
                "data_quality_spec_version": DATA_QUALITY_SPEC_VERSION,
                "data_quality_forward_start_utc": DATA_QUALITY_FORWARD_START_UTC.isoformat(),
                "consecutive_capture_count": quality["capture_count"],
                "required_consecutive_capture_count": quality["required_capture_count"],
                "evidence_window_fingerprint": quality["evidence_window_fingerprint"],
                "full_orderbook_coverage": True,
                "orderbook_errors_allowed_per_capture": 0,
                "outcome_fields_used": False,
                "threshold_search_allowed": False,
                "prospective_adoption": True,
                "historical_backfill": False,
            },
            source_commit_sha=source_commit_sha,
        )
        return _base_result(inserted=bool(event.get("inserted")), event_type=event.get("event_type"), event_fingerprint=event.get("event_fingerprint"), recorded_at=event.get("recorded_at"), data_quality=quality, reason="prospective_data_quality_gate")

    if current_event_type == "DATA_QUALITY_GATE_RECORDED":
        row, dq_event_fp = await _load_post_data_quality_lineage_evidence(conn, trial_id=trial_id, trial_fp=fingerprint)
        if row is None or not dq_event_fp:
            return _base_result(event_type="DATA_QUALITY_GATE_RECORDED", reason="waiting_for_fresh_post_data_quality_lineage_capture")
        lineage = evaluate_lineage_capture(
            row,
            trial_id=trial_id,
            trial_fingerprint=fingerprint,
            data_quality_event_fingerprint=dq_event_fp,
        )
        if lineage.get("ready") is not True:
            return _base_result(event_type="DATA_QUALITY_GATE_RECORDED", reason=str(lineage.get("reason") or "waiting_for_lineage_gate"), lineage=lineage)
        event = await record_trial_event(
            conn, STUDY,
            event_id=LINEAGE_EVENT_ID,
            event_type="LINEAGE_RECORDED",
            event_payload={
                "summary": "Dataset lineage recorded from a fresh post-data-quality label-blind swing-liquidity capture.",
                "evidence_refs": [fingerprint, dq_event_fp, lineage["evidence_fingerprint"], lineage["lineage_fingerprint"]],
                "lineage_verified": True,
                "lineage_spec_version": LINEAGE_SPEC_VERSION,
                "lineage_forward_start_utc": LINEAGE_FORWARD_START_UTC.isoformat(),
                "lineage_fingerprint": lineage["lineage_fingerprint"],
                "outcome_fields_used": False,
                "threshold_search_allowed": False,
                "prospective_adoption": True,
                "historical_backfill": False,
            },
            source_commit_sha=source_commit_sha,
        )
        return _base_result(inserted=bool(event.get("inserted")), event_type=event.get("event_type"), event_fingerprint=event.get("event_fingerprint"), recorded_at=event.get("recorded_at"), lineage=lineage, reason="prospective_lineage_gate")

    if current_event_type == "LINEAGE_RECORDED":
        return _base_result(
            event_type="LINEAGE_RECORDED",
            reason="waiting_for_development_maturity_gate",
            development={
                "required_matured_event_count": DEVELOPMENT_TARGET_MATURED_EVENTS,
                "validation_target_matured_event_count": VALIDATION_TARGET_MATURED_EVENTS,
                "maturity_source": "label_blind_forward_event_status",
                "development_evidence_recorded": False,
                "outcome_visible": False,
                "threshold_search_allowed": False,
                "promotion_allowed": False,
            },
        )

    return _base_result(event_type=current_event_type, reason="lifecycle_already_beyond_development_gate")
