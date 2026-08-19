"""Append-only research experiment and feature lifecycle ledger.

The ledger records immutable governance evidence for preregistered research
trials and feature candidates. It never mutates strategy, scoring, eligibility,
orders, positions, or execution state. A recorded PROMOTE decision is only a
research-governance decision record; live promotion requires a separate change.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from research.research_governance import trial_fingerprint, trial_manifest
from research.research_trial_registry import ensure_trial_registered

LEDGER_VERSION = "research-lifecycle-ledger-v1"
ENTITY_TRIAL = "TRIAL"
ENTITY_FEATURE = "FEATURE"
DECISION_EVENT = "DECISION_RECORDED"

TRIAL_EVENT_ORDER = {
    "TRIAL_REGISTERED": 10,
    "PIT_AUDIT_RECORDED": 30,
    "DATA_QUALITY_GATE_RECORDED": 40,
    "LINEAGE_RECORDED": 50,
    "DEVELOPMENT_EVIDENCE_RECORDED": 60,
    "WALK_FORWARD_EVIDENCE_RECORDED": 70,
    "MULTIPLE_TESTING_PLAN_RECORDED": 80,
    "OOS_SEAL_RECORDED": 90,
    "OOS_OPEN_RECORDED": 100,
    "ROBUSTNESS_EVIDENCE_RECORDED": 110,
    "SHADOW_EVIDENCE_RECORDED": 120,
    DECISION_EVENT: 130,
}
FEATURE_EVENT_ORDER = {
    "HYPOTHESIS_RECORDED": 10,
    "FEATURE_CARD_RECORDED": 20,
    "PIT_AUDIT_RECORDED": 30,
    "DATA_QUALITY_GATE_RECORDED": 40,
    "LINEAGE_RECORDED": 50,
    "DEVELOPMENT_EVIDENCE_RECORDED": 60,
    "WALK_FORWARD_EVIDENCE_RECORDED": 70,
    "MULTIPLE_TESTING_PLAN_RECORDED": 80,
    "OOS_SEAL_RECORDED": 90,
    "OOS_OPEN_RECORDED": 100,
    "ROBUSTNESS_EVIDENCE_RECORDED": 110,
    "SHADOW_EVIDENCE_RECORDED": 120,
    DECISION_EVENT: 130,
}
EVENT_ORDER = {ENTITY_TRIAL: TRIAL_EVENT_ORDER, ENTITY_FEATURE: FEATURE_EVENT_ORDER}

PROMOTION_REQUIRED = {
    ENTITY_TRIAL: tuple(event for event in TRIAL_EVENT_ORDER if event != DECISION_EVENT),
    ENTITY_FEATURE: tuple(event for event in FEATURE_EVENT_ORDER if event != DECISION_EVENT),
}

EVENT_REQUIRED_FLAGS: dict[str, tuple[str, Any]] = {
    "TRIAL_REGISTERED": ("trial_registered", True),
    "HYPOTHESIS_RECORDED": ("preregistered", True),
    "FEATURE_CARD_RECORDED": ("feature_card_recorded", True),
    "PIT_AUDIT_RECORDED": ("point_in_time_verified", True),
    "DATA_QUALITY_GATE_RECORDED": ("data_quality_gate_passed", True),
    "LINEAGE_RECORDED": ("lineage_verified", True),
    "DEVELOPMENT_EVIDENCE_RECORDED": ("development_complete", True),
    "WALK_FORWARD_EVIDENCE_RECORDED": ("walk_forward_complete", True),
    "MULTIPLE_TESTING_PLAN_RECORDED": ("multiple_testing_plan_frozen", True),
    "OOS_SEAL_RECORDED": ("oos_sealed", True),
    "OOS_OPEN_RECORDED": ("oos_opened", True),
    "ROBUSTNESS_EVIDENCE_RECORDED": ("robustness_evaluated", True),
    "SHADOW_EVIDENCE_RECORDED": ("shadow_evaluated", True),
}

FORBIDDEN_RAW_PAYLOAD_KEYS = {
    "returns",
    "raw_returns",
    "outcomes",
    "raw_outcomes",
    "oos_payload",
    "net_r",
    "mfe_r",
    "mae_r",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_ALL_EVENT_TYPES = tuple(sorted(set(TRIAL_EVENT_ORDER) | set(FEATURE_EVENT_ORDER)))
_EVENT_SQL = ",".join("'" + event + "'" for event in _ALL_EVENT_TYPES)

LIFECYCLE_LEDGER_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS research_lifecycle_events (
    trial_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    research_family TEXT NOT NULL,
    trial_fingerprint TEXT NOT NULL,
    entity_type TEXT NOT NULL CHECK (entity_type IN ('TRIAL','FEATURE')),
    entity_id TEXT NOT NULL,
    entity_spec_version TEXT NOT NULL,
    entity_fingerprint TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ({_EVENT_SQL})),
    event_fingerprint TEXT NOT NULL UNIQUE,
    event_payload_fingerprint TEXT NOT NULL,
    event_payload JSONB NOT NULL,
    source_commit_sha TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trial_id, revision, entity_type, entity_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_research_lifecycle_entity
ON research_lifecycle_events(trial_id, revision, entity_type, entity_id, recorded_at, event_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_research_lifecycle_decision
ON research_lifecycle_events(trial_id, revision, entity_type, entity_id)
WHERE event_type = 'DECISION_RECORDED';

CREATE OR REPLACE FUNCTION reject_research_lifecycle_row_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research_lifecycle_events is append-only';
END;
$$;
DROP TRIGGER IF EXISTS trg_research_lifecycle_no_row_mutation ON research_lifecycle_events;
CREATE TRIGGER trg_research_lifecycle_no_row_mutation
BEFORE UPDATE OR DELETE ON research_lifecycle_events
FOR EACH ROW EXECUTE FUNCTION reject_research_lifecycle_row_mutation();

CREATE OR REPLACE FUNCTION reject_research_lifecycle_truncate()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research_lifecycle_events cannot be truncated';
END;
$$;
DROP TRIGGER IF EXISTS trg_research_lifecycle_no_truncate ON research_lifecycle_events;
CREATE TRIGGER trg_research_lifecycle_no_truncate
BEFORE TRUNCATE ON research_lifecycle_events
FOR EACH STATEMENT EXECUTE FUNCTION reject_research_lifecycle_truncate();
"""


