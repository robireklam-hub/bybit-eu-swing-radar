# Policy Catalyst Sources v1

## Scope

This registry is research/context infrastructure for issue #394. It records high-authority primary sources that may later feed a dedicated real-time policy/liquidity catalyst collector.

It is **context-only**. It cannot change day/swing strategy scores, ranking, eligibility, shortability, entry/stop/targets, or execution.

## SEC source

Primary monitored source:

- Provider: U.S. Securities and Exchange Commission (SEC)
- Authority tier: `PRIMARY_REGULATOR`
- Source family: `OFFICIAL_PRESS_RELEASES`
- Monitor URL: `https://www.sec.gov/newsroom/press-releases`
- Relevant event class: `US_CRYPTO_REGULATION`

Regression fixture supplied from the 2026-08-21 project review:

- Release: `2026-76`
- Date: `2026-08-18`
- Headline: `SEC Proposes New Regulation Crypto Assets`
- URL: `https://www.sec.gov/newsroom/press-releases/2026-76-sec-proposes-new-regulation-crypto-assets`

The individual 2026-76 release is a frozen classification/regression fixture. The monitored source is the SEC press-release index so future material SEC crypto announcements can be discovered rather than polling only one historical page.

## Guardrails

- `context_only=true`
- `hard_gate=false`
- `score_mutation=false`
- `ranking_mutation=false`
- `eligibility_mutation=false`
- `execution_mutation=false`
- no bullish/bearish direction is inferred from a regulatory announcement
- temporal correlation with a market impulse is not causal attribution
- only HTTPS URLs on the exact `www.sec.gov` host and `/newsroom/press-releases` path family qualify for this SEC source class

## Integration boundary

This change freezes source identity and deterministic URL classification only. It does **not** claim that a live SEC collector already exists. The real-time ingestion/persistence/first-seen timestamp layer remains part of issue #394 and must preserve the same non-gating invariants when implemented.
