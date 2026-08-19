# Research Lifecycle Ledger v1

Status: **research-only governance primitive**. This ledger records immutable lifecycle evidence for preregistered research trials and feature candidates. It does not change live strategy, scoring, eligibility, execution, or production configuration.

## Purpose

The project already has separate controls for frozen trial registration, point-in-time correctness, data-quality contracts, dataset/feature lineage, immutable OOS storage, and statistical robustness. The lifecycle ledger connects those controls into an append-only experiment history so that research progress and decisions cannot be reconstructed retroactively from mutable state.

Two entity types are supported:

- `TRIAL` — the preregistered experiment as a whole;
- `FEATURE` — a concrete feature/Trading Intelligence candidate bound to an immutable feature-card fingerprint.

## Lifecycle

Trial evidence is recorded monotonically as:

`TRIAL_REGISTERED → PIT_AUDIT_RECORDED → DATA_QUALITY_GATE_RECORDED → LINEAGE_RECORDED → DEVELOPMENT_EVIDENCE_RECORDED → WALK_FORWARD_EVIDENCE_RECORDED → MULTIPLE_TESTING_PLAN_RECORDED → OOS_SEAL_RECORDED → OOS_OPEN_RECORDED → ROBUSTNESS_EVIDENCE_RECORDED → SHADOW_EVIDENCE_RECORDED → DECISION_RECORDED`

Feature evidence follows the same governance chain, preceded by:

`HYPOTHESIS_RECORDED → FEATURE_CARD_RECORDED`

Exact retries are idempotent. Reusing an event identity with different content fails closed. Every advancing non-decision milestone requires its direct predecessor to have already been recorded, so lifecycle gates cannot be skipped. Once a later lifecycle stage has been recorded, an earlier stage cannot be appended retroactively. `DECISION_RECORDED` is terminal for that entity/specification.

## Evidence model

Evidence events contain compact governance metadata plus `evidence_refs`, which must be SHA-256 fingerprints. The ledger is not a second results database.

Raw OOS or outcome payloads are explicitly forbidden, including keys such as `returns`, `net_r`, `mfe_r`, `mae_r`, and `oos_payload`. Raw OOS material remains behind the Immutable OOS Vault; the lifecycle ledger may reference its immutable fingerprints only.

A feature lifecycle is bound to one `feature_card_fingerprint`. Changing the feature card or feature specification requires a new lifecycle identity rather than rewriting prior history.

## Promotion decision contract

A `PROMOTE` decision can be recorded only after the complete evidence chain is already present for that entity. A `REJECT` decision may terminate a candidate earlier when evidence is insufficient or negative.

Even a recorded `PROMOTE` decision has:

- `live_mutation_authorized = false`;
- no strategy-score mutation;
- no eligibility mutation;
- no execution authorization;
- no production change.

Any eventual production promotion remains a separate reviewed code/configuration change after the research governance decision.

## Immutability

The ledger table is append-only at both application and database-role level:

- `UPDATE` rejected;
- `DELETE` rejected;
- `TRUNCATE` rejected;
- only one terminal decision is allowed per trial/revision/entity identity.

This is a workflow/data-integrity control for the application database role, not a claim of cryptographic isolation from database administrators.

## Integration policy

V1 intentionally adds no API endpoint, scheduled worker, score hook, or execution path. Trial-specific integration should be added separately when a collector/evaluator reaches a real lifecycle milestone. Existing accumulating samples must not be backfilled with invented historical lifecycle timestamps.
