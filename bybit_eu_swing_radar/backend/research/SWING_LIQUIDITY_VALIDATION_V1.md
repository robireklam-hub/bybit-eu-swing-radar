# Swing executable-liquidity validation v1 — preregistration

Status: **PREREGISTERED BEFORE FORWARD VALIDATION**

This study is research/shadow only. It does not change `MIN_TURNOVER_USDC=100000`, `MAX_SPREAD_BPS=50`, swing scores, eligibility, ranking, tradeability, shortability, or execution. Long execution remains Bybit EU USDC spot. Short execution remains verified borrowable Bybit EU USDC spot-margin only. Derivatives are context-only.

## Question

Is the fixed `turnover_24h_usdc >= 100000` hard gate unnecessarily excluding executable altcoin swing setups when spread and order-book cost are acceptable, particularly in the 25k-100k USDC turnover region?

The study must not replace 100k with an arbitrary lower constant. Turnover is treated as a continuous/tiered liquidity feature and evaluated jointly with spread and top-of-book/depth cost.

## Forward data and label blindness

Each collector snapshot is captured prospectively from the production swing scan plus the contemporaneous Bybit EU spot order book. The snapshot contains only information available at capture time. No future return, TP/stop outcome, MFE, MAE, or post-snapshot bar is written into the forward artifact.

Only a snapshot strictly before a later 4H trigger may be used for execution-cost evaluation. The latest eligible pre-trigger snapshot must be no more than 90 minutes old. If no such snapshot exists, that event is excluded from the primary cost-sensitive test rather than backfilled with future liquidity.

Snapshots are deduplicated during event construction by symbol, side, and first qualifying 4H trigger bar. Repeated hourly snapshots are covariates, not independent outcomes.

## Fixed liquidity features

Turnover tiers are fixed before validation:

- `<25k`
- `25k-50k`
- `50k-100k`
- `100k-250k`
- `250k-1m`
- `>=1m` USDC rolling 24h turnover

Spread tiers are fixed before validation:

- `<=10 bps`
- `>10-20 bps`
- `>20-35 bps`
- `>35-50 bps`
- `>50 bps`

The collector also stores raw L50 bids/asks and immediate round-trip book-crossing cost at standardized quote notionals of 100, 250, 500, and 1000 USDC. These notionals are sensitivity points only; they are not assumed user position sizes and do not authorize a live rule. If an actual intended order notional is later available, participation is computed as `order_notional / turnover_24h_usdc` using that actual notional.

Primary standardized cost sensitivity is 500 USDC. Raw order-book levels are retained so other notionals can be recomputed without recollecting data.

## Opportunity definition

Primary outcome analysis uses a first closed-4H trigger event derived from the existing swing trigger geometry. The event must have:

- side `long` or `short` (neutral excluded);
- `expansion_score >= 55`;
- side-aligned `abs(direction_score) >= 35`;
- valid trigger, entry zone, stop and TP2 geometry;
- for short events, verified `shortable=true` at the relevant pre-trigger snapshot.

The current hard liquidity gate is **not** required for shadow eligibility. Liquidity is the exposure being tested. Core scores and other technical covariates are retained for matching/regression rather than rewritten.

## Outcome definition

After the first qualifying closed-4H trigger:

- entry is the existing setup entry-zone midpoint;
- stop and TP2 use the existing setup geometry;
- horizon is 10 days;
- if stop and TP2 are both touched in the same 4H candle, stop is assumed first;
- gross R is calculated from the fixed entry/stop geometry;
- primary net R subtracts the pre-trigger 500-USDC immediate round-trip book-cost estimate converted into R;
- 100/250/1000 USDC are sensitivity analyses;
- missing/incomplete order-book fills are classified non-executable for that notional, never imputed optimistically.

This outcome model is a research comparability model, not a claim of realized fills.

## Discovery / untouched validation split

Events are ordered by trigger time after collection begins.

