from datetime import datetime, timezone
import json
import pytest

from research import research_lifecycle_ledger as ledger
from research.research_governance import trial_fingerprint, trial_manifest

STUDY = "swing-liquidity-validation-v1"
FP = "a" * 64


def payload(event_type, **overrides):
    flags = {
        "TRIAL_REGISTERED": {"trial_registered": True},
        "HYPOTHESIS_RECORDED": {"preregistered": True},
        "FEATURE_CARD_RECORDED": {"feature_card_recorded": True, "feature_card_fingerprint": FP},
        "PIT_AUDIT_RECORDED": {"point_in_time_verified": True},
        "DATA_QUALITY_GATE_RECORDED": {"data_quality_gate_passed": True},
        "LINEAGE_RECORDED": {"lineage_verified": True},
        "DEVELOPMENT_EVIDENCE_RECORDED": {"development_complete": True},
        "WALK_FORWARD_EVIDENCE_RECORDED": {"walk_forward_complete": True},
        "MULTIPLE_TESTING_PLAN_RECORDED": {"multiple_testing_plan_frozen": True},
        "OOS_SEAL_RECORDED": {"oos_sealed": True},
        "OOS_OPEN_RECORDED": {"oos_opened": True, "oos_tuning_forbidden": True},
        "ROBUSTNESS_EVIDENCE_RECORDED": {"robustness_evaluated": True},
        "SHADOW_EVIDENCE_RECORDED": {"shadow_evaluated": True},
    }
    result = {"summary": event_type, "evidence_refs": [FP]}
    result.update(flags.get(event_type, {}))
    result.update(overrides)
    return result


class FakeConn:
    def __init__(self):
        self.rows = []

    async def execute(self, sql, *args):
        if "INSERT INTO research_lifecycle_events" in sql:
            (
                trial_id, revision, family, trial_fp, entity_type, entity_id,
                spec_version, entity_fp, event_id, event_type, event_fp,
                payload_fp, payload_json, source_sha,
            ) = args
            if any(
                row["trial_id"] == trial_id
                and row["revision"] == revision
                and row["entity_type"] == entity_type
                and row["entity_id"] == entity_id
                and row["event_id"] == event_id
                for row in self.rows
            ):
                return "INSERT 0 0"
            if event_type == "DECISION_RECORDED" and any(
                row["trial_id"] == trial_id
                and row["revision"] == revision
                and row["entity_type"] == entity_type
                and row["entity_id"] == entity_id
                and row["event_type"] == "DECISION_RECORDED"
                for row in self.rows
            ):
                return "INSERT 0 0"
            self.rows.append({
                "trial_id": trial_id,
                "revision": revision,
                "research_family": family,
                "trial_fingerprint": trial_fp,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "entity_spec_version": spec_version,
                "entity_fingerprint": entity_fp,
                "event_id": event_id,
                "event_type": event_type,
                "event_fingerprint": event_fp,
                "event_payload_fingerprint": payload_fp,
                "event_payload": json.loads(payload_json),
                "source_commit_sha": source_sha,
                "recorded_at": datetime(2026, 8, 19, 10, len(self.rows), tzinfo=timezone.utc),
            })
            return "INSERT 0 1"
        return "CREATE TABLE"

    async def fetchrow(self, sql, *args):
        if "FROM research_lifecycle_events" not in sql:
            raise AssertionError(sql)
        trial_id, revision, entity_type, entity_id, event_id = args
        return next((
            row for row in self.rows
            if row["trial_id"] == trial_id
            and row["revision"] == revision
            and row["entity_type"] == entity_type
            and row["entity_id"] == entity_id
            and row["event_id"] == event_id
        ), None)

    async def fetch(self, sql, *args):
        trial_id, revision, entity_type, entity_id = args
        return sorted([
            row for row in self.rows
            if row["trial_id"] == trial_id
            and row["revision"] == revision
            and row["entity_type"] == entity_type
            and row["entity_id"] == entity_id
        ], key=lambda row: (row["recorded_at"], row["event_id"]))


@pytest.fixture(autouse=True)
def registered(monkeypatch):
    async def fake_registered(conn, study, *, source_commit_sha=None):
        return {"manifest_fingerprint": trial_fingerprint(study), "immutable": True}
    monkeypatch.setattr(ledger, "ensure_trial_registered", fake_registered)


