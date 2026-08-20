# Controlled-Pullback Activation Calibration v1

Status: **CALIBRATION RULE FROZEN / NOT ACTIVATED / RESEARCH ONLY**

This contract implements the follow-on requirement from the immutable `MICROSTRUCTURE_CONTROLLED_PULLBACK_V1.md` preregistration: numeric activation thresholds must be frozen without inspecting experiment outcomes.

## Label-blind calibration

- Cohort remains strategy version `0.7.4`, symbols `BTCUSDC`, `ETHUSDC`, `SOLUSDC`.
- Source data is pre-activation `microstructure_buckets` only, using 5-second buckets.
- Calibration rows may contain only timestamp, symbol, absolute 60-second mid-price displacement, absolute aggressive-flow share, and absolute book-pressure features.
- Any unexpected field fails closed. This intentionally rejects outcome-bearing rows.
- Each symbol requires at least 100 eligible pre-activation rows.
- The calibration cutoff must be strictly earlier than the forward cohort start, and every calibration row must precede the cutoff.

## Frozen distribution rules

Thresholds are derived independently per symbol so BTC liquidity/volatility cannot set ETH or SOL thresholds:

- momentum absolute 60-second mid-price displacement: 75th percentile;
- momentum absolute aggressive-flow share: 70th percentile;
- reacceleration absolute aggressive-flow share: 65th percentile;
- reacceleration absolute book pressure: 65th percentile.

These quantiles are feature-distribution rules only; no forward return, MAE, MFE, hit rate, PnL, or other outcome is permitted in calibration.

## Frozen structural rules

- controlled retracement fraction: 20% to 60% of the impulse leg;
- spread may be at most 1.10x its pre-impulse baseline;
- top-5 depth must be at least 0.90x its pre-impulse baseline;
- an opposite structural break is not allowed.

## Activation governance

This change **does not activate the experiment** and does not choose a forward start. A later activation snapshot must provide an explicit UTC start and one immutable set of thresholds derived by this contract from pre-start features. Recalibration after activation is prohibited. Outcomes remain hidden until the preregistered 60-event / 10-per-symbol DEVELOPMENT gate, followed by 40 untouched VALIDATION events.

No live score, ranking, eligibility, liquidity threshold, trigger, entry, stop, target, or execution behavior is modified. USDC-only execution invariants remain unchanged: long = spot, short = verified borrowable USDC spot-margin, no derivatives execution.