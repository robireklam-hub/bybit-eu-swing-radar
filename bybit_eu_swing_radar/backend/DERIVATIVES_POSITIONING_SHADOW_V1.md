# Derivatives Positioning Shadow v1

Research-only, label-free forward context layer.

Inputs are existing cached data only:
- Bybit global linear derivatives OI/funding from `day_trade_flow:*`;
- cached Coinalyze liquidation totals when available;
- `market-regime-shadow-v1` forward regime snapshots.

Frozen descriptive states:
- positioning: LONG_BUILD, SHORT_BUILD, LONG_DELEVERAGING, SHORT_COVERING, MIXED, INSUFFICIENT_DATA;
- funding crowding: POSITIVE_CROWDED, NEGATIVE_CROWDED, NEUTRAL, UNKNOWN;
- liquidation skew: LONG_LIQ_DOMINANT, SHORT_LIQ_DOMINANT, BALANCED, UNAVAILABLE;
- regime interaction: TREND_ALIGNED_BUILD, TREND_OPPOSED_BUILD, COMPRESSION_POSITION_BUILD, RANGE_CROWDING, VOLATILITY_UNWIND, OBSERVATION.

Frozen thresholds:
- funding crowding: absolute funding rate >= 0.0001;
- liquidation skew: absolute `(long_liq-short_liq)/(long_liq+short_liq)` >= 0.35.

Safety invariants:
- `research_only=true`;
- `label_free=true`;
- `live_strategy_mutated=false`;
- `promotion_allowed=false`;
- derivatives are context only and never Bybit EU spot execution proof;
- missing liquidation data is explicit coverage loss, never an eligibility gate;
- no outcome, journal, net-R or post-trade labels are read;
- no threshold search/tuning on forward observations.
