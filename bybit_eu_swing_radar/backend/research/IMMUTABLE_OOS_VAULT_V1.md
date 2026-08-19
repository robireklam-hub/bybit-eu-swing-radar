# Immutable OOS Vault v1

Status: **research-only governance primitive**. The vault protects the project workflow from accidental or retroactive use of a sealed out-of-sample partition. It is not a claim of cryptographic isolation from database administrators.

## Model

The vault separates two append-only records:

1. **Sealed partition** — immutable trial identity, partition manifest, dataset-lineage fingerprint, payload fingerprint, and OOS payload.
2. **Exposure event** — an explicit, one-time authorization proving the sealed partition was deliberately opened after the prerequisite research stages were frozen/completed.

The library's read path fails closed until an exposure event exists. Status calls expose fingerprints and timestamps only, never the sealed payload.

At the PostgreSQL application-role layer, both tables also install mutation-guard triggers. `UPDATE`, `DELETE`, and `TRUNCATE` are rejected for sealed vault records and exposure records. This materially strengthens append-only behavior, but still does not claim cryptographic isolation from a privileged database administrator capable of changing database objects or bypassing application-role controls.

## Seal contract

A partition can be sealed only for a durably registered frozen trial. Its manifest must state and fingerprint at least:

- exact `trial_id`, revision, research family, and frozen trial fingerprint;
- unique partition ID;
- purpose `IMMUTABLE_OOS`;
- dataset-lineage fingerprint;
- explicit non-empty partition rule;
- `sealed_before_evaluation = true`;
- tuning forbidden;
- threshold search forbidden;
- post-seal selection forbidden;
- open policy `EXPLICIT_AUTHORIZATION_ONCE`.

The OOS payload must be canonical JSON object/array data. Exact retries are idempotent. Reusing the same trial/revision/partition identity with different manifest or payload content fails closed.

## Exposure contract

Opening is append-only and one-time. The authorization must bind to the same frozen trial/partition fingerprint and explicitly attest:

- development frozen;
- walk-forward complete;
- multiple-testing plan frozen;
- data-quality gate passed;
- point-in-time verification passed;
- lineage verification passed;
- thresholds frozen before OOS open;
- OOS tuning forbidden;
- explicit authorizer and reason.

An exact retry is idempotent. A second, different authorization for the same sealed partition fails closed.

## Read contract

`read_exposed_oos_partition` refuses access before exposure. After exposure it recomputes and verifies both the stored partition-manifest fingerprint and the OOS payload fingerprint before returning the payload. A tampered payload or manifest is rejected.

## Non-goals and restrictions

- No API or worker endpoint is added in v1.
- No current accumulating study is automatically sealed or opened by this PR.
- The library does not decide when a sample is mature.
- The library does not choose DSR, PBO, parameter-surface, or promotion thresholds.
- OOS data must never be used to tune thresholds, parameters, feature selection, or model selection.
- OOS exposure does not imply promotion. Statistical robustness, shadow production, degradation monitoring, and all Bybit EU execution invariants remain separate gates.
- The database mutation guards protect normal application-role writes; they are not a substitute for infrastructure-level access control, backups, or privileged-admin governance.

Trial-specific OOS partition rules and open authorization must be introduced separately and preregistered before the relevant holdout is evaluated.
