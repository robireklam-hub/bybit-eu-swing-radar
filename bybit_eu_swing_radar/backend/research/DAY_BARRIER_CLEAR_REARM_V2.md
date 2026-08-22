# Day Barrier-Clear Rearm V2 — independent preregistration

Status: RESEARCH ONLY / FROZEN DESIGN / NOT ACTIVATED / NO LIVE STRATEGY MUTATION

## Why v2 exists

V1 is final. Its immutable 60-event DEVELOPMENT cohort failed the preregistered side-balance gate (56 long / 4 short). V1 must not be extended, rebalanced, retuned, or reused as v2 evidence.

V2 addresses only that sampling-design failure. It does not use the observed v1 clear/non-clear effect, feature values, thresholds, or outcome direction to choose parameters.

## Study identity and execution invariants

- trial_id: `day-barrier-clear-rearm-v2`
- research family: `day-barrier-clear-rearm`
- revision: 2
- quote asset: `USDC` only
- long execution model: USDC spot
- short execution model: verified borrowable USDC spot-margin only
- futures/perpetual execution: prohibited
- derivatives OI/funding/liquidations: context only; missing derivatives never gates eligibility
- live score/ranking/eligibility/execution mutation: prohibited

## Fresh prospective activation

This preregistration does **not** activate v2. A separate post-merge activation change must freeze an explicit timezone-aware UTC start boundary strictly after this preregistration is merged. Only terminal parent events resolved strictly after that boundary may enter v2.

No v1 parent event, terminal event, feature row, outcome, partition membership, or fingerprint may be reused or backfilled into v2.

## Parent and barrier semantics

Unless a later preregistered amendment is justified independently of v1 outcomes, v2 preserves the v1 parent eligibility and closed-5m barrier-clear semantics, including fresh post-clear geometry and no inheritance of the original entry/stop/targets.

## Frozen side-stratified partition

Sampling is outcome-blind and side-stratified using only event identity, side, terminal/resolution status, and resolution timestamp.

Deterministic ordering within each side is `(resolved_at UTC, event_id)` ascending.

DEVELOPMENT is frozen only when both quotas are satisfied:

- 30 long terminal events
- 30 short terminal events
- total: 60

Untouched VALIDATION is frozen only after DEVELOPMENT and requires the next:

- 20 long terminal events
- 20 short terminal events
- total: 40

There is no partial DEVELOPMENT or VALIDATION freeze. A side reaching quota early waits for the other side; extra events on the already-full side do not expand or replace the frozen quota. Cohorts are never extended because observed results are favorable or unfavorable.

Stable SHA-256 fingerprints identify frozen DEVELOPMENT and VALIDATION event identities.

## Outcome firewall

Before DEVELOPMENT is frozen, no event supplied to the partition builder may contain forward return, MFE/MAE, PnL, net-R, win/loss, target/stop result, or any other outcome field.

Freezing DEVELOPMENT does not automatically authorize outcome analysis. A later explicit verifier must confirm the immutable cohort identity and all preregistered gates before opening DEVELOPMENT outcomes.

VALIDATION remains untouched until a complete DEVELOPMENT rule and go/no-go criterion are frozen without using VALIDATION outcomes.

No automatic promotion follows any result.
