# Microstructure Controlled Pullback + Reacceleration v1

Status: **PREREGISTERED / NOT ACTIVATED / RESEARCH ONLY**

This experiment tests whether a momentum leg followed by a controlled pullback and pre-trigger order-flow reacceleration has measurable forward value beyond a frozen momentum-only comparator.

## Governance

- Strategy cohort: `0.7.4` only.
- Symbols: `BTCUSDC`, `ETHUSDC`, `SOLUSDC`.
- Direction-symmetric and label-blind.
- No live score, ranking, eligibility, trigger, entry, stop, target, or execution mutation.
- USDC-only execution invariants remain untouched: long = spot; short = verified borrowable USDC spot-margin; no derivatives execution.
- The forward cohort is deliberately **not activated by this PR**. A later activation PR must freeze an explicit UTC start before any outcome for this experiment is inspected. Historical/backfilled rows are ineligible.
- Outcome fields remain hidden until the development sample gate is satisfied.

## Frozen event sequence

1. **Momentum:** direction-normalized price displacement and signed aggressive-flow imbalance agree using only pre-event observations.
2. **Controlled pullback:** partial retracement of the momentum leg without an opposite structural break; spread/depth quality is not degraded relative to the pre-impulse baseline.
3. **Order-flow reacceleration:** signed aggressive-flow and book-pressure measures realign with the original direction before the research trigger timestamp.

The exact numeric feature thresholds are intentionally **not selected in this preregistration PR**. They must be frozen in the activation PR using only pre-outcome calibration rules; no outcome-conditioned threshold search is allowed.

## Frozen hypothesis and evaluation

Primary hypothesis: reacceleration-confirmed controlled pullbacks improve direction-normalized forward performance relative to the momentum-only comparator for the same symbol and direction.

Post-trigger outcomes, inaccessible before the gate:

- direction-normalized return at 5 minutes;
- direction-normalized return at 15 minutes;
- 15-minute MAE;
- 15-minute MFE.

Development gate: **60 independent events total and at least 10 per symbol**. After development, at most one rule may be frozen. Validation then requires **40 untouched events**. Promotion is forbidden before successful untouched validation.

Required robustness cuts include symbol, direction, session, volatility regime, spread, top-of-book depth, and trade volume. Any apparent edge confined to a narrow regime must be reported as conditional rather than generalized.
