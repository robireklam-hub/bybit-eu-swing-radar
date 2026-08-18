# Cross-Layer Context v2

V2 starts a new research-only context schema while preserving all v1 snapshots and signal-time freezes unchanged.

## Added layers

- sourced Sector Rotation Shadow v1
- BTC On-Chain Context v1
- ETH On-Chain Context v1

Existing v1 inputs remain: market regime, derivatives positioning, relative strength, event/tokenomics and BTC macro/cycle/ETF.

## Temporal contract

Each source is loaded only from a persisted snapshot with `captured_at <=` the new context timestamp. Future/invalid snapshots are rejected. Missing and stale layers remain explicit with their own age and provenance. Daily sector/relative-strength layers use a wider freshness window than intraday market/derivatives layers; BTC/ETH on-chain uses the six-hour capture cadence with an eight-hour maximum age.

BTC on-chain is attached only to `BTCUSDC`; ETH on-chain only to `ETHUSDC`. Sourced functional taxonomy is attached per symbol without changing its existing relative-strength score.

## Version isolation

`cross-layer-context-shadow-v1` remains untouched. V2 persists into the same research table under `spec_version=cross-layer-context-shadow-v2`, so old and new forward evidence cannot be silently mixed.

## Safety

V2 is label-free, context-only and emits no composite score, eligibility gate, execution proof or trading instruction. Microstructure remains excluded from snapshot joins and must stay strictly pre-signal at signal time.
