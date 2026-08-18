# Sourced Sector Rotation Shadow v1

Research-only, label-free sector-taxonomy and cross-sectional rotation context for the Bybit EU Trading Radar.

## Contract

- Spec: `sector-rotation-shadow-v1`
- Universe: current Bybit EU USDC spot relative-strength universe
- Taxonomy provider: CoinPaprika
- Taxonomy source: provider `functional` tags only
- Multi-label: yes; one asset may belong to multiple provider groups
- Manual/hand-maintained sector labels: forbidden
- `research_only=true`
- `context_only=true`
- `live_strategy_mutated=false`
- `execution_proof=false`
- `promotion_allowed=false`

## Identity resolution

Bybit `BASEUSDC` symbols are resolved against active CoinPaprika tickers by base symbol. When a symbol collision exists, the best positive provider rank is selected deterministically. The collision is never hidden: candidate count, candidates and `ambiguous=true` remain in the snapshot for auditability.

Unresolved symbols remain explicit and reduce resolution/taxonomy coverage. They are never guessed or manually mapped.

## Functional taxonomy

The collector requests the provider tag list with coin memberships and retains only tags whose provider type is `functional`. Technical tags are excluded from the sector taxonomy. Membership remains overlapping rather than forcing every token into one arbitrary sector.

## Rotation context

For each sourced functional group, the layer aggregates the already-preregistered `relative-strength-shadow-v1` symbol metrics:

- mean / median RS score
- mean 7d / 30d / 90d return
- mean 7d-vs-30d rotation delta
- accelerating / decelerating constituent counts
- leader / outperformer constituent count

Single-member groups are reported for taxonomy completeness but are not ranked as sector rotation. Groups with at least two current-universe constituents receive a descriptive relative-strength rank.

## Coverage and safety

The snapshot reports symbol-resolution coverage, functional-taxonomy coverage, ambiguity counts, total groups and rotation-ranked multi-coin groups. Missing coverage remains explicit.

This layer must not generate a trade direction, bull/bear score, eligibility gate, execution proof, outcome-label join, threshold search or automatic live-strategy promotion.
