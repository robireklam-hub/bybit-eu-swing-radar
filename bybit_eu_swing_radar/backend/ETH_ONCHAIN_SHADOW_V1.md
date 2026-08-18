# ETH On-Chain Context v1

Research-only, label-free Ethereum network context for the Bybit EU Trading Radar.

## Contract

- Spec: `eth-onchain-context-shadow-v1`
- Asset: ETH
- Source: Coin Metrics Community API
- Frequency: closed UTC daily observations only
- Forward persistence: one idempotent snapshot per UTC hour
- Capture cadence: every 6 hours
- `research_only=true`
- `context_only=true`
- `live_strategy_mutated=false`
- `execution_proof=false`
- `promotion_allowed=false`

## Core metrics

- `AdrActCnt`
- `TxCnt`
- `FeeTotNtv`
- `SplyCur`

All four core metrics are required for `data_quality=COMPLETE`.

## Optional ETH-specific metrics

- `SplyCurEL`
- `FeePrioTotNtv`
- `ValidatorActOngCnt`

Optional provider coverage is explicit. Missing optional metrics remain `available=false` / `latest=null`; they are never converted to zero and never gate trading eligibility.

## Provider isolation

Each metric is requested independently and concurrently with Coin Metrics unsupported/forbidden-error tolerance. A provider entitlement or coverage failure for one metric therefore cannot discard valid observations for other metrics.

## Consensus semantics

BTC proof-of-work fields such as `HashRate` and `DiffMean` are deliberately excluded. ETH v1 uses proof-of-stake-compatible network context and does not reinterpret mining metrics after the Merge.

## Forbidden behavior

This layer must not produce a directional bull/bear score, trade signal, eligibility gate, execution proof, outcome-label join, threshold search, or automatic promotion into the live strategy.
