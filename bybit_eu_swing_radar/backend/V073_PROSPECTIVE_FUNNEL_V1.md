# v0.7.3 Prospective Funnel Observability v1

## Purpose

Research-only, label-free forward observability for the live day-trade v0.7.3 gate chain.

The recorder answers: **where do real forward liquidity-sweep opportunities fail before STRICT?** It does not change the strategy and it does not fit thresholds.

## Gate contract

The recorder reuses the existing v0.7.3 sweep detector and historical diagnostic gate definitions:

1. liquidity sweep
2. reclaim
3. 5m structure shift
4. 5m volume confirmation
5. fully closed non-opposing 15m structure
6. candidate build
7. Bybit EU USDC spot liquidity/execution
8. side execution model
9. expansion >= live v0.7.3 threshold
10. side direction >= live v0.7.3 threshold
11. quality >= live v0.7.3 threshold
12. setup >= live v0.7.3 threshold
13. structural target path
14. net RR >= live v0.7.3 threshold
15. STRICT trade

The recorder receives the live `DAY_TRIGGER_VOLUME_RATIO` value from the day worker, so the forward volume-confirmation gate cannot drift from the deployed v0.7.3 trigger configuration.

Historical diagnostics retain their technical-only short assumption because historical borrowability is unavailable. **Forward prospective capture requires current Bybit EU USDC spot-margin borrowability on short rows.**

## Prospective boundary

The first successful recorder run writes `prospective_start_at`. No sweep whose `sweep_time` is before this timestamp is persisted. There is no historical backfill.

Recent events are sampled as run snapshots for up to 90 minutes after the sweep. A sweep can therefore have multiple point-in-time snapshots as reclaim/structure/volume/15m confirmation evolves.

## Stored data

Stored fields include:

- symbol / side
- sweep time and sweep depth
- gate pass/fail flags
- first failed gate
- current USDC spot-margin shortability status
- live v0.7.3 score components
- target-path validity and expected RR
- source commit SHA
- comparable gate-chain STRICT state
- separate exact `live_strict_trigger_observed` flag from the authoritative live worker payload
- raw label-free sweep/gate snapshot payload

The recorder does **not** store realized outcome, PnL, MFE/MAE, win/loss labels or other post-event target variables.

## Production isolation

The recorder runs after live candidate construction and inside a nested database savepoint. Recorder failure is caught and surfaced as `DEGRADED`; it must not abort journal/cache persistence or mutate live strategy state.

The day-trade status payload exposes a `prospective_funnel` summary with:

- prospective boundary
- current-run snapshot counts
- cumulative distinct sweep events
- cumulative `exact_live_strict_trigger_events`
- latest gate pass counts
- latest first-failed-gate counts
- source commit SHA

## Validation policy

This dataset is observability evidence only. Do not tune thresholds merely to increase sample count. Any future feature or strategy change must be preregistered and validated with discovery/validation separation and untouched forward/out-of-sample evidence.
## Standalone production isolation

Production capture is owned exclusively by the `prospective-funnel-worker` Railway cron service. The live `day_worker.py` contains no prospective-recorder import, call, timeout, or enable flag; it only emits an `EXTERNALIZED` marker. The standalone recorder writes only the prospective research tables and `day_trade_prospective_funnel_status`. Exact live STRICT provenance is read from the authoritative `day_trade_scan` cache rather than inferred from the research recomputation.