def test_spec_has_db_guards_and_no_live_authority():
    spec = ledger.spec()
    assert spec["append_only"] is True
    assert spec["database_role_mutation_guards"] == ["UPDATE", "DELETE", "TRUNCATE"]
    assert spec["direct_predecessor_required"] is True
    assert spec["promotion_decision_executes_live_change"] is False
    assert spec["execution_authorized"] is False
    assert "BEFORE UPDATE OR DELETE" in ledger.LIFECYCLE_LEDGER_SCHEMA_SQL
    assert "BEFORE TRUNCATE" in ledger.LIFECYCLE_LEDGER_SCHEMA_SQL


def test_payload_rejects_raw_outcomes_and_bad_refs():
    with pytest.raises(ValueError, match="forbidden"):
        ledger.validate_event_payload(
            entity_type="TRIAL",
            event_type="PIT_AUDIT_RECORDED",
            payload=payload("PIT_AUDIT_RECORDED", net_r=1.2),
            entity_fingerprint=trial_fingerprint(STUDY),
        )
    bad = payload("PIT_AUDIT_RECORDED")
    bad["evidence_refs"] = ["not-sha"]
    with pytest.raises(ValueError, match="SHA-256"):
        ledger.validate_event_payload(
            entity_type="TRIAL",
            event_type="PIT_AUDIT_RECORDED",
            payload=bad,
            entity_fingerprint=trial_fingerprint(STUDY),
        )


def test_feature_card_event_binds_exact_fingerprint():
    bad = payload("FEATURE_CARD_RECORDED", feature_card_fingerprint="b" * 64)
    with pytest.raises(ValueError, match="exact feature_card_fingerprint"):
        ledger.validate_event_payload(
            entity_type="FEATURE",
            event_type="FEATURE_CARD_RECORDED",
            payload=bad,
            entity_fingerprint=FP,
        )


@pytest.mark.asyncio
async def test_exact_retry_idempotent_and_conflict_fails():
    conn = FakeConn()
    event_payload = payload("TRIAL_REGISTERED", evidence_refs=[trial_fingerprint(STUDY)])
    first = await ledger.record_trial_event(
        conn, STUDY, event_id="e1", event_type="TRIAL_REGISTERED", event_payload=event_payload
    )
    retry = await ledger.record_trial_event(
        conn, STUDY, event_id="e1", event_type="TRIAL_REGISTERED", event_payload=event_payload
    )
    assert first["inserted"] is True
    assert retry["inserted"] is False
    changed = dict(event_payload)
    changed["summary"] = "changed"
    with pytest.raises(RuntimeError, match="immutable lifecycle event conflict"):
        await ledger.record_trial_event(
            conn, STUDY, event_id="e1", event_type="TRIAL_REGISTERED", event_payload=changed
        )


@pytest.mark.asyncio
async def test_lifecycle_cannot_skip_required_predecessor():
    conn = FakeConn()
    await ledger.record_trial_event(
        conn, STUDY, event_id="a", event_type="TRIAL_REGISTERED",
        event_payload=payload("TRIAL_REGISTERED", evidence_refs=[trial_fingerprint(STUDY)]),
    )
    with pytest.raises(RuntimeError, match="required predecessor: PIT_AUDIT_RECORDED"):
        await ledger.record_trial_event(
            conn, STUDY, event_id="b", event_type="DATA_QUALITY_GATE_RECORDED",
            event_payload=payload("DATA_QUALITY_GATE_RECORDED"),
        )


@pytest.mark.asyncio
async def test_lifecycle_cannot_move_backward():
    conn = FakeConn()
    await ledger.record_trial_event(
        conn, STUDY, event_id="a", event_type="TRIAL_REGISTERED",
        event_payload=payload("TRIAL_REGISTERED", evidence_refs=[trial_fingerprint(STUDY)]),
    )
    await ledger.record_trial_event(
        conn, STUDY, event_id="b", event_type="PIT_AUDIT_RECORDED",
        event_payload=payload("PIT_AUDIT_RECORDED"),
    )
    await ledger.record_trial_event(
        conn, STUDY, event_id="c", event_type="DATA_QUALITY_GATE_RECORDED",
        event_payload=payload("DATA_QUALITY_GATE_RECORDED"),
    )
    with pytest.raises(RuntimeError, match="backward"):
        await ledger.record_trial_event(
            conn, STUDY, event_id="d", event_type="PIT_AUDIT_RECORDED",
            event_payload=payload("PIT_AUDIT_RECORDED"),
        )


