# Day Barrier-Clear Rearm Follow-up V2 — preregistration

Status: RESEARCH ONLY / FROZEN BEFORE COLLECTION / NO LIVE STRATEGY MUTATION

## Why a separate follow-up exists

`day-barrier-clear-rearm-v1` remains immutable. Its fixed first-60 DEVELOPMENT cohort produced 56 long and 4 short terminal events and therefore failed its preregistered minimum-short balance gate. V2 does not pad, reopen, relabel, or otherwise rescue V1.

## Study identity and execution invariants

- trial_id: `day-barrier-clear-rearm-followup-v2`
- parent semantics: exactly the already-frozen V1 parent-event definition (`0.7.5` research semantics)
- market universe: Bybit EU active `USDC` spot pairs only
- long execution context: USDC spot
- short execution context: only verified borrowable USDC spot-margin
- futures/perpetual execution: prohibited
- derivatives OI/funding/liquidations: context/scoring only; missing derivatives data never gates event eligibility
- live score/ranking/eligibility/threshold/execution mutation: prohibited

## Fresh prospective start boundary

V2 must use a new immutable composite start boundary `(resolved_at, event_id)` captured only after this preregistration is deployed. The boundary is mandatory input to cohort construction and must not be inferred from outcomes or chosen after inspecting V2 event results.

Only terminal parent events strictly after that composite boundary may enter V2. All earlier V1 events remain excluded even if reprocessing them would improve balance.

## Label-blind terminal event stream

Eligible terminal states remain:

- `cleared`
- `invalidated_boundary`
- `invalidated_structure`

Events are canonicalized and ordered by `(resolved_at, event_id)` within each side. No forward return, MFE/MAE, PnL, TP/stop result, win/loss, realized/net-R or other outcome-bearing field may participate in collection, side assignment, ordering, quota completion or fingerprinting.

## Preregistered side-stratified cohort

The estimand is deliberately side-balanced rather than natural-frequency weighted.

DEVELOPMENT is frozen when both independent side quotas are complete:

- earliest 30 eligible long terminal events after the V2 start boundary;
- earliest 30 eligible short terminal events after the V2 start boundary.

Thus DEVELOPMENT = 60 events exactly, 30 long + 30 short.

Untouched VALIDATION is frozen only after DEVELOPMENT and consists of:

- next 20 eligible long terminal events after the frozen long DEVELOPMENT boundary;
- next 20 eligible short terminal events after the frozen short DEVELOPMENT boundary.

Thus VALIDATION = 40 events exactly, 20 long + 20 short. No event may appear in both partitions.

The selector must retain every eligible prospective event in the source stream. Quotas affect only immutable cohort membership; they must not suppress recording of excess events on the more frequent side.

Because this design intentionally equal-weights sides, a natural-frequency aggregate effect is out of scope and may not be claimed from this cohort without a separately preregistered analysis.

## Additional DEVELOPMENT balance gate

Before DEVELOPMENT outcomes may be opened, the fixed 60 must contain at least:

- 15 `cleared` events;
- 15 non-clear terminal events (`invalidated_boundary` or `invalidated_structure`).

If this outcome-independent terminal-state balance fails, V2 closes as insufficiently balanced. It must not collect extra DEVELOPMENT events to repair the result.

## Fingerprints and boundaries

Stable SHA-256 fingerprints identify DEVELOPMENT and VALIDATION. The frozen evidence must include:

- global V2 start boundary;
- long DEVELOPMENT boundary;
- short DEVELOPMENT boundary;
- DEVELOPMENT fingerprint;
- long VALIDATION boundary when ready;
- short VALIDATION boundary when ready;
- VALIDATION fingerprint when ready.

## Outcome and promotion firewall

Completing the quotas does not itself open outcomes. A separate label-blind gate must verify the frozen identities, fingerprints, side quotas and terminal-state balance before DEVELOPMENT outcomes can be requested.

VALIDATION outcomes remain untouched until a complete DEVELOPMENT-derived rule is frozen. Threshold search on VALIDATION is prohibited. No favorable result automatically authorizes promotion.

No live strategy threshold, score, ranking, eligibility, shortability or execution rule may change from V2 without the preregistered DEVELOPMENT and untouched VALIDATION sequence plus a separate explicit promotion decision.

All prior cohorts, including V1 first-60 and any already-collected V1 validation candidates, remain immutable.
