"""Shared append-only audit history for research snapshots.

Several research collectors intentionally maintain a mutable hourly/daily
materialization for simple status reads. This module preserves every raw capture
*before* that materialization may be replaced. It prevents destructive history
loss but does not, by itself, prove provider availability timing or point-in-time
correctness.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timezone
from typing import Any

FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,79}$")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS research_snapshot_history (
    research_family TEXT NOT NULL,
    spec_version TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    capture_bucket TIMESTAMPTZ,
    source_commit_sha TEXT,
    payload_fingerprint TEXT NOT NULL,
    payload JSONB NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (research_family, spec_version, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_research_snapshot_history_family_time
ON research_snapshot_history(research_family, spec_version, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_snapshot_history_bucket
ON research_snapshot_history(research_family, spec_version, capture_bucket, captured_at DESC);
"""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("research snapshot timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def normalize_bucket(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    raise TypeError("capture bucket must be datetime, date, or None")


def canonical_payload(snapshot: dict[str, Any]) -> str:
    if not isinstance(snapshot, dict):
        raise TypeError("research snapshot payload must be an object")
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def payload_fingerprint(snapshot: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(snapshot).encode("utf-8")).hexdigest()


def validate_history_identity(research_family: str, spec_version: str) -> tuple[str, str]:
    family = str(research_family or "").strip().lower()
    version = str(spec_version or "").strip()
    if not FAMILY_RE.fullmatch(family):
        raise ValueError("invalid research family")
    if not version or len(version) > 160:
        raise ValueError("invalid research spec version")
    return family, version


def validate_stored_history_row(
    row: Any,
    *,
    expected_fingerprint: str,
) -> None:
    if row is None:
        raise RuntimeError("immutable research history row is missing after insert")
    stored = str(dict(row).get("payload_fingerprint") or "")
    if stored != expected_fingerprint:
        raise RuntimeError("immutable research history conflict: payload_fingerprint")


async def append_snapshot_history(
    connection: Any,
    *,
    research_family: str,
    spec_version: str,
    captured_at: datetime,
    capture_bucket: datetime | date | None,
    source_commit_sha: str | None,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Append one immutable raw capture and fail closed on identity conflict.

    Exact retry of the same family/spec/captured_at/payload is idempotent.
    Reusing the same identity with different payload content is rejected.
    """
    family, version = validate_history_identity(research_family, spec_version)
    captured = _utc(captured_at)
    bucket = normalize_bucket(capture_bucket)
    canonical = canonical_payload(snapshot)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    await connection.execute(SCHEMA_SQL)
    insert_result = await connection.execute(
        """
        INSERT INTO research_snapshot_history (
            research_family,spec_version,captured_at,capture_bucket,
            source_commit_sha,payload_fingerprint,payload
        ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)
        ON CONFLICT (research_family,spec_version,captured_at) DO NOTHING
        """,
        family,
        version,
        captured,
        bucket,
        source_commit_sha,
        fingerprint,
        canonical,
    )
    row = await connection.fetchrow(
        """
        SELECT payload_fingerprint
        FROM research_snapshot_history
        WHERE research_family=$1 AND spec_version=$2 AND captured_at=$3
        """,
        family,
        version,
        captured,
    )
    validate_stored_history_row(row, expected_fingerprint=fingerprint)
    total = await connection.fetchval(
        """
        SELECT COUNT(*)::int FROM research_snapshot_history
        WHERE research_family=$1 AND spec_version=$2
        """,
        family,
        version,
    )
    bucket_count = None
    if bucket is not None:
        bucket_count = await connection.fetchval(
            """
            SELECT COUNT(*)::int FROM research_snapshot_history
            WHERE research_family=$1 AND spec_version=$2 AND capture_bucket=$3
            """,
            family,
            version,
            bucket,
        )
    return {
        "immutable": True,
        "purpose": "append_only_raw_history",
        "point_in_time_verified": False,
        "research_family": family,
        "spec_version": version,
        "captured_at": captured.isoformat(),
        "capture_bucket": bucket.isoformat() if bucket is not None else None,
        "payload_fingerprint": fingerprint,
        "inserted": str(insert_result).endswith("1"),
        "history_count": int(total or 0),
        "bucket_history_count": int(bucket_count or 0) if bucket is not None else None,
    }
