"""Durable append-only registry for frozen research trial specifications.

Research only. A trial_id/revision pair is registered once with the exact
preregistered manifest fingerprint. Later captures may reference the same row,
but conflicting manifest content or fingerprints fail closed. No strategy,
scoring, promotion, order, position, or execution state is mutated here.
"""
from __future__ import annotations

import json
from typing import Any

from research.research_governance import manifest_fingerprint, trial_manifest

TRIAL_REGISTRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_trial_registry (
    trial_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    research_family TEXT NOT NULL,
    manifest_fingerprint TEXT NOT NULL UNIQUE,
    manifest JSONB NOT NULL,
    source_commit_sha TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (trial_id, revision)
);
"""


def _manifest_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("stored trial manifest is not an object")
    return value


def validate_registry_record(
    row: Any,
    *,
    expected_manifest: dict[str, Any],
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Validate a durable row against the frozen in-code registration."""
    if row is None:
        raise RuntimeError("durable trial registry row is missing")
    record = dict(row)
    stored_manifest = _manifest_object(record.get("manifest"))
    expected_trial_id = str(expected_manifest.get("trial_id") or "")
    expected_revision = int(expected_manifest.get("revision") or 0)
    expected_family = str(expected_manifest.get("research_family") or "")

    mismatches: list[str] = []
    if str(record.get("trial_id") or "") != expected_trial_id:
        mismatches.append("trial_id")
    try:
        stored_revision = int(record.get("revision"))
    except (TypeError, ValueError):
        stored_revision = -1
    if stored_revision != expected_revision:
        mismatches.append("revision")
    if str(record.get("research_family") or "") != expected_family:
        mismatches.append("research_family")
    if str(record.get("manifest_fingerprint") or "") != expected_fingerprint:
        mismatches.append("manifest_fingerprint")
    if stored_manifest != expected_manifest:
        mismatches.append("manifest")
    if mismatches:
        raise RuntimeError("durable trial registry conflict: " + ",".join(mismatches))

    registered_at = record.get("registered_at")
    if hasattr(registered_at, "isoformat"):
        registered_at = registered_at.isoformat()
    return {
        "trial_id": expected_trial_id,
        "revision": expected_revision,
        "research_family": expected_family,
        "manifest_fingerprint": expected_fingerprint,
        "manifest": stored_manifest,
        "source_commit_sha": record.get("source_commit_sha"),
        "registered_at": registered_at,
        "immutable": True,
    }


async def ensure_trial_registered(
    conn: Any,
    study: str,
    *,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Insert once, then prove the durable row still equals the frozen manifest."""
    manifest = trial_manifest(study)
    fingerprint = manifest_fingerprint(manifest)
    trial_id = str(manifest["trial_id"])
    revision = int(manifest["revision"])
    research_family = str(manifest["research_family"])

    await conn.execute(TRIAL_REGISTRY_SCHEMA_SQL)
    result = await conn.execute(
        """
        INSERT INTO research_trial_registry (
            trial_id, revision, research_family, manifest_fingerprint,
            manifest, source_commit_sha
        ) VALUES ($1,$2,$3,$4,$5::jsonb,$6)
        ON CONFLICT DO NOTHING
        """,
        trial_id,
        revision,
        research_family,
        fingerprint,
        json.dumps(manifest, sort_keys=True, separators=(",", ":")),
        source_commit_sha,
    )
    row = await conn.fetchrow(
        """
        SELECT trial_id, revision, research_family, manifest_fingerprint,
               manifest, source_commit_sha, registered_at
        FROM research_trial_registry
        WHERE trial_id = $1 AND revision = $2
        """,
        trial_id,
        revision,
    )
    validated = validate_registry_record(
        row,
        expected_manifest=manifest,
        expected_fingerprint=fingerprint,
    )
    validated["inserted"] = str(result).endswith("1")
    return validated


async def trial_registry_status(conn: Any, study: str) -> dict[str, Any]:
    """Return registry state without inserting or changing a trial."""
    manifest = trial_manifest(study)
    fingerprint = manifest_fingerprint(manifest)
    await conn.execute(TRIAL_REGISTRY_SCHEMA_SQL)
    row = await conn.fetchrow(
        """
        SELECT trial_id, revision, research_family, manifest_fingerprint,
               manifest, source_commit_sha, registered_at
        FROM research_trial_registry
        WHERE trial_id = $1 AND revision = $2
        """,
        str(manifest["trial_id"]),
        int(manifest["revision"]),
    )
    if row is None:
        return {
            "trial_id": manifest["trial_id"],
            "revision": manifest["revision"],
            "research_family": manifest["research_family"],
            "manifest_fingerprint": fingerprint,
            "registered": False,
            "immutable": True,
        }
    result = validate_registry_record(
        row,
        expected_manifest=manifest,
        expected_fingerprint=fingerprint,
    )
    result["registered"] = True
    return result
