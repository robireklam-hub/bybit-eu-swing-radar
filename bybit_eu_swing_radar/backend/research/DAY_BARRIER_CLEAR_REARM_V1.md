# Day Barrier-Clear Rearm V1 — preregistration

Status: RESEARCH ONLY / FROZEN SPEC / NO LIVE STRATEGY MUTATION

## Study identity

- trial_id: `day-barrier-clear-rearm-v1`
- parent strategy cohort: exactly `0.7.5`
- market universe: Bybit EU active `USDC` spot pairs only
- long execution context: USDC spot
- short execution context: only verified borrowable USDC spot-margin
- futures/perpetual execution: prohibited
- derivatives OI/funding/liquidations: context only; missing derivatives never gates event eligibility

## Eligible parent event

A parent event is admitted only when all frozen conditions hold at the original decision time:

1. strategy version is exactly `0.7.5`;
2. an already-triggered day setup exists;
3. the execution side is valid under the Bybit EU spot constraints above;
4. setup score >= 70, expansion >= 55, side-direction >= 35, quality >= 65;
5. net RR without the structural barrier is >= 1.8;
6. a confirmed structural barrier lies before TP2 and therefore `target_path_valid=false`;
7. no derivatives field participates in eligibility.

## Barrier-clear observation

After an eligible parent event, observe subsequent 5m bars without looking at forward outcomes. A barrier clear is recorded only when a **closed 5m** finishes beyond the frozen barrier: above it for long, below it for short, while the original breakout boundary/structure is still held.

Capture at minimum:

- symbol, side, parent event identity/time;
- frozen barrier price;
- bars-to-clear;
- clear-bar close and clearance distance;
- clearance distance / 5m ATR where available;
- 5m and 15m relative volume;
- relevant 15m/1H structure state;
- spread/liquidity context;
- session/regime context;
- optional derivatives context with explicit `context_only` provenance.

## Fresh geometry rule

A barrier clear **does not re-authorize the original trade**. Any post-clear candidate must calculate a fresh entry, stop, targets, target path and net RR from information available after the clear. The original entry zone/stop/targets must never be inherited into a post-clear record.

## Frozen DEVELOPMENT / untouched VALIDATION partition

This partition is preregistered before the first exact-main prospective observer produced any terminal parent event. No outcome-bearing field may participate in sample selection or ordering.

- terminal event states eligible for partitioning: `cleared`, `invalidated_boundary`, `invalidated_structure`;
- deterministic ordering: `(resolved_at, event_id)` ascending;
- DEVELOPMENT is exactly the **first 60 terminal parent events**;
- no partial DEVELOPMENT cohort is frozen at 59 or fewer events;
- DEVELOPMENT never expands beyond 60, even if its eventual result or group balance is unfavorable;
- untouched VALIDATION is exactly the **next 40 terminal parent events** after the frozen DEVELOPMENT boundary;
- no event may appear in both partitions;
- stable SHA-256 fingerprints identify both frozen partitions.

Before DEVELOPMENT outcome analysis may even be requested, its fixed 60 events must also contain at least:

- 15 `cleared` events;
- 15 non-clear terminal events (`invalidated_boundary` or `invalidated_structure`);
- 10 long events;
- 10 short events.

If the fixed first 60 fail that balance contract, the study reports insufficient group balance. It does **not** collect extra DEVELOPMENT events until the sample becomes favorable. Validation remains untouched and cannot rescue or retune DEVELOPMENT.

## Label and promotion firewall

Initial capture and partitioning are label-blind: no forward return, MFE/MAE, PnL, TP/stop result, win/loss, realized/net-R or other outcome field is admitted into the event record or sample partition. Freezing the 60/40 identities does not itself open outcomes. A later explicit research-only step must verify that the frozen DEVELOPMENT gate is satisfied before DEVELOPMENT outcomes can be opened.

Any parameter choice may be made on DEVELOPMENT only after that gate. Subsequent VALIDATION remains untouched until the complete development rule is frozen. No automatic promotion follows a favorable DEVELOPMENT or VALIDATION result.

One BTC retrospective example from 2026-08-20 is motivation only, not evidence and not a parameter-selection sample. No live threshold, score, ranking, eligibility or execution rule may be changed from this study without the preregistered development and untouched validation sequence.

All pre-existing v0.7.5 and other historical cohorts remain immutable.
