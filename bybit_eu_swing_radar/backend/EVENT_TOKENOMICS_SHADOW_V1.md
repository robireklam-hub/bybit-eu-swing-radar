# Event & Tokenomics Intelligence — Shadow v1

Status: **research-only / label-free / context-only / promotion_allowed=false**.

## Purpose

Capture forward and very recent catalysts without changing live strategy behavior. The layer records event type, time precision, severity, affected USDC symbols, source identity, supply context, and explicit provider coverage.

## Sources

Always-on keyless sources:
- U.S. Bureau of Labor Statistics official ICS calendar: selected CPI, Employment Situation, PPI, JOLTS, and ECI releases.
- Federal Reserve official FOMC calendar: scheduled policy decisions and explicitly registered minutes releases.
- Bybit official V5 announcements: listing/delisting/maintenance/protocol-upgrade context. Global Bybit announcements are never treated as proof of Bybit EU availability or execution eligibility.

Optional adapters:
- CoinMarketCal (`COINMARKETCAL_API_KEY`) for curated crypto catalysts.
- Tokenomist (`TOKENOMIST_API_KEY`) for upcoming unlocks and, where the provider plan allows, recent burn/buyback actions.

A missing optional key produces `MISSING_KEY`, not zero-event coverage.

## Frozen v1 semantics

- Active snapshot window: prior 24h plus next 30d.
- Estimated provider dates remain estimated and preserve their display date.
- Token unlock severity is descriptive and based on event value / market-cap ratio:
  - >=5% CRITICAL
  - >=2% HIGH
  - >=0.5% MEDIUM_HIGH
  - otherwise MEDIUM
- CoinMarketCal impact mapping is fixed in code.
- No event produces a long/short signal.
- No event changes scoring, eligibility, shortability, entries, stops, targets, or execution.
- No journal outcome, net-R, or post-trade label is read.

## Forward storage

`research_event_tokenomics_events` keeps stable event IDs with first/last seen timestamps.
`research_event_tokenomics_snapshots` stores idempotent hourly snapshots. The GitHub workflow captures every six hours and also performs exact-SHA verification after a main push.

## Promotion rule

There is no automatic promotion path in v1. Any later use in live scoring requires separate forward validation and explicit approval.
