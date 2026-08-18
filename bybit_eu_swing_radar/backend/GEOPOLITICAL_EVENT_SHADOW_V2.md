# Geopolitical Event Shadow v2

## Why v2 exists

`geopolitical-event-shadow-v2` replaces the **automatic capture path** of the experimental GDELT DOC-query v1. Exact-main production tests showed that the hosted full-text DOC API could become rate-limited and could exceed a bounded synchronous capture window even after conservative pacing/backoff.

The v1 routes and historical snapshots remain available for audit, but its GitHub Actions workflow is manual-only. No v1 data is rewritten or backfilled.

## Source

V2 consumes the GDELT 2.0 Event Database raw static stream. GDELT publishes Event Database updates every 15 minutes. The collector reads `gdeltv2/lastupdate.txt`, selects the latest `.export.CSV.zip` event file, verifies manifest byte size/MD5 when supplied, opens the ZIP and parses only stable core Event fields.

One source export file is treated as one point-in-time observation. No full-text DOC API query is required.

## Stable fields used

- `GLOBALEVENTID`
- `SQLDATE`
- `IsRootEvent`
- `EventCode`
- `EventBaseCode`
- `EventRootCode`
- `QuadClass`
- `GoldsteinScale`
- `NumMentions`
- `NumSources`
- `NumArticles`
- `AvgTone`
- `ActionGeo_CountryCode`
- `DATEADDED`
- `SOURCEURL`

The current v2 export contract is **exactly 61 columns**. The parser fails closed on any different column count. `ActionGeo_CountryCode` is read from the 54th field, `DATEADDED` from the 60th, and `SOURCEURL` from the 61st. Non-empty action-country values must match the two-letter FIPS geo-code shape and `DATEADDED` must be a 14-digit provider timestamp.

## Descriptive context

V2 stores, without producing any trade score:

- total/valid/invalid rows;
- root-event count;
- raw QuadClass distribution;
- GDELT Material Conflict context (`QuadClass = 4`): event count/share, root-event count/share, provider mention/source/article totals, mean Goldstein scale, mean tone, fixed `GoldsteinScale <= -7` descriptive tail count/share, top action countries;
- all-event provider mention/source/article totals;
- mean Goldstein scale and tone;
- top EventRootCode distribution;
- top action countries.

The `-7` Goldstein bin is preregistered as a **descriptive tail bin**, not fitted from outcomes and not used as a threshold for eligibility, direction or execution.

## Point-in-time and prospective rules

- `historical_backfill_allowed=false`.
- Each unique `source_file_timestamp` is idempotently persisted.
- Provider/file/hash/transport provenance is stored.
- Future-dated source files are rejected.
- A file older than 3 hours is marked stale and cannot be production-smoke `FRESH`.
- The collector records how many unique v2 source files were already persisted in the preceding 24 hours. A future rolling baseline must be computed only from these prospective observations; v2 does not backfill a historical baseline.

## Research guards

- research-only;
- label-free;
- context-only;
- no composite geopolitical risk score;
- no bullish/bearish direction;
- no trade decision or hard gate;
- no day/swing strategy score mutation;
- no journal, net-R, outcome or post-trade label reads;
- no shortability/execution mutation;
- no Cross-Layer Context v2 or Signal-Time Context Freeze v2 mutation.

If this layer is later integrated with other context, that must use separately versioned Cross-Layer v3 and prospective Signal-Time Freeze v3.

## Persistence and routes

Table: `research_geopolitical_event_v2_snapshots`

Hidden authenticated routes:

- `GET /v1/research/geopolitical-event-v2/spec`
- `POST /v1/research/geopolitical-event-v2/capture`
- `GET /v1/research/geopolitical-event-v2/status`

Automatic capture is scheduled at minutes `11,26,41,56` UTC each hour. Re-observing the same GDELT source file is idempotent.