def spec() -> dict[str, Any]:
    return {
        "ledger_version": LEDGER_VERSION,
        "research_only": True,
        "append_only": True,
        "database_role_mutation_guards": ["UPDATE", "DELETE", "TRUNCATE"],
        "entities": [ENTITY_TRIAL, ENTITY_FEATURE],
        "monotonic_lifecycle": True,
        "exact_retry_idempotent": True,
        "conflicting_event_id_fails_closed": True,
        "single_terminal_decision_per_entity": True,
        "promotion_decision_requires_full_evidence_chain": True,
        "promotion_decision_executes_live_change": False,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
        "raw_oos_payload_allowed": False,
        "trial_event_order": dict(TRIAL_EVENT_ORDER),
        "feature_event_order": dict(FEATURE_EVENT_ORDER),
    }


def canonical_fingerprint(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("lifecycle payload must be canonical JSON-compatible data") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _json_object(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"stored {field} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"stored {field} is not an object")
    return value


def _find_forbidden_keys(value: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in FORBIDDEN_RAW_PAYLOAD_KEYS:
                found.append(child_path)
            found.extend(_find_forbidden_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_forbidden_keys(child, f"{path}[{index}]"))
    return found


def _evidence_refs(payload: Mapping[str, Any]) -> list[str]:
    refs = payload.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("lifecycle evidence event requires non-empty evidence_refs")
    normalized: list[str] = []
    for value in refs:
        text = str(value or "").lower()
        if not _SHA256_RE.fullmatch(text):
            raise ValueError("evidence_refs must contain SHA-256 hex fingerprints")
        normalized.append(text)
    if len(set(normalized)) != len(normalized):
        raise ValueError("evidence_refs must not contain duplicates")
    return normalized


def validate_event_payload(
    *,
    entity_type: str,
    event_type: str,
    payload: Mapping[str, Any],
    entity_fingerprint: str,
) -> dict[str, Any]:
    if entity_type not in EVENT_ORDER:
        raise ValueError("entity_type must be TRIAL or FEATURE")
    if event_type not in EVENT_ORDER[entity_type]:
        raise ValueError(f"event_type {event_type!r} is not valid for {entity_type}")
    if not isinstance(payload, Mapping):
        raise ValueError("event_payload must be an object")
    result = dict(payload)
    summary = result.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("event_payload requires non-empty summary")
    forbidden = _find_forbidden_keys(result)
    if forbidden:
        raise ValueError("raw outcome/OOS payload keys are forbidden in lifecycle ledger: " + ",".join(forbidden))
    if event_type != DECISION_EVENT:
        _evidence_refs(result)
    required = EVENT_REQUIRED_FLAGS.get(event_type)
    if required is not None:
        key, expected = required
        if result.get(key) != expected:
            raise ValueError(f"{event_type} requires {key}={expected!r}")
    if event_type == "FEATURE_CARD_RECORDED":
        if result.get("feature_card_fingerprint") != entity_fingerprint:
            raise ValueError("FEATURE_CARD_RECORDED must bind the exact feature_card_fingerprint")
        if entity_fingerprint not in _evidence_refs(result):
            raise ValueError("FEATURE_CARD_RECORDED evidence_refs must include feature_card_fingerprint")
    if event_type == "OOS_OPEN_RECORDED" and result.get("oos_tuning_forbidden") is not True:
        raise ValueError("OOS_OPEN_RECORDED requires oos_tuning_forbidden=True")
    if event_type == DECISION_EVENT:
        decision = result.get("decision")
        if decision not in {"PROMOTE", "REJECT"}:
            raise ValueError("DECISION_RECORDED decision must be PROMOTE or REJECT")
        if not isinstance(result.get("authorized_by"), str) or not result["authorized_by"].strip():
            raise ValueError("DECISION_RECORDED requires authorized_by")
        if not isinstance(result.get("reason"), str) or not result["reason"].strip():
            raise ValueError("DECISION_RECORDED requires reason")
        if result.get("live_mutation_authorized") is not False:
            raise ValueError("DECISION_RECORDED requires live_mutation_authorized=False")
        if decision == "PROMOTE" and result.get("promotion_prerequisites_frozen") is not True:
            raise ValueError("PROMOTE requires promotion_prerequisites_frozen=True")
    canonical_fingerprint(result)
    return result


def _identity(study: str) -> tuple[dict[str, Any], str, str, int, str]:
    manifest = trial_manifest(study)
    fingerprint = trial_fingerprint(study)
    return (
        manifest,
        fingerprint,
        str(manifest["trial_id"]),
        int(manifest["revision"]),
        str(manifest["research_family"]),
    )


def _event_fingerprint(
    *,
    trial_id: str,
    revision: int,
    entity_type: str,
    entity_id: str,
    entity_spec_version: str,
    entity_fingerprint: str,
    event_id: str,
    event_type: str,
    event_payload_fingerprint: str,
) -> str:
    return canonical_fingerprint(
        {
            "ledger_version": LEDGER_VERSION,
            "trial_id": trial_id,
            "revision": revision,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_spec_version": entity_spec_version,
            "entity_fingerprint": entity_fingerprint,
            "event_id": event_id,
            "event_type": event_type,
            "event_payload_fingerprint": event_payload_fingerprint,
        }
    )


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _validate_stored_event(
    row: Any,
    *,
    trial_id: str,
    revision: int,
    research_family: str,
    trial_fp: str,
    entity_type: str,
    entity_id: str,
    entity_spec_version: str,
    entity_fingerprint: str,
    event_id: str,
    event_type: str,
    event_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("lifecycle event row is missing")
    record = dict(row)
    stored_payload = _json_object(record.get("event_payload"), "event_payload")
    payload_fp = canonical_fingerprint(dict(event_payload))
    expected_event_fp = _event_fingerprint(
        trial_id=trial_id,
        revision=revision,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_spec_version=entity_spec_version,
        entity_fingerprint=entity_fingerprint,
        event_id=event_id,
        event_type=event_type,
        event_payload_fingerprint=payload_fp,
    )
    mismatches: list[str] = []
    expected_pairs = {
        "trial_id": (str(record.get("trial_id") or ""), trial_id),
        "research_family": (str(record.get("research_family") or ""), research_family),
        "trial_fingerprint": (str(record.get("trial_fingerprint") or ""), trial_fp),
        "entity_type": (str(record.get("entity_type") or ""), entity_type),
        "entity_id": (str(record.get("entity_id") or ""), entity_id),
        "entity_spec_version": (str(record.get("entity_spec_version") or ""), entity_spec_version),
        "entity_fingerprint": (str(record.get("entity_fingerprint") or ""), entity_fingerprint),
        "event_id": (str(record.get("event_id") or ""), event_id),
        "event_type": (str(record.get("event_type") or ""), event_type),
        "event_payload_fingerprint": (str(record.get("event_payload_fingerprint") or ""), payload_fp),
        "event_fingerprint": (str(record.get("event_fingerprint") or ""), expected_event_fp),
    }
    for field, (actual, expected) in expected_pairs.items():
        if actual != expected:
            mismatches.append(field)
    try:
        stored_revision = int(record.get("revision"))
    except (TypeError, ValueError):
        stored_revision = -1
    if stored_revision != revision:
        mismatches.append("revision")
    if stored_payload != dict(event_payload):
        mismatches.append("event_payload")
    if canonical_fingerprint(stored_payload) != payload_fp:
        mismatches.append("stored_event_payload_fingerprint")
    if mismatches:
        raise RuntimeError("immutable lifecycle event conflict: " + ",".join(sorted(set(mismatches))))
    return {
        "ledger_version": LEDGER_VERSION,
        "trial_id": trial_id,
        "revision": revision,
        "research_family": research_family,
        "trial_fingerprint": trial_fp,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "entity_spec_version": entity_spec_version,
        "entity_fingerprint": entity_fingerprint,
        "event_id": event_id,
        "event_type": event_type,
        "event_payload_fingerprint": payload_fp,
        "event_fingerprint": expected_event_fp,
        "source_commit_sha": record.get("source_commit_sha"),
        "recorded_at": _iso(record.get("recorded_at")),
        "immutable": True,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
    }


async def _fetch_events(
    conn: Any,
    *,
    trial_id: str,
    revision: int,
    entity_type: str,
    entity_id: str,
) -> list[dict[str, Any]]:
    rows = await conn.fetch(
        """
        SELECT trial_id,revision,research_family,trial_fingerprint,
               entity_type,entity_id,entity_spec_version,entity_fingerprint,
               event_id,event_type,event_fingerprint,event_payload_fingerprint,
               event_payload,source_commit_sha,recorded_at
        FROM research_lifecycle_events
        WHERE trial_id=$1 AND revision=$2 AND entity_type=$3 AND entity_id=$4
        ORDER BY recorded_at ASC, event_id ASC
        """,
        trial_id,
        revision,
        entity_type,
        entity_id,
    )
    return [dict(row) for row in rows]


def _stage_rank(entity_type: str, event_type: str) -> int:
    return EVENT_ORDER[entity_type][event_type]


def _validate_monotonic(existing: Sequence[Mapping[str, Any]], entity_type: str, event_type: str) -> None:
    if any(str(row.get("event_type")) == DECISION_EVENT for row in existing):
        raise RuntimeError("lifecycle entity already has a terminal decision")
    if not existing:
        return
    current = max(_stage_rank(entity_type, str(row.get("event_type"))) for row in existing)
    proposed = _stage_rank(entity_type, event_type)
    if proposed < current:
        raise RuntimeError("lifecycle event would move entity backward in the research lifecycle")


def _promotion_missing(existing: Sequence[Mapping[str, Any]], entity_type: str) -> list[str]:
    present = {str(row.get("event_type")) for row in existing}
    return [event for event in PROMOTION_REQUIRED[entity_type] if event not in present]


async def _record_event(
    conn: Any,
    study: str,
    *,
    entity_type: str,
    entity_id: str,
    entity_spec_version: str,
    entity_fingerprint: str,
    event_id: str,
    event_type: str,
    event_payload: Mapping[str, Any],
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    _, trial_fp, trial_id, revision, research_family = _identity(study)
    if not event_id or not str(event_id).strip():
        raise ValueError("event_id is required")
    if not entity_id or not str(entity_id).strip():
        raise ValueError("entity_id is required")
    if not entity_spec_version or not str(entity_spec_version).strip():
        raise ValueError("entity_spec_version is required")
    if not _SHA256_RE.fullmatch(str(entity_fingerprint).lower()):
        raise ValueError("entity_fingerprint must be a SHA-256 hex fingerprint")
    normalized_payload = validate_event_payload(
        entity_type=entity_type,
        event_type=event_type,
        payload=event_payload,
        entity_fingerprint=str(entity_fingerprint).lower(),
    )
    payload_fp = canonical_fingerprint(normalized_payload)
    event_fp = _event_fingerprint(
        trial_id=trial_id,
        revision=revision,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_spec_version=entity_spec_version,
        entity_fingerprint=str(entity_fingerprint).lower(),
        event_id=event_id,
        event_type=event_type,
        event_payload_fingerprint=payload_fp,
    )
    registry = await ensure_trial_registered(conn, study, source_commit_sha=source_commit_sha)
    if registry.get("manifest_fingerprint") != trial_fp:
        raise RuntimeError("durable trial registry fingerprint mismatch before lifecycle event")
    await conn.execute(LIFECYCLE_LEDGER_SCHEMA_SQL)

    existing_same = await conn.fetchrow(
        """
        SELECT trial_id,revision,research_family,trial_fingerprint,
               entity_type,entity_id,entity_spec_version,entity_fingerprint,
               event_id,event_type,event_fingerprint,event_payload_fingerprint,
               event_payload,source_commit_sha,recorded_at
        FROM research_lifecycle_events
        WHERE trial_id=$1 AND revision=$2 AND entity_type=$3 AND entity_id=$4 AND event_id=$5
        """,
        trial_id,
        revision,
        entity_type,
        entity_id,
        event_id,
    )
    if existing_same is not None:
        validated = _validate_stored_event(
            existing_same,
            trial_id=trial_id,
            revision=revision,
            research_family=research_family,
            trial_fp=trial_fp,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_spec_version=entity_spec_version,
            entity_fingerprint=str(entity_fingerprint).lower(),
            event_id=event_id,
            event_type=event_type,
            event_payload=normalized_payload,
        )
        validated["inserted"] = False
        return validated

    existing = await _fetch_events(
        conn,
        trial_id=trial_id,
        revision=revision,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    if existing:
        fingerprints = {str(row.get("entity_fingerprint") or "") for row in existing}
        spec_versions = {str(row.get("entity_spec_version") or "") for row in existing}
        if fingerprints != {str(entity_fingerprint).lower()} or spec_versions != {entity_spec_version}:
            raise RuntimeError("lifecycle entity identity changed after first event")
    _validate_monotonic(existing, entity_type, event_type)
    if event_type == DECISION_EVENT and normalized_payload.get("decision") == "PROMOTE":
        missing = _promotion_missing(existing, entity_type)
        if missing:
            raise RuntimeError("PROMOTE decision missing lifecycle prerequisites: " + ",".join(missing))

    result = await conn.execute(
        """
        INSERT INTO research_lifecycle_events (
            trial_id,revision,research_family,trial_fingerprint,
            entity_type,entity_id,entity_spec_version,entity_fingerprint,
            event_id,event_type,event_fingerprint,event_payload_fingerprint,
            event_payload,source_commit_sha
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14)
        ON CONFLICT DO NOTHING
        """,
        trial_id,
        revision,
        research_family,
        trial_fp,
        entity_type,
        entity_id,
        entity_spec_version,
        str(entity_fingerprint).lower(),
        event_id,
        event_type,
        event_fp,
        payload_fp,
        json.dumps(normalized_payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
        source_commit_sha,
    )
    row = await conn.fetchrow(
        """
        SELECT trial_id,revision,research_family,trial_fingerprint,
               entity_type,entity_id,entity_spec_version,entity_fingerprint,
               event_id,event_type,event_fingerprint,event_payload_fingerprint,
               event_payload,source_commit_sha,recorded_at
        FROM research_lifecycle_events
        WHERE trial_id=$1 AND revision=$2 AND entity_type=$3 AND entity_id=$4 AND event_id=$5
        """,
        trial_id,
        revision,
        entity_type,
        entity_id,
        event_id,
    )
    if row is None:
        raise RuntimeError("lifecycle event insertion conflicted with immutable ledger")
    validated = _validate_stored_event(
        row,
        trial_id=trial_id,
        revision=revision,
        research_family=research_family,
        trial_fp=trial_fp,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_spec_version=entity_spec_version,
        entity_fingerprint=str(entity_fingerprint).lower(),
        event_id=event_id,
        event_type=event_type,
        event_payload=normalized_payload,
    )
    validated["inserted"] = str(result).endswith("1")
    return validated


async def record_trial_event(
    conn: Any,
    study: str,
    *,
    event_id: str,
    event_type: str,
    event_payload: Mapping[str, Any],
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    _, trial_fp, trial_id, revision, _ = _identity(study)
    return await _record_event(
        conn,
        study,
        entity_type=ENTITY_TRIAL,
        entity_id=trial_id,
        entity_spec_version=str(revision),
        entity_fingerprint=trial_fp,
        event_id=event_id,
        event_type=event_type,
        event_payload=event_payload,
        source_commit_sha=source_commit_sha,
    )


async def record_feature_event(
    conn: Any,
    study: str,
    *,
    feature_id: str,
    feature_spec_version: str,
    feature_card_fingerprint: str,
    event_id: str,
    event_type: str,
    event_payload: Mapping[str, Any],
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    return await _record_event(
        conn,
        study,
        entity_type=ENTITY_FEATURE,
        entity_id=feature_id,
        entity_spec_version=feature_spec_version,
        entity_fingerprint=feature_card_fingerprint,
        event_id=event_id,
        event_type=event_type,
        event_payload=event_payload,
        source_commit_sha=source_commit_sha,
    )


async def lifecycle_status(
    conn: Any,
    study: str,
    *,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    _, trial_fp, trial_id, revision, research_family = _identity(study)
    if entity_type not in EVENT_ORDER:
        raise ValueError("entity_type must be TRIAL or FEATURE")
    await conn.execute(LIFECYCLE_LEDGER_SCHEMA_SQL)
    rows = await _fetch_events(
        conn,
        trial_id=trial_id,
        revision=revision,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    event_types = [str(row.get("event_type")) for row in rows]
    current_event_type = None
    if rows:
        current_event_type = max(event_types, key=lambda value: _stage_rank(entity_type, value))
    decision = None
    for row in rows:
        if row.get("event_type") == DECISION_EVENT:
            payload = _json_object(row.get("event_payload"), "event_payload")
            decision = payload.get("decision")
    return {
        "ledger_version": LEDGER_VERSION,
        "trial_id": trial_id,
        "revision": revision,
        "research_family": research_family,
        "trial_fingerprint": trial_fp,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "event_count": len(rows),
        "event_types": event_types,
        "current_event_type": current_event_type,
        "terminal_decision": decision,
        "missing_promotion_prerequisites": _promotion_missing(rows, entity_type),
        "research_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "execution_authorized": False,
    }