@pytest.mark.asyncio
async def test_promote_fails_until_full_chain_exists():
    conn = FakeConn()
    await ledger.record_trial_event(
        conn, STUDY, event_id="a", event_type="TRIAL_REGISTERED",
        event_payload=payload("TRIAL_REGISTERED", evidence_refs=[trial_fingerprint(STUDY)]),
    )
    decision = {
        "summary": "promote",
        "decision": "PROMOTE",
        "authorized_by": "test",
        "reason": "done",
        "live_mutation_authorized": False,
        "promotion_prerequisites_frozen": True,
    }
    with pytest.raises(RuntimeError, match="missing lifecycle prerequisites"):
        await ledger.record_trial_event(
            conn, STUDY, event_id="decision", event_type="DECISION_RECORDED",
            event_payload=decision,
        )


@pytest.mark.asyncio
async def test_reject_can_be_terminal_early():
    conn = FakeConn()
    await ledger.record_trial_event(
        conn, STUDY, event_id="a", event_type="TRIAL_REGISTERED",
        event_payload=payload("TRIAL_REGISTERED", evidence_refs=[trial_fingerprint(STUDY)]),
    )
    decision = {
        "summary": "reject",
        "decision": "REJECT",
        "authorized_by": "test",
        "reason": "no edge",
        "live_mutation_authorized": False,
    }
    result = await ledger.record_trial_event(
        conn, STUDY, event_id="decision", event_type="DECISION_RECORDED",
        event_payload=decision,
    )
    assert result["inserted"] is True
    with pytest.raises(RuntimeError, match="terminal decision"):
        await ledger.record_trial_event(
            conn, STUDY, event_id="late", event_type="DECISION_RECORDED",
            event_payload=decision,
        )


@pytest.mark.asyncio
async def test_full_feature_chain_allows_research_promote_record_only():
    conn = FakeConn()
    for index, event_type in enumerate(ledger.PROMOTION_REQUIRED["FEATURE"]):
        await ledger.record_feature_event(
            conn,
            STUDY,
            feature_id="cross-layer-v2",
            feature_spec_version="v2",
            feature_card_fingerprint=FP,
            event_id=f"e{index:02}",
            event_type=event_type,
            event_payload=payload(event_type),
        )
    decision = {
        "summary": "promote research candidate",
        "decision": "PROMOTE",
        "authorized_by": "committee",
        "reason": "all gates recorded",
        "live_mutation_authorized": False,
        "promotion_prerequisites_frozen": True,
    }
    result = await ledger.record_feature_event(
        conn,
        STUDY,
        feature_id="cross-layer-v2",
        feature_spec_version="v2",
        feature_card_fingerprint=FP,
        event_id="decision",
        event_type="DECISION_RECORDED",
        event_payload=decision,
    )
    assert result["live_strategy_mutated"] is False
    assert result["execution_authorized"] is False
    status = await ledger.lifecycle_status(
        conn, STUDY, entity_type="FEATURE", entity_id="cross-layer-v2"
    )
    assert status["terminal_decision"] == "PROMOTE"
    assert status["missing_promotion_prerequisites"] == []
    assert status["promotion_allowed"] is False


@pytest.mark.asyncio
async def test_status_shows_missing_prerequisites_without_payload():
    conn = FakeConn()
    await ledger.record_trial_event(
        conn, STUDY, event_id="a", event_type="TRIAL_REGISTERED",
        event_payload=payload("TRIAL_REGISTERED", evidence_refs=[trial_fingerprint(STUDY)]),
    )
    status = await ledger.lifecycle_status(
        conn, STUDY, entity_type="TRIAL", entity_id=trial_manifest(STUDY)["trial_id"]
    )
    assert status["event_count"] == 1
    assert "OOS_OPEN_RECORDED" in status["missing_promotion_prerequisites"]
    assert "event_payload" not in status
