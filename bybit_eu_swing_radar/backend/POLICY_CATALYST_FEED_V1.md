# Policy Catalyst Feed v1

## Scope

`policy-catalyst-feed-v1` is a primary-source, point-in-time policy/liquidity context layer for the Bybit EU Trading Radar. It exists to make material public U.S. policy announcements visible next to observed market impulse without converting news into a trading signal.

It is **research/context only**. It cannot modify day/swing scores, ranking, eligibility, entry, stop, targets, shortability, or execution.

## Primary sources

| Provider | Authority | Capture | Frozen classes |
| --- | --- | --- | --- |
| SEC | PRIMARY_REGULATOR | official press-release RSS | US_CRYPTO_REGULATION |
| Federal Reserve | PRIMARY_CENTRAL_BANK | official press-release RSS | FED_LIQUIDITY_MARKET_OPERATION, US_MONETARY_POLICY |
| U.S. Treasury | PRIMARY_TREASURY | official press-release index + detail page | TREASURY_DEBT_MANAGEMENT, US_CRYPTO_REGULATION, SANCTIONS_FINANCIAL_GEOPOLITICS |
| CFTC | PRIMARY_REGULATOR | official general press-release RSS | US_CRYPTO_REGULATION, DERIVATIVES_REGULATION |
| White House | PRIMARY_EXECUTIVE | official briefings/statements index + detail page | US_CRYPTO_REGULATION, TRADE_POLICY, SANCTIONS_FINANCIAL_GEOPOLITICS |
| Congress.gov | PRIMARY_LEGISLATIVE | **not yet configured** | US_CRYPTO_REGULATION, TRADE_POLICY |

Congress coverage is intentionally reported unavailable until a bounded targeted/saved-search feed is configured. Missing coverage is never represented as zero policy risk.

## Point-in-time timestamps

Every event preserves separate clocks:

- `published_at`: timestamp supplied by the primary source when recoverable;
- `first_seen_at`: first successful Trading Radar observation persisted in the database;
- `last_seen_at`: most recent capture that still observed the same deterministic event identity;
- capture `captured_at`: timestamp of the source sweep.

`first_seen_at` is immutable on re-observation. This prevents later provider edits/backfills from being mistaken for information that was available earlier.

## Classification

The event taxonomy and keyword mapping are frozen before outcome evaluation. Headline classification is provider-bounded: a keyword can only activate an event class that the provider is preregistered to supply.

Regression fixtures include:

- SEC release 2026-76, `Regulation Crypto Assets`, published 2026-08-18;
- Treasury release `sb0607`, 2026-08-19 long-end liquidity-support buyback announcement.

Fixtures validate source/category handling only. They contain no bullish/bearish label and are not used to tune a trade threshold.

## Live response contract

The existing `market_context_alerts` response-copy layer exposes `policy_catalyst` with:

- `ACTIVE`: fresh capture plus a relevant event first seen in the last six hours;
- `NORMAL`: fresh capture with no newly first-seen relevant event in that window;
- `STALE`: latest capture older than 30 minutes;
- `UNAVAILABLE`: no persisted capture.

When market impulse is elevated/high and policy coverage is stale/unavailable, the response must explicitly state that real-time policy attribution is incomplete. When a fresh policy event and market impulse coincide, the response may state temporal coincidence only; causality remains `UNCONFIRMED_CONTEXT_ONLY`.

## Safety / research guards

- `context_only=true`
- `hard_gate=false`
- `trade_direction=null`
- `score_mutation=false`
- `ranking_mutation=false`
- `eligibility_mutation=false`
- `execution_mutation=false`
- no inference of Bybit EU spot or margin execution conditions from external policy sources
- no post-trade label access in capture/classification
- provider errors are explicit and never converted to a neutral/zero event state

## Persistence and routes

Tables:

- `research_policy_catalyst_events`
- `research_policy_catalyst_captures`

Hidden authenticated routes:

- `GET /v1/research/policy-catalyst/spec`
- `POST /v1/research/policy-catalyst/capture`
- `GET /v1/research/policy-catalyst/status`

The scheduled production capture runs every ten minutes at minutes `04,14,24,34,44,54` UTC and verifies the exact deployed commit before persisting a capture.
