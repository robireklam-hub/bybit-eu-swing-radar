# Bybit EU Trading Intelligence & Execution Platform — Product Direction

## Mission

Build one evidence-based Trading Intelligence Engine that can support four monetizable/output paths without fragmenting the codebase into separate products.

## Long-term output paths

### 1. Proprietary trading edge

Use the platform first to improve the operator's own day-trade and swing decisions. Priority is measurable after-cost edge, not signal frequency.

### 2. Trading Intelligence SaaS

Expose validated intelligence through a user-facing radar: ranked setups, market regime, liquidity, microstructure/order-flow, OI/funding/liquidation context, relative strength, tokenomics/events, macro/global liquidity, ETF/institutional flows and on-chain context.

### 3. API / B2B

Keep decision outputs structured and machine-readable so the same validated intelligence can later power third-party dashboards, integrations, alerts and white-label/B2B use cases.

### 4. Automated execution bot

Automated execution is a later layer, not an independent strategy engine. The bot must not invent trades or use looser rules than the validated radar.

Target architecture:

`Market/Data -> Trading Intelligence Engine -> Signal/Decision -> Risk Engine -> Execution Engine -> Journal/Validation`

The execution bot may be introduced only in stages:

1. shadow/paper execution;
2. deterministic replay and failure-mode testing;
3. very small-capital controlled live execution;
4. wider deployment only after operational and strategy evidence is sufficient.

## Development priority

Current priority order:

1. reliable data collection and source observability;
2. forward/out-of-sample validation;
3. identification of repeatable after-cost edge;
4. improvement of radar decision quality;
5. productization/alerts/API surfaces;
6. automated execution.

Do not divert the project into building an execution bot before the upstream signal and risk layers have sufficient evidence.

## Promotion discipline

All candidate intelligence must follow:

`Evidence -> Feature -> Signal -> Validation -> Production`

For eventual execution-capable features, extend the chain to:

`Evidence -> Feature -> Signal -> Validation -> Production -> Risk authorization -> Execution`

Research/shadow layers cannot mutate live strategy/scoring/eligibility/shortability/trigger/entry/stop/target/execution until explicitly promoted after robust evidence.

## Architecture requirements for future automation

Even before an execution bot exists, authoritative setup outputs should remain structured enough to support later deterministic execution. Relevant fields include, where available:

- symbol;
- direction;
- setup/state/decision;
- entry or entry zone;
- invalidation;
- stop;
- targets;
- expected/net R;
- confidence/quality;
- execution market/provenance;
- shortability/borrowability;
- expiry/freshness/data_as_of;
- strategy and feature version.

Human-readable commentary must never become the only source of an execution-critical value.

## Invariants

- Bybit EU execution only.
- USDC quote only.
- Long = USDC spot only.
- Short = verified borrowable USDC spot-margin short only.
- No perpetual/futures execution.
- OI/funding/liquidations = context/scoring enrichment only; missing/degraded derivatives context is not a hard gate.
- Day trade research prioritizes microstructure/liquidity/order flow.
- Swing research prioritizes HTF structure, macro, on-chain, tokenomics and event/news context.
- BTC four-year/halving-cycle information is context/score only, never a deterministic signal.
- No threshold loosening solely to manufacture sample size or increase trade frequency.

## Product-name migration rule

The product/roadmap identity is **Bybit EU Trading Intelligence & Execution Platform**.

Legacy technical names such as `bybit-eu-swing-radar` may remain indefinitely where renaming creates compatibility, deployment, historical-data or operational risk. Branding changes must never trigger technical identifier churn without a separate migration plan and regression verification.

## Decision filter for future work

A proposed feature receives higher priority when it measurably improves one or more of:

- proprietary trading edge;
- validation quality / false-positive reduction;
- reusable Trading Intelligence product value;
- API/B2B utility;
- future safe automated execution.

Features that are mainly cosmetic, narrative or difficult to validate should remain lower priority than evidence-generating infrastructure and decision-quality improvements.
