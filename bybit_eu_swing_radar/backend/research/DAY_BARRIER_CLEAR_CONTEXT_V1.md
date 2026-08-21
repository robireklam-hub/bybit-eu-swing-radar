# Day Barrier-Clear Context V1

Status: RESEARCH ONLY / LABEL-BLIND / NO LIVE STRATEGY MUTATION

This schema completes the context fields required by `day-barrier-clear-rearm-v1` without changing parent eligibility, terminal-state ordering, DEVELOPMENT/VALIDATION partitioning, scoring, ranking or execution.

## Point-in-time clear-bar fields

The following fields are reconstructed only from candles closed by the clearing 5m bar close:

- 5m relative volume over the previous 20 completed 5m bars;
- 15m relative volume over the previous 20 completed 15m bars;
- analogous 5m/15m turnover ratios;
- clear 5m and last completed 15m candle volume/turnover;
- 15m sweep-structure state;
- 15m and 1H EMA structure labels when at least 50 completed bars are available;
- 15m ATR-ratio context when sufficient history exists;
- a deterministic UTC session bucket;
- a limited regime context consisting of ATR-volatility state plus 15m/1H structure alignment.

No candle after the clear close may participate.

## Session buckets

The taxonomy is frozen before any barrier-clear outcome analysis:

- `ASIA_00_08_UTC`
- `EUROPE_08_13_UTC`
- `US_13_21_UTC`
- `LATE_US_21_24_UTC`

These are research buckets only. They do not claim exchange opening/closing hours or causal market-session effects. The raw UTC hour is stored so future analysis can audit the bucket assignment without changing recorded events.

## Regime provenance

This context does **not** pretend to reconstruct the complete `market-regime-shadow-v1` classifier, because that classifier requires 4H/1D history and market breadth not guaranteed by the barrier observer at the exact clear boundary.

Instead it stores:

- point-in-time 15m ATR ratio;
- ATR-only volatility state using the already-frozen `market-regime-shadow-v1` ATR thresholds;
- point-in-time 15m/1H structure alignment;
- `full_market_regime_not_reconstructed=true`.

This prevents false precision while preserving useful regime covariates.

## Spread and liquidity provenance

Historical bid/ask spread cannot be reconstructed from OHLCV. Therefore two liquidity layers are kept separate:

1. **clear-time candle-derived liquidity** — volume, turnover, relative volume and turnover ratios from closed bars;
2. **observer-run market snapshot** — Bybit ticker/instrument bid, ask, spread, 24h turnover/volume and tradeability state collected by the research sidecar near the clear observation.

The observer-run layer must always state:

- `point_in_time_at_clear=false`;
- `spread_at_clear_reconstructed=false`;
- `snapshot_timing=OBSERVER_RUN_NEAR_CLEAR_NOT_RECONSTRUCTED`.

No historical spread is fabricated.

## Firewalls

- research_only = true
- label_free = true
- execution_authorized = false
- live_strategy_mutation = false
- score_mutation = false
- ranking_mutation = false
- eligibility_mutation = false

Outcome fields remain prohibited. This context schema cannot open DEVELOPMENT outcomes, alter the frozen 60/40 partition, or authorize live promotion.
