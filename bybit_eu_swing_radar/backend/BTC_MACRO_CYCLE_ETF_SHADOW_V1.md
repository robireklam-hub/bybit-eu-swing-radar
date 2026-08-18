# BTC Macro / Cycle / ETF Intelligence v1

Research-only, label-free, context-only forward layer.

## Inputs

- BTCUSDC closed daily candles from Bybit EU public spot API.
- Bitcoin block height from mempool.space public REST API.
- FRED series: DGS10, DTWEXBGS, WALCL, RRPONTSYD.
- U.S. spot Bitcoin ETF daily flow table from Farside Investors.
- Existing Event & Tokenomics BLS feed receives an explicit embedded-official-2026 fallback if the BLS ICS endpoint is blocked from Railway.

## Frozen descriptive features

- Halving block progress and cycle quartile.
- BTC close, 200D SMA distance, 30D/90D returns, rolling 300D high drawdown.
- Latest macro series values plus 5-observation and 20-observation changes.
- Latest ETF daily net flow plus rolling 5-day and 20-day sums.

No combined bull/bear score is produced. No thresholds are optimized against outcomes.

## Guardrails

- `research_only=true`
- `label_free=true`
- `context_only=true`
- `live_strategy_mutated=false`
- `promotion_allowed=false`
- no scoring, eligibility, trigger, stop, target, shortability or execution mutation
- BLS embedded schedule fallback is reported as PARTIAL, never LIVE
- Farside ETF flow is institutional-flow context, not exchange execution evidence
