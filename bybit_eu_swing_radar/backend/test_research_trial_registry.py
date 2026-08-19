from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from research.research_governance import manifest_fingerprint, trial_manifest
from research.research_trial_registry import (
    ensure_trial_registered,
    trial_registry_status,
    validate_registry_record,
)

STUDY = "swing-liquidity-validation-v1"


def _row(*, manifest=None, fingerprint=None, source_commit_sha="abc123"):
    expected = trial_manifest(STUDY)
    return {
        "trial_id": expected["trial_id"],
        "revision": expected["revision"],
        "research_family": expected["research_family"],
        "manifest_fingerprint": fingerprint or manifest_fingerprint(expected),
        "manifest": expected if manifest is None else manifest,
        "source_commit_sha": source_commit_sha,
        "registered_at": datetime(2026, 8, 19, 7, 30, tzinfo=timezone.utc),
    }


def test_validate_registry_record_accepts_exact_frozen_manifest():
    expected = trial_manifest(STUDY)
    result = validate_registry_record(
        _row(),
        expected_manifest=expected,
        expected_fingerprint=manifest_fingerprint(expected),
    )
    assert result["immutable"] is True
    assert result["trial_id"] == STUDY
    assert result["revision"] == 1
    assert result["source_commit_sha"] == "abc123"


def test_validate_registry_record_rejects_manifest_drift():
    expected = trial_manifest(STUDY)
    drifted = dict(expected)
    drifted["development_target_matured_events"] = 40
    with pytest.raises(RuntimeError, match="durable trial registry conflict: manifest"):
        validate_registry_record(
            _row(manifest=drifted),
            expected_manifest=expected,
            expected_fingerprint=manifest_fingerprint(expected),
        )


def test_validate_registry_record_rejects_fingerprint_drift():
    expected = trial_manifest(STUDY)
    with pytest.raises(RuntimeError, match="manifest_fingerprint"):
        validate_registry_record(
            _row(fingerprint="0" * 64),
            expected_manifest=expected,
            expected_fingerprint=manifest_fingerprint(expected),
        )


class FakeConn:
    def __init__(self):
        self.row = None
        self.insert_calls = 0

    async def execute(self, sql, *args):
        if "INSERT INTO research_trial_registry" not in sql:
            return "CREATE TABLE"
        self.insert_calls += 1
        if self.row is None:
            trial_id, revision, family, fingerprint, manifest_json, source_sha = args
            self.row = {
                "trial_id": trial_id,
                "revision": revision,
                "research_family": family,
                "manifest_fingerprint": fingerprint,
                "manifest": json.loads(manifest_json),
                "source_commit_sha": source_sha,
                "registered_at": datetime(2026, 8, 19, 7, 31, tzinfo=timezone.utc),
            }
            return "INSERT 0 1"
        return "INSERT 0 0"

    async def fetchrow(self, sql, *args):
        return self.row


@pytest.mark.asyncio
async def test_ensure_trial_registered_is_insert_once_and_keeps_first_source_sha():
    conn = FakeConn()
    first = await ensure_trial_registered(conn, STUDY, source_commit_sha="sha-first")
    second = await ensure_trial_registered(conn, STUDY, source_commit_sha="sha-later")
    assert first["inserted"] is True
    assert second["inserted"] is False
    assert second["source_commit_sha"] == "sha-first"
    assert conn.insert_calls == 2


@pytest.mark.asyncio
async def test_trial_registry_status_does_not_insert():
    conn = FakeConn()
    status = await trial_registry_status(conn, STUDY)
    assert status["registered"] is False
    assert status["immutable"] is True
    assert conn.insert_calls == 0
