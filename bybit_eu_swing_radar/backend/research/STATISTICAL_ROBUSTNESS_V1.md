# Statistical Robustness v1

Status: **research-only governance primitive**. This document defines how the shared statistical robustness library may be used. It does not authorize live promotion, live scoring changes, eligibility changes, or execution.

## Scope

`research/statistical_robustness.py` supplies three threshold-free evidence calculations:

1. **Deflated Sharpe Ratio (DSR)** — evaluates the selected return series relative to a zero-skill hurdle that reflects the recorded strategy-trial Sharpe dispersion and an explicit effective-trial count, while accounting for return skewness and kurtosis.
2. **Probability of Backtest Overfitting (PBO)** — uses Combinatorially Symmetric Cross-Validation (CSCV) over equal contiguous blocks. The configuration selected in each in-sample half is ranked in its complementary out-of-sample half; PBO is the share of splits with a negative OOS-rank logit.
3. **Parameter-surface robustness** — describes local ordinal-grid neighbors around a selected configuration and reports missing-grid/neighbor coverage explicitly. An optional relative plateau tolerance may be supplied by the caller.

Methodology basis: Bailey & López de Prado, *The Deflated Sharpe Ratio*; Bailey, Borwein, López de Prado & Zhu, *The Probability of Backtest Overfitting*.

## Non-negotiable controls

- `research_only = true`.
- `promotion_allowed = false` in this library.
- No decision threshold is embedded in DSR, PBO, or parameter-surface calculations.
- No automatic PASS/FAIL or PROMOTE/REJECT decision is emitted.
- No threshold may be chosen after observing the evaluation sample.
- Any future decision threshold, CSCV block design, PBO metric, effective-trial-count rule, parameter grid, plateau tolerance, or aggregation rule used as a gate must be frozen in the relevant preregistered trial manifest **before** the applicable evaluation sample is opened.
- DSR Sharpe values are **per observation** in v1. The library performs no implicit annualization.
- `effective_trials` is explicit and may not exceed the number of recorded trial Sharpe values. V1 does not invent a correlation haircut or infer an effective-trial count from the result being evaluated.
- PBO requires an explicit even block count of at least four and an observation count exactly divisible by that block count. Blocks are contiguous and equal-size. Excessive CSCV combinatorics fail closed.
- Missing parameter-grid points are reported as missing; they are never imputed into a robustness plateau.

## Required evidence before any future production promotion

A favorable DSR, PBO, or local parameter surface is **necessary evidence only**, never sufficient by itself. Any future promotion decision must additionally prove, under the project research lifecycle:

- frozen preregistration / trial-registry identity;
- point-in-time correctness and leakage audit;
- unified data-quality gate;
- dataset/feature lineage to immutable source snapshots;
- development and walk-forward evidence;
- immutable OOS evidence that was not used for tuning;
- multiple-testing / model-selection controls appropriate to the recorded search process;
- parameter and regime robustness;
- shadow-production behavior and live degradation monitoring;
- preservation of all execution invariants, including Bybit EU USDC-only execution rules.

## Integration policy

V1 is intentionally a pure-function library and is not wired to a worker, API, strategy score, eligibility gate, or production execution path. Trial-specific integration belongs in a separate PR after that trial's inputs, thresholds, sample partition, and decision rules have been preregistered.

For the currently accumulating swing-liquidity and prospective day-trade studies, this library must not be used to inspect immature outcomes early or to retroactively choose thresholds.
