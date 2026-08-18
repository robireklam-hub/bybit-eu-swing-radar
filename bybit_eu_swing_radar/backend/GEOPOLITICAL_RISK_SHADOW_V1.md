# Geopolitical Risk Shadow v1

## Scope

`geopolitical-risk-shadow-v1` is a research-only, label-free geopolitical **news-attention context** layer. It uses GDELT DOC 2.0 TimelineVolRaw data to describe how much monitored global-news attention is associated with a fixed taxonomy of geopolitical topics.

It is **not** a verified event feed, not a geopolitical truth engine, not a trade signal, and not an execution gate.

## Fixed taxonomy

The v1 taxonomy is frozen before outcome observation:

- `armed_conflict`
- `sanctions_trade`
- `energy_shipping`
- `cyber_infrastructure`
- `nuclear_escalation`

Queries are generic geopolitical terms only. They contain no coin names, no Bybit candidates, no long/short terms, and no outcome labels.

## Point-in-time metrics

Each hourly capture requests the previous 24 hours and stores, per topic:

- raw article count;
- GDELT monitored-article normalization count when available;
- normalized article share;
- recent 6-hour share;
- preceding 18-hour baseline share;
- recent-vs-baseline share ratio;
- maximum raw-count timeline bin and timestamp.

Only provider bins at or before `captured_at` are used.

## Methodological constraints

- No composite geopolitical risk score is produced.
- No bullish/bearish direction is produced.
- No threshold is interpreted as an entry, exit, eligibility, or execution decision.
- No journal, net-R, post-trade, strategy-score, eligibility, or execution labels are read.
- Provider failures and missing bins remain explicit and are never interpreted as zero geopolitical risk.
- Media attention can reflect reporting intensity, source mix, terminology, duplicate coverage, and editorial focus; it must not be treated as verified event severity.

## Provider transport

HTTPS is always attempted first. If and only if the GDELT HTTPS endpoint fails at the connection layer, the collector may use GDELT's officially supported HTTP DOC 2.0 endpoint. A successful HTTP fallback is recorded as `PARTIAL` with `transport=HTTP` and `transport_security=PLAINTEXT_PROVIDER_FALLBACK`; it is never represented as full-quality `LIVE` coverage. Non-connect HTTP errors, invalid payloads, and missing timeline bins do not silently downgrade to HTTP.

## Persistence and routes

Snapshots are idempotent per UTC hour in `research_geopolitical_risk_snapshots`.

Hidden authenticated routes:

- `GET /v1/research/geopolitical-risk/spec`
- `POST /v1/research/geopolitical-risk/capture`
- `GET /v1/research/geopolitical-risk/status`

The scheduled workflow runs hourly at minute 43 UTC.

## Production boundary

This v1 layer remains standalone research context. It does not mutate Cross-Layer Context v2 or Signal-Time Context Freeze v2. A later integration must use a separately versioned Cross-Layer v3 and prospective Signal-Time Freeze v3 so existing cohorts remain immutable.
