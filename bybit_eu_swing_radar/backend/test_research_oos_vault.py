from datetime import datetime, timezone

import pytest

import research.research_oos_vault as vault
from research.research_governance import trial_fingerprint, trial_manifest


STUDY = "swing-liquidity-validation-v1"
PARTITION = "validation-oos-v1"


def partition_manifest():
    trial = trial_manifest(STUDY)
    return {
        "vault_version": vault.VAULT_VERSION,
        "trial_id": trial["trial_id"],
        "revision": trial["revision"],
        "research_family": trial["research_family"],
        "trial_fingerprint": trial_fingerprint(STUDY),
        "partition_id": PARTITION,
        "purpose": "IMMUTABLE_OOS",
        "dataset_lineage_fingerprint": "lineage-abc",
        "partition_rule": {
            "type": "prospective_tail_after_development",
            "development_target_matured_events": 60,
            "validation_target_matured_events": 40,
        },
        "sealed_before_evaluation": True,
        "tuning_forbidden": True,
        "threshold_search_forbidden": True,
        "selection_forbidden_after_seal": True,
        "open_policy": "EXPLICIT_AUTHORIZATION_ONCE",
    }


def open_authorization(**overrides):
    trial = trial_manifest(STUDY)
    result = {
        "authorization_version": vault.OPEN_AUTHORIZATION_VERSION,
        "trial_id": trial["trial_id"],
        "revision": trial["revision"],
        "research_family": trial["research_family"],
        "trial_fingerprint": trial_fingerprint(STUDY),
        "partition_id": PARTITION,
        "development_frozen": True,
        "walk_forward_complete": True,
        "multiple_testing_plan_frozen": True,
        "data_quality_gate_passed": True,
        "point_in_time_verified": True,
        "lineage_verified": True,
        "thresholds_frozen_before_oos_open": True,
        "oos_tuning_forbidden": True,
        "authorized_by": "research-governance-test",
        "authorization_reason": "pre-registered validation gate reached",
    }
    result.update(overrides)
    return result


class FakeConn:
    def __init__(self):
        self.vault_row = None
        self.exposure_row = None

    async def execute(self, sql, *args):
        if "INSERT INTO research_oos_vault" in sql:
            if self.vault_row is not None:
                return "INSERT 0 0"
            (
                trial_id,
                revision,
                partition_id,
                family,
                trial_fp,
                manifest_fp,
                manifest_json,
                payload_fp,
                payload_json,
                source_sha,
            ) = args
            import json
            self.vault_row = {
                "trial_id": trial_id,
                "revision": revision,
                "partition_id": partition_id,
                "research_family": family,
                "trial_fingerprint": trial_fp,
                "partition_manifest_fingerprint": manifest_fp,
                "partition_manifest": json.loads(manifest_json),
                "payload_fingerprint": payload_fp,
                "payload": json.loads(payload_json),
                "source_commit_sha": source_sha,
                "sealed_at": datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc),
            }
            return "INSERT 0 1"
        if "INSERT INTO research_oos_exposure_events" in sql:
            if self.exposure_row is not None:
                return "INSERT 0 0"
            trial_id, revision, partition_id, auth_fp, auth_json, source_sha = args
            import json
            self.exposure_row = {
                "trial_id": trial_id,
                "revision": revision,
                "partition_id": partition_id,
                "authorization_fingerprint": auth_fp,
                "authorization": json.loads(auth_json),
                "source_commit_sha": source_sha,
                "opened_at": datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc),
            }
            return "INSERT 0 1"
        return "CREATE TABLE"

    async def fetchrow(self, sql, *args):
        if "FROM research_oos_exposure_events" in sql:
            return self.exposure_row
        if "FROM research_oos_vault" in sql:
            return self.vault_row
        raise AssertionError(sql)


@pytest.fixture
def registered(monkeypatch):
    async def fake_registered(conn, study, *, source_commit_sha=None):
        return {"manifest_fingerprint": trial_fingerprint(study), "immutable": True}
    monkeypatch.setattr(vault, "ensure_trial_registered", fake_registered)


def test_spec_is_fail_closed_research_only():
    payload = vault.spec()
    assert payload["research_only"] is True
    assert payload["append_only_partition"] is True
    assert payload["append_only_exposure_event"] is True
    assert payload["sealed_payload_read_before_exposure"] is False
    assert payload["promotion_allowed"] is False
    assert payload["live_strategy_mutated"] is False
    assert payload["production_eligibility_mutated"] is False
    assert payload["database_admin_cryptographic_isolation"] is False


def test_canonical_fingerprint_is_deterministic_and_strict():
    assert vault.canonical_fingerprint({"b": 2, "a": 1}) == vault.canonical_fingerprint(
        {"a": 1, "b": 2}
    )
    with pytest.raises(ValueError, match="canonical JSON-compatible"):
        vault.canonical_fingerprint({"x": float("nan")})