**DEVELOPMENT:** first 60 matured independent events.

**VALIDATION:** all subsequent events remain untouched until at least 40 matured independent validation events are available, including at least 10 events in the newly admitted `<100k` turnover region and at least 20 events under the current `>=100k` comparator when feasible. If these minimums are not met, the result is `INSUFFICIENT_VALIDATION_SAMPLE`; thresholds must not be tuned on validation.

Development may be used to select exactly one candidate liquidity rule from the fixed rule family below or to conclude that the current rule should remain unchanged. After that selection, the rule is frozen before validation is inspected.

## Fixed candidate rule family

The finite discovery family is intentionally small:

- **R0 CURRENT:** turnover `>=100k` and spread `<=50 bps`.
- **R1:** turnover `>=50k`, spread `<=35 bps`, and complete 500-USDC book fill.
- **R2:** turnover `>=25k`, spread `<=20 bps`, and complete 500-USDC book fill.
- **R3 COST-AWARE:** turnover `>=25k`, spread `<=50 bps`, complete 500-USDC book fill, and 500-USDC immediate round-trip book cost `<=50 bps`.

No v1 candidate rule admits `turnover <25k` or `spread >50 bps`. Those regions are measured as negative-control/thin-liquidity cohorts but remain blocked regardless of discovery result. New threshold families require a new preregistration and a new untouched validation period.

## Development selection criteria

A non-current rule can be selected for validation only if, in DEVELOPMENT:

1. it admits at least 15 additional independent events below 100k turnover;
2. those additional events have positive mean primary after-cost net R and profit factor >1.0;
3. their median 500-USDC round-trip book cost is <=50 bps and 90th percentile <=100 bps;
4. the candidate is not dominated by R0 on both after-cost expectancy and executable-fill rate;
5. performance is not confined to a single symbol or one contiguous time block.

If none pass, R0 remains the frozen rule and no live liquidity relaxation is proposed.

## Validation promotion criteria

A frozen relaxed rule may be recommended for a separate live-change decision only if all are true on untouched VALIDATION:

1. sample minimums above are met;
2. newly admitted `<100k` events have mean primary net R >0 and profit factor >1.0;
3. versus matched/current-gate comparator, the bootstrap 90% lower confidence bound for the mean-net-R difference is greater than `-0.10 R` (predefined non-inferiority margin);
4. 500-USDC complete-fill rate is >=95% for admitted events;
5. median 500-USDC round-trip book cost <=50 bps and 90th percentile <=100 bps;
6. neither chronological half of validation has mean net R below `-0.20 R`;
7. no evidence requires admitting spread >50 bps or turnover <25k;
8. short events still independently satisfy verified Bybit EU USDC spot-margin borrowability.

Failure of any criterion means **no live gate relaxation** from this study. Missing evidence is failure to promote, not evidence of safety.

## Analyses fixed before validation

Report, at minimum:

- turnover as continuous `log10(turnover)` and the fixed tiers;
- spread as continuous bps and fixed tiers;
- turnover × spread interaction;
- 500-USDC round-trip book cost and complete-fill indicator;
- standardized-notional/turnover participation for 100/250/500/1000 USDC;
- stratification by side, symbol, setup-score band and chronological block;
- matched comparison of newly admitted `<100k` events to current-gate events using pre-trigger technical covariates;
- robust/bootstrap uncertainty and sensitivity across standardized notionals.

No validation-driven threshold search is allowed.

## Decision boundary

This study can produce one of three conclusions:

- `KEEP_CURRENT_GATE`
- `RELAXATION_SUPPORTED_FOR_SEPARATE_LIVE_REVIEW`
- `INSUFFICIENT_VALIDATION_SAMPLE`

Even `RELAXATION_SUPPORTED_FOR_SEPARATE_LIVE_REVIEW` does not itself change production. A separate explicit evidence-backed live-gate patch and regression test is required.
