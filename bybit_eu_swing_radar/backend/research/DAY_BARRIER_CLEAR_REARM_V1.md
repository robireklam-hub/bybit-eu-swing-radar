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

## Label and promotion firewall

Initial capture is label-blind: no forward return, MFE/MAE, PnL, TP/stop result, win/loss or other outcome field is admitted into the event record. Outcome visibility remains locked until a separately preregistered DEVELOPMENT gate. Any parameter choice must be made on DEVELOPMENT only; subsequent VALIDATION must remain untouched until the development rule is frozen.

One BTC retrospective example from 2026-08-20 is motivation only, not evidence and not a parameter-selection sample. No live threshold, score, ranking, eligibility or execution rule may be changed from this study without the preregistered development and untouched validation sequence.

All pre-existing v0.7.5 and other historical cohorts remain immutable.