def test_partition_manifest_rejects_tuning_or_missing_lineage():
    bad = partition_manifest()
    bad["tuning_forbidden"] = False
    with pytest.raises(ValueError, match="tuning_forbidden"):
        vault.validate_partition_manifest(STUDY, PARTITION, bad)
    bad = partition_manifest()
    bad["dataset_lineage_fingerprint"] = ""
    with pytest.raises(ValueError, match="dataset_lineage_fingerprint"):
        vault.validate_partition_manifest(STUDY, PARTITION, bad)


def test_open_authorization_fails_closed_on_unmet_prerequisite():
    bad = open_authorization(point_in_time_verified=False)
    with pytest.raises(ValueError, match="point_in_time_verified"):
        vault.validate_open_authorization(STUDY, PARTITION, bad)


@pytest.mark.asyncio
async def test_seal_is_idempotent_and_conflicting_reseal_fails(registered):
    conn = FakeConn()
    payload = {"rows": [{"event_id": "A", "net_r": 1.2}]}
    first = await vault.seal_oos_partition(
        conn,
        STUDY,
        partition_id=PARTITION,
        partition_manifest=partition_manifest(),
        payload=payload,
        source_commit_sha="sha-1",
    )
    assert first["inserted"] is True
    assert first["payload_exposed"] is False
    assert "payload" not in first
    second = await vault.seal_oos_partition(
        conn,
        STUDY,
        partition_id=PARTITION,
        partition_manifest=partition_manifest(),
        payload=payload,
        source_commit_sha="sha-2",
    )
    assert second["inserted"] is False
    with pytest.raises(RuntimeError, match="immutable OOS vault conflict"):
        await vault.seal_oos_partition(
            conn,
            STUDY,
            partition_id=PARTITION,
            partition_manifest=partition_manifest(),
            payload={"rows": [{"event_id": "A", "net_r": -9.0}]},
            source_commit_sha="sha-3",
        )


@pytest.mark.asyncio
async def test_payload_cannot_be_read_before_exposure(registered):
    conn = FakeConn()
    await vault.seal_oos_partition(
        conn,
        STUDY,
        partition_id=PARTITION,
        partition_manifest=partition_manifest(),
        payload={"secret_oos": [1, 2, 3]},
    )
    status = await vault.oos_partition_status(conn, STUDY, partition_id=PARTITION)
    assert status["sealed"] is True
    assert status["exposed"] is False
    assert status["payload_returned"] is False
    assert "payload" not in status
    with pytest.raises(RuntimeError, match="not exposed"):
        await vault.read_exposed_oos_partition(conn, STUDY, partition_id=PARTITION)


@pytest.mark.asyncio
async def test_open_is_one_time_and_read_verifies_fingerprints(registered):
    conn = FakeConn()
    payload = {"secret_oos": [{"signal": 1}, {"signal": 2}]}
    sealed = await vault.seal_oos_partition(
        conn,
        STUDY,
        partition_id=PARTITION,
        partition_manifest=partition_manifest(),
        payload=payload,
        source_commit_sha="seal-sha",
    )
    auth = open_authorization()
    opened = await vault.authorize_oos_open(
        conn,
        STUDY,
        partition_id=PARTITION,
        authorization=auth,
        source_commit_sha="open-sha",
    )
    assert opened["inserted"] is True
    retry = await vault.authorize_oos_open(
        conn,
        STUDY,
        partition_id=PARTITION,
        authorization=auth,
        source_commit_sha="retry-sha",
    )
    assert retry["inserted"] is False

    status = await vault.oos_partition_status(conn, STUDY, partition_id=PARTITION)
    assert status["exposed"] is True
    assert status["payload_returned"] is False

    read = await vault.read_exposed_oos_partition(conn, STUDY, partition_id=PARTITION)
    assert read["payload"] == payload
    assert read["payload_fingerprint"] == sealed["payload_fingerprint"]
    assert read["payload_returned"] is True
    assert read["promotion_allowed"] is False

    changed = open_authorization(authorization_reason="different retroactive reason")
    with pytest.raises(RuntimeError, match="exposure event conflict"):
        await vault.authorize_oos_open(
            conn,
            STUDY,
            partition_id=PARTITION,
            authorization=changed,
        )


@pytest.mark.asyncio
async def test_tampered_payload_is_detected_after_exposure(registered):
    conn = FakeConn()
    await vault.seal_oos_partition(
        conn,
        STUDY,
        partition_id=PARTITION,
        partition_manifest=partition_manifest(),
        payload={"secret_oos": [1, 2, 3]},
    )
    await vault.authorize_oos_open(
        conn,
        STUDY,
        partition_id=PARTITION,
        authorization=open_authorization(),
    )
    conn.vault_row["payload"] = {"secret_oos": [999]}
    with pytest.raises(RuntimeError, match="payload fingerprint mismatch"):
        await vault.read_exposed_oos_partition(conn, STUDY, partition_id=PARTITION)
