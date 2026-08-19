"""Fail-closed immutable out-of-sample vault primitives.

The vault has two append-only concepts:
1. an immutable sealed OOS partition (manifest + payload fingerprints + payload);
2. a one-time exposure event proving that the partition was deliberately opened.

Normal callers cannot read a sealed payload through this library before an
exposure event exists. This is a research-governance boundary, not a claim that
database administrators are cryptographically unable to access storage.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from research.research_governance import trial_fingerprint, trial_manifest
from research.research_trial_registry import ensure_trial_registered

VAULT_VERSION = "immutable-oos-v1"
OPEN_AUTHORIZATION_VERSION = "oos-open-authorization-v1"

OOS_VAULT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_oos_vault (
    trial_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    partition_id TEXT NOT NULL,
    research_family TEXT NOT NULL,
    trial_fingerprint TEXT NOT NULL,
    partition_manifest_fingerprint TEXT NOT NULL,
    partition_manifest JSONB NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    source_commit_sha TEXT,
    sealed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trial_id, revision, partition_id)
);
CREATE INDEX IF NOT EXISTS idx_research_oos_vault_trial
ON research_oos_vault(trial_id, revision, sealed_at DESC);

CREATE TABLE IF NOT EXISTS research_oos_exposure_events (
    trial_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    partition_id TEXT NOT NULL,
    authorization_fingerprint TEXT NOT NULL,
    authorization JSONB NOT NULL,
    source_commit_sha TEXT,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trial_id, revision, partition_id),
    FOREIGN KEY (trial_id, revision, partition_id)
        REFERENCES research_oos_vault(trial_id, revision, partition_id)
);
"""


def spec() -> dict[str, Any]:
    return {
        "vault_version": VAULT_VERSION,
        "open_authorization_version": OPEN_AUTHORIZATION_VERSION,
        "research_only": True,
        "append_only_partition": True,
        "append_only_exposure_event": True,
        "sealed_payload_read_before_exposure": False,
        "reseal_with_different_content": False,
        "reopen_with_different_authorization": False,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
        "requires_durable_trial_registration": True,
        "database_admin_cryptographic_isolation": False,
        "forbidden": [
            "reading_oos_payload_before_explicit_exposure",
            "changing_sealed_partition_manifest",
            "changing_sealed_oos_payload",
            "using_oos_for_threshold_search",
            "using_oos_for_parameter_tuning",
            "automatic_live_promotion",
        ],
    }


def canonical_fingerprint(value: Any) -> str:
    """Fingerprint JSON-compatible content deterministically."""
    try:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("vault content must be canonical JSON-compatible data") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_payload(payload: Any) -> dict[str, Any] | list[Any]:
    if not isinstance(payload, (dict, list)):
        raise ValueError("OOS payload must be a JSON object or array")
    canonical_fingerprint(payload)
    return payload


def _json_object(value: Any, field: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"stored {field} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"stored {field} is not an object")
    return value


def _json_value(value: Any, field: str) -> dict[str, Any] | list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"stored {field} is invalid JSON") from exc
    if not isinstance(value, (dict, list)):
        raise RuntimeError(f"stored {field} is not a JSON object or array")
    try:
        canonical_fingerprint(value)
    except ValueError as exc:
        raise RuntimeError(f"stored {field} is not canonical JSON-compatible data") from exc
    return value


def _partition_identity(study: str, partition_id: str) -> tuple[dict[str, Any], str, str, int, str]:
    manifest = trial_manifest(study)
    fingerprint = trial_fingerprint(study)
    trial_id = str(manifest["trial_id"])
    revision = int(manifest["revision"])
    research_family = str(manifest["research_family"])
    if not partition_id or not str(partition_id).strip():
        raise ValueError("partition_id is required")
    return manifest, fingerprint, trial_id, revision, research_family


