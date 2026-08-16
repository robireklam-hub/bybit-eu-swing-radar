# Repository Cleanup Audit — Research Reset v1

Date: 2026-08-16

## Objective

Keep the proven production infrastructure and reusable research plumbing, remove/retire one-off experiment automation, and establish a clean boundary for the next evidence-driven research architecture.

This cleanup must not alter live day-trade strategy/scoring/execution behavior.

## KEEP — production / reusable infrastructure

- `.github/workflows/backend-tests.yml`
- `.github/workflows/production-flow-freshness-smoke.yml`
- `bybit_eu_swing_radar/backend/day_worker.py`
- `bybit_eu_swing_radar/backend/worker.py`
- `bybit_eu_swing_radar/backend/flow_worker.py`
- `bybit_eu_swing_radar/backend/flow_context.py`
- `bybit_eu_swing_radar/backend/app/repository.py`
- `bybit_eu_swing_radar/backend/app/providers/bybit.py`
- `bybit_eu_swing_radar/backend/app/providers/coinalyze.py`
- production API, health/version/data-status, journal, scan and flow endpoints
- USDC-only execution and spot-margin shortability logic
- generic backtest/replay/data access that is still needed by the new research layer
- `docs/research/TRADING_INTELLIGENCE_KNOWLEDGE_BASE.md`

## KEEP FOR NOW — reusable research plumbing

- `research_dataset_v1.py`: current materialized opportunity/dataset plumbing; do not treat its old strategy population as a promoted strategy.
- historical OI/funding fetch/alignment components where they provide reusable point-in-time enrichment.
- diagnostics/backtest database structures until the clean research core replaces their reusable functions.
- dataset auto workflow while it remains needed for reproducible dataset materialization.

## RETIRE FROM ACTIVE AUTOMATION — completed/failed experiment families

These experiments already produced terminal evidence and must not re-run automatically after every production smoke:

- breakout continuation v5 auto workflow
- entry retest v4 auto workflow
- historical flow v2 auto workflow
- premium microstructure v3 auto workflow

Their code/results remain recoverable from Git history and the knowledge base records the conclusion. Removing the workflow files prevents accidental repeated data mining and CI/production noise.

## RETIRE — obsolete manual tuning workflows

The following were tied to the exhausted v0.7.3 threshold/A-B campaign and should not remain as easy-to-run research paths:

- parameter sensitivity manual workflow
- structure A/B manual workflow
- target-path A/B manual workflow

Reason: further tuning of these families on the already inspected historical population has high overfitting risk and no current promotion path.

## LEGACY CODE — quarantine before deletion/refactor

The following families are not current strategy candidates. Do not extend them. They should be moved under a legacy/archive namespace or removed once reusable pieces are extracted:

- `research_breakout_continuation_v5.py`
- `research_entry_retest_v4.py`
- `research_gate_family_v1.py`
- `research_interactions_v1.py`
- `research_premium_microstructure_v3.py`
- old v0.7.3 sensitivity / structure A-B / target-path A-B modules
- corresponding one-off production runner scripts and tests
- experiment-specific FastAPI modules under `app/v073_research_*`

Do not bulk-delete these in the same change as workflow retirement: some historical/research endpoint dependencies should first be mechanically checked and reusable functions extracted.

## TARGET CLEAN ARCHITECTURE

```text
bybit_eu_swing_radar/backend/
  app/                    # production API + repository/providers
  execution/              # execution invariants/helpers (future extraction)
  research/
    core/                 # common events, costs, splits, metrics
    datasets/             # point-in-time datasets
    features/             # momentum/regime/activity features
    validation/           # purging, holdouts, PBO/DSR-style controls
    strategies/           # small preregistered strategy families
    microstructure/       # trade/L2 recorder + feature engineering
  legacy/                 # temporarily retained failed experiments only
```

No large directory move should be done merely for aesthetics. Extract only code that is actively reused by the next campaign.

## NEXT RESEARCH BUILD

1. Start research-only Bybit spot publicTrade + L2 recorder so untouched forward data begins accumulating.
2. Keep live strategy unchanged.
3. Build the evidence-to-feature specification before strategy implementation.
4. Preregister a small number of momentum + controlled-pullback + order-flow hypotheses.
5. Validate with realistic costs and untouched forward/OOS data.

## Cleanup rule

A file stays active only if it serves one of:

- current production operation;
- reusable data/research infrastructure;
- the currently preregistered research campaign;
- essential tests for one of the above.

Everything else is legacy evidence, not active product surface.