def validate_partition_manifest(
    study: str,
    partition_id: str,
    partition_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(partition_manifest, Mapping):
        raise ValueError("partition_manifest must be an object")
    manifest, trial_fp, trial_id, revision, research_family = _partition_identity(
        study, partition_id
    )
    result = dict(partition_manifest)
    required_exact = {
        "vault_version": VAULT_VERSION,
        "trial_id": trial_id,
        "revision": revision,
        "research_family": research_family,
        "trial_fingerprint": trial_fp,
        "partition_id": partition_id,
        "purpose": "IMMUTABLE_OOS",
        "sealed_before_evaluation": True,
        "tuning_forbidden": True,
        "threshold_search_forbidden": True,
        "selection_forbidden_after_seal": True,
        "open_policy": "EXPLICIT_AUTHORIZATION_ONCE",
    }
    mismatches = [
        key for key, expected in required_exact.items()
        if result.get(key) != expected
    ]
    if mismatches:
        raise ValueError(
            "partition_manifest does not satisfy frozen OOS contract: "
            + ",".join(mismatches)
        )
    lineage = result.get("dataset_lineage_fingerprint")
    if not isinstance(lineage, str) or not lineage.strip():
        raise ValueError("partition_manifest requires dataset_lineage_fingerprint")
    partition_rule = result.get("partition_rule")
    if not isinstance(partition_rule, Mapping) or not partition_rule:
        raise ValueError("partition_manifest requires non-empty partition_rule")
    canonical_fingerprint(result)
    return result


def validate_open_authorization(
    study: str,
    partition_id: str,
    authorization: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(authorization, Mapping):
        raise ValueError("authorization must be an object")
    _, trial_fp, trial_id, revision, research_family = _partition_identity(
        study, partition_id
    )
    result = dict(authorization)
    required_exact = {
        "authorization_version": OPEN_AUTHORIZATION_VERSION,
        "trial_id": trial_id,
        "revision": revision,
        "research_family": research_family,
        "trial_fingerprint": trial_fp,
        "partition_id": partition_id,
        "development_frozen": True,
        "walk_forward_complete": True,
        "multiple_testing_plan_frozen": True,
        "data_quality_gate_passed": True,
        "point_in_time_verified": True,
        "lineage_verified": True,
        "thresholds_frozen_before_oos_open": True,
        "oos_tuning_forbidden": True,
    }
    mismatches = [
        key for key, expected in required_exact.items()
        if result.get(key) != expected
    ]
    if mismatches:
        raise ValueError(
            "authorization does not satisfy OOS open prerequisites: "
            + ",".join(mismatches)
        )
    authorized_by = result.get("authorized_by")
    reason = result.get("authorization_reason")
    if not isinstance(authorized_by, str) or not authorized_by.strip():
        raise ValueError("authorization requires authorized_by")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("authorization requires authorization_reason")
    canonical_fingerprint(result)
    return result


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def validate_vault_record(
    row: Any,
    *,
    expected_trial_id: str,
    expected_revision: int,
    expected_partition_id: str,
    expected_research_family: str,
    expected_trial_fingerprint: str,
    expected_manifest: dict[str, Any],
    expected_manifest_fingerprint: str,
    expected_payload: Any,
    expected_payload_fingerprint: str,
) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("immutable OOS vault row is missing")
    record = dict(row)
    stored_manifest = _json_object(record.get("partition_manifest"), "partition_manifest")
    stored_payload = _json_value(record.get("payload"), "payload")
    mismatches: list[str] = []
    checks = {
        "trial_id": (str(record.get("trial_id") or ""), expected_trial_id),
        "partition_id": (str(record.get("partition_id") or ""), expected_partition_id),
        "research_family": (
            str(record.get("research_family") or ""),
            expected_research_family,
        ),
        "trial_fingerprint": (
            str(record.get("trial_fingerprint") or ""),
            expected_trial_fingerprint,
        ),
        "partition_manifest_fingerprint": (
            str(record.get("partition_manifest_fingerprint") or ""),
            expected_manifest_fingerprint,
        ),
        "payload_fingerprint": (
            str(record.get("payload_fingerprint") or ""),
            expected_payload_fingerprint,
        ),
    }
    for field, (actual, expected) in checks.items():
        if actual != expected:
            mismatches.append(field)
    try:
        stored_revision = int(record.get("revision"))
    except (TypeError, ValueError):
        stored_revision = -1
    if stored_revision != expected_revision:
        mismatches.append("revision")
    if stored_manifest != expected_manifest:
        mismatches.append("partition_manifest")
    if stored_payload != expected_payload:
        mismatches.append("payload")
    if canonical_fingerprint(stored_manifest) != expected_manifest_fingerprint:
        mismatches.append("stored_manifest_fingerprint")
    if canonical_fingerprint(stored_payload) != expected_payload_fingerprint:
        mismatches.append("stored_payload_fingerprint")
    if mismatches:
        raise RuntimeError(
            "immutable OOS vault conflict: " + ",".join(sorted(set(mismatches)))
        )
    return {
        "vault_version": VAULT_VERSION,
        "trial_id": expected_trial_id,
        "revision": expected_revision,
        "partition_id": expected_partition_id,
        "research_family": expected_research_family,
        "trial_fingerprint": expected_trial_fingerprint,
        "partition_manifest_fingerprint": expected_manifest_fingerprint,
        "payload_fingerprint": expected_payload_fingerprint,
        "source_commit_sha": record.get("source_commit_sha"),
        "sealed_at": _iso(record.get("sealed_at")),
        "immutable": True,
        "payload_exposed": False,
    }


def validate_exposure_record(
    row: Any,
    *,
    expected_trial_id: str,
    expected_revision: int,
    expected_partition_id: str,
    expected_authorization: dict[str, Any],
    expected_authorization_fingerprint: str,
) -> dict[str, Any]:
    if row is None:
        raise RuntimeError("OOS exposure event is missing")
    record = dict(row)
    stored_authorization = _json_object(record.get("authorization"), "authorization")
    mismatches: list[str] = []
    if str(record.get("trial_id") or "") != expected_trial_id:
        mismatches.append("trial_id")
    try:
        stored_revision = int(record.get("revision"))
    except (TypeError, ValueError):
        stored_revision = -1
    if stored_revision != expected_revision:
        mismatches.append("revision")
    if str(record.get("partition_id") or "") != expected_partition_id:
        mismatches.append("partition_id")
    if str(record.get("authorization_fingerprint") or "") != expected_authorization_fingerprint:
        mismatches.append("authorization_fingerprint")
    if stored_authorization != expected_authorization:
        mismatches.append("authorization")
    if canonical_fingerprint(stored_authorization) != expected_authorization_fingerprint:
        mismatches.append("stored_authorization_fingerprint")
    if mismatches:
        raise RuntimeError(
            "OOS exposure event conflict: " + ",".join(sorted(set(mismatches)))
        )
    return {
        "authorization_version": OPEN_AUTHORIZATION_VERSION,
        "trial_id": expected_trial_id,
        "revision": expected_revision,
        "partition_id": expected_partition_id,
        "authorization_fingerprint": expected_authorization_fingerprint,
        "source_commit_sha": record.get("source_commit_sha"),
        "opened_at": _iso(record.get("opened_at")),
        "append_only": True,
    }


async def seal_oos_partition(
    conn: Any,
    study: str,
    *,
    partition_id: str,
    partition_manifest: Mapping[str, Any],
    payload: Any,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Seal once; exact retries are idempotent and conflicting retries fail closed."""
    _, trial_fp, trial_id, revision, research_family = _partition_identity(
        study, partition_id
    )
    normalized_manifest = validate_partition_manifest(
        study, partition_id, partition_manifest
    )
    manifest_fp = canonical_fingerprint(normalized_manifest)
    normalized_payload = validate_payload(payload)
    payload_fp = canonical_fingerprint(normalized_payload)
    registry = await ensure_trial_registered(
        conn, study, source_commit_sha=source_commit_sha
    )
    if registry.get("manifest_fingerprint") != trial_fp:
        raise RuntimeError("durable trial registry fingerprint mismatch before OOS seal")
    await conn.execute(OOS_VAULT_SCHEMA_SQL)
    result = await conn.execute(
        """
        INSERT INTO research_oos_vault (
            trial_id,revision,partition_id,research_family,trial_fingerprint,
            partition_manifest_fingerprint,partition_manifest,
            payload_fingerprint,payload,source_commit_sha
        ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10)
        ON CONFLICT DO NOTHING
        """,
        trial_id,
        revision,
        partition_id,
        research_family,
        trial_fp,
        manifest_fp,
        json.dumps(normalized_manifest, sort_keys=True, separators=(",", ":")),
        payload_fp,
        json.dumps(normalized_payload, sort_keys=True, separators=(",", ":"), allow_nan=False),
        source_commit_sha,
    )
    row = await conn.fetchrow(
        """
        SELECT trial_id,revision,partition_id,research_family,trial_fingerprint,
               partition_manifest_fingerprint,partition_manifest,
               payload_fingerprint,payload,source_commit_sha,sealed_at
        FROM research_oos_vault
        WHERE trial_id=$1 AND revision=$2 AND partition_id=$3
        """,
        trial_id,
        revision,
        partition_id,
    )
    validated = validate_vault_record(
        row,
        expected_trial_id=trial_id,
        expected_revision=revision,
        expected_partition_id=partition_id,
        expected_research_family=research_family,
        expected_trial_fingerprint=trial_fp,
        expected_manifest=normalized_manifest,
        expected_manifest_fingerprint=manifest_fp,
        expected_payload=normalized_payload,
        expected_payload_fingerprint=payload_fp,
    )
    validated["inserted"] = str(result).endswith("1")
    validated["trial_registry_registered"] = True
    return validated


async def authorize_oos_open(
    conn: Any,
    study: str,
    *,
    partition_id: str,
    authorization: Mapping[str, Any],
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Create the single append-only exposure event after explicit prerequisites."""
    _, trial_fp, trial_id, revision, research_family = _partition_identity(
        study, partition_id
    )
    normalized = validate_open_authorization(study, partition_id, authorization)
    authorization_fp = canonical_fingerprint(normalized)
    await conn.execute(OOS_VAULT_SCHEMA_SQL)
    vault = await conn.fetchrow(
        """
        SELECT trial_id,revision,partition_id,research_family,trial_fingerprint,
               partition_manifest_fingerprint,payload_fingerprint,source_commit_sha,sealed_at
        FROM research_oos_vault
        WHERE trial_id=$1 AND revision=$2 AND partition_id=$3
        """,
        trial_id,
        revision,
        partition_id,
    )
    if vault is None:
        raise RuntimeError("cannot open OOS partition before it is sealed")
    vault_dict = dict(vault)
    if str(vault_dict.get("research_family") or "") != research_family:
        raise RuntimeError("sealed OOS partition research_family mismatch")
    if str(vault_dict.get("trial_fingerprint") or "") != trial_fp:
        raise RuntimeError("sealed OOS partition trial fingerprint mismatch")

    result = await conn.execute(
        """
        INSERT INTO research_oos_exposure_events (
            trial_id,revision,partition_id,authorization_fingerprint,
            authorization,source_commit_sha
        ) VALUES ($1,$2,$3,$4,$5::jsonb,$6)
        ON CONFLICT DO NOTHING
        """,
        trial_id,
        revision,
        partition_id,
        authorization_fp,
        json.dumps(normalized, sort_keys=True, separators=(",", ":")),
        source_commit_sha,
    )
    row = await conn.fetchrow(
        """
        SELECT trial_id,revision,partition_id,authorization_fingerprint,
               authorization,source_commit_sha,opened_at
        FROM research_oos_exposure_events
        WHERE trial_id=$1 AND revision=$2 AND partition_id=$3
        """,
        trial_id,
        revision,
        partition_id,
    )
    validated = validate_exposure_record(
        row,
        expected_trial_id=trial_id,
        expected_revision=revision,
        expected_partition_id=partition_id,
        expected_authorization=normalized,
        expected_authorization_fingerprint=authorization_fp,
    )
    validated["inserted"] = str(result).endswith("1")
    return validated


async def oos_partition_status(
    conn: Any,
    study: str,
    *,
    partition_id: str,
) -> dict[str, Any]:
    """Return fingerprints/exposure state without returning the sealed payload."""
    _, trial_fp, trial_id, revision, research_family = _partition_identity(
        study, partition_id
    )
    await conn.execute(OOS_VAULT_SCHEMA_SQL)
    vault = await conn.fetchrow(
        """
        SELECT trial_id,revision,partition_id,research_family,trial_fingerprint,
               partition_manifest_fingerprint,payload_fingerprint,
               source_commit_sha,sealed_at
        FROM research_oos_vault
        WHERE trial_id=$1 AND revision=$2 AND partition_id=$3
        """,
        trial_id,
        revision,
        partition_id,
    )
    exposure = await conn.fetchrow(
        """
        SELECT authorization_fingerprint,source_commit_sha,opened_at
        FROM research_oos_exposure_events
        WHERE trial_id=$1 AND revision=$2 AND partition_id=$3
        """,
        trial_id,
        revision,
        partition_id,
    )
    if vault is None:
        return {
            "vault_version": VAULT_VERSION,
            "trial_id": trial_id,
            "revision": revision,
            "partition_id": partition_id,
            "research_family": research_family,
            "trial_fingerprint": trial_fp,
            "sealed": False,
            "exposed": False,
            "payload_returned": False,
            "research_only": True,
        }
    row = dict(vault)
    if str(row.get("trial_fingerprint") or "") != trial_fp:
        raise RuntimeError("sealed OOS partition trial fingerprint mismatch")
    return {
        "vault_version": VAULT_VERSION,
        "trial_id": trial_id,
        "revision": revision,
        "partition_id": partition_id,
        "research_family": research_family,
        "trial_fingerprint": trial_fp,
        "partition_manifest_fingerprint": row.get("partition_manifest_fingerprint"),
        "payload_fingerprint": row.get("payload_fingerprint"),
        "source_commit_sha": row.get("source_commit_sha"),
        "sealed_at": _iso(row.get("sealed_at")),
        "sealed": True,
        "exposed": exposure is not None,
        "authorization_fingerprint": (
            dict(exposure).get("authorization_fingerprint")
            if exposure is not None
            else None
        ),
        "opened_at": (
            _iso(dict(exposure).get("opened_at"))
            if exposure is not None
            else None
        ),
        "payload_returned": False,
        "research_only": True,
    }


async def read_exposed_oos_partition(
    conn: Any,
    study: str,
    *,
    partition_id: str,
) -> dict[str, Any]:
    """Return the immutable payload only after a valid exposure event exists."""
    _, trial_fp, trial_id, revision, research_family = _partition_identity(
        study, partition_id
    )
    await conn.execute(OOS_VAULT_SCHEMA_SQL)
    exposure = await conn.fetchrow(
        """
        SELECT trial_id,revision,partition_id,authorization_fingerprint,
               authorization,source_commit_sha,opened_at
        FROM research_oos_exposure_events
        WHERE trial_id=$1 AND revision=$2 AND partition_id=$3
        """,
        trial_id,
        revision,
        partition_id,
    )
    if exposure is None:
        raise RuntimeError("sealed OOS partition is not exposed")
    vault = await conn.fetchrow(
        """
        SELECT trial_id,revision,partition_id,research_family,trial_fingerprint,
               partition_manifest_fingerprint,partition_manifest,
               payload_fingerprint,payload,source_commit_sha,sealed_at
        FROM research_oos_vault
        WHERE trial_id=$1 AND revision=$2 AND partition_id=$3
        """,
        trial_id,
        revision,
        partition_id,
    )
    if vault is None:
        raise RuntimeError("OOS exposure exists without sealed vault row")
    row = dict(vault)
    if str(row.get("research_family") or "") != research_family:
        raise RuntimeError("sealed OOS partition research_family mismatch")
    if str(row.get("trial_fingerprint") or "") != trial_fp:
        raise RuntimeError("sealed OOS partition trial fingerprint mismatch")
    manifest = _json_object(row.get("partition_manifest"), "partition_manifest")
    payload = _json_value(row.get("payload"), "payload")
    manifest_fp = str(row.get("partition_manifest_fingerprint") or "")
    payload_fp = str(row.get("payload_fingerprint") or "")
    if canonical_fingerprint(manifest) != manifest_fp:
        raise RuntimeError("sealed OOS partition manifest fingerprint mismatch")
    if canonical_fingerprint(payload) != payload_fp:
        raise RuntimeError("sealed OOS payload fingerprint mismatch")
    return {
        "vault_version": VAULT_VERSION,
        "trial_id": trial_id,
        "revision": revision,
        "partition_id": partition_id,
        "research_family": research_family,
        "trial_fingerprint": trial_fp,
        "partition_manifest_fingerprint": manifest_fp,
        "payload_fingerprint": payload_fp,
        "sealed_at": _iso(row.get("sealed_at")),
        "opened_at": _iso(dict(exposure).get("opened_at")),
        "payload": payload,
        "payload_returned": True,
        "research_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
    }
