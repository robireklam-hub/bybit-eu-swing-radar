# Evidence → Feature → Signal → Validation Specification v1

Status: preregistration scaffold for the next research campaign. This document does **not** authorize live strategy changes.

Date: 2026-08-16

## Research question

Can short-horizon positive expectancy on liquid Bybit EU USDC spot instruments be concentrated in periods where:

1. the instrument is genuinely active (`coin in play`),
2. it has persistent time-series momentum rather than a one-bar spike,
3. a controlled pullback preserves the directional structure, and
4. spot aggressive order flow / visible liquidity reaccelerates in the original direction,

**after realistic execution costs?**

## Evidence-to-mechanism map

| Evidence | Mechanism to falsify | Primary observable |
|---|---|---|
| Time-series momentum literature | directional persistence can survive short horizons | return persistence, directional efficiency, path smoothness |
| Order-flow / market-microstructure evidence | aggressive flow and depth imbalance can contain forward information | taker imbalance, CVD delta, book imbalance, microprice, spread |
| Practitioner pullback evidence | continuation quality differs from chase/exhaustion | displacement, retracement depth/duration, volatility/activity contraction |
| Information-arrival research | signal expectancy changes by regime | volatility expansion, macro/event flag, liquidity stress |
| Execution research | prediction can disappear after fees/slippage/adverse selection | spread, modeled taker/maker cost, expected slippage buffer |

## Data policy

### Live forward microstructure collection

Initial collection deliberately starts small to avoid turning the production database into a raw-tick warehouse before storage economics are measured.

Default baseline symbols:

- BTCUSDC
- ETHUSDC
- SOLUSDC

Default source:

- Bybit EU spot public WebSocket (configurable endpoint)
- `publicTrade.{symbol}`
- `orderbook.50.{symbol}`

Default persisted granularity:

- 5-second derived buckets, **not** every L2 message.

This gives genuinely untouched forward data while keeping database growth bounded. Expansion to a dynamic `coin in play` universe happens only after measuring row rate, storage and recorder stability.

### Why derived buckets first

The exchange can push L50 order-book changes at very high frequency. Persisting every raw update across a broad universe would create substantial database/storage load before we know which microstructure features are useful. The first recorder therefore maintains the full L50 state in memory but persists compact, reproducible bucket features.

## Microstructure bucket features v1

### Trades

- trade_count
- taker_buy_base
- taker_sell_base
- taker_buy_quote
- taker_sell_quote
- signed_quote_flow = buy_quote - sell_quote
- total_quote_volume
- trade_vwap
- block_trade_count
- rpi_trade_count

### Book state

- best_bid / best_ask / mid
- spread_bps
- microprice
- bid/ask quote depth at L5, L10 and L50
- normalized depth imbalance at L5, L10 and L50
- last update id / cross sequence
- book message count

### Book-flow proxies

Using delta messages and the prior local-book size at each price:

- bid added quote notional
- bid removed quote notional
- ask added quote notional
- ask removed quote notional

These are **visible-book proxies**, not claims about hidden liquidity or causal intent.

## Data-quality rules

A bucket is research-usable only when:

- a valid local order-book snapshot has been received;
- best bid < best ask;
- book timestamp is present and reasonably fresh;
- required L5 metrics exist;
- symbol is USDC quoted;
- bucket timestamps are monotonic after database normalization.

Trade-only buckets and book-only buckets may be retained but must carry explicit coverage fields; missing data is not silently imputed.

## Phase-1 predictive tests

The recorder itself does not create a signal. Once sufficient untouched data exists, test **forward** relationships only.

Examples:

- `signed_quote_flow(t) -> return(t+5s, t+30s, t+60s, t+5m)`
- `depth_imbalance(t) -> future return`
- `microprice_minus_mid(t) -> future return`
- `flow_acceleration(t) x spread/liquidity state -> future return`
- `pullback_state(t) x flow_reacceleration(t) -> future return`

Contemporaneous price impact is descriptive and must not be presented as tradable edge.

## Candidate strategy family — not yet promoted

### Long hypothesis

`COIN_IN_PLAY`
→ `PERSISTENT_POSITIVE_MOMENTUM`
→ `DISPLACEMENT`
→ `CONTROLLED_PULLBACK`
→ `SELL_AGGRESSION_DECELERATES`
→ `BID_LIQUIDITY_STABILIZES/REFILLS`
→ `TAKER_BUY_FLOW_REACCELERATES`
→ `COST-AWARE CONTINUATION CANDIDATE`

### Bearish technical research

Symmetric mechanics may be measured historically/forward for technical research. Live short execution remains restricted to actually borrowable Bybit EU USDC spot-margin instruments.

## Invalidation candidates

Long-side information invalidation to test:

- structural loss of displacement origin;
- renewed sell-flow acceleration;
- meaningful bid-depth depletion without refill;
- spread/liquidity stress exceeding execution tolerance;
- modeled realizable edge dropping below cost buffer.

No information invalidation may widen a hard risk stop.

## Regime/context features

Initially observational, not hard gates:

- NORMAL
- VOLATILITY_EXPANSION
- EVENT_INFORMATION_ARRIVAL
- LIQUIDITY_STRESS / LIQUIDATION_DRIVEN
- OI/funding/crowding context

OI/funding/liquidations remain enrichment/context only.

## Validation doctrine

Before strategy parameter search:

1. Freeze a small candidate set and record the number of variants tried.
2. Use time-ordered development and holdout splits with purging/embargo for overlapping labels.
3. Keep a genuinely untouched forward period.
4. Report by symbol, time block, regime and liquidity bucket.
5. Require positive **absolute** net expectancy and PF > 1 after costs; relative improvement over a losing baseline is insufficient.
6. Stress maker/taker assumptions separately.
7. Apply multiple-testing / Deflated-Sharpe / PBO-style controls where sample structure permits.
8. Do not rebrand previously inspected validation as untouched OOS.
9. Promotion to live strategy requires a separate explicit evidence-backed decision.

## Initial recorder success criteria

The data-collection layer is technically accepted only if:

- it reconnects safely after WebSocket interruption;
- local snapshots/deltas reconstruct correctly;
- bucket writes are idempotent;
- a database singleton lock prevents duplicate active recorders;
- recorder failures do not affect API/strategy availability;
- status exposes last message/write/error timestamps and counts;
- USDC-only symbol validation is enforced;
- storage rate remains within the planned bounded bucket design.

## Next gate

Do **not** optimize a trading threshold immediately after the recorder starts. First accumulate enough forward observations to measure feature stability and forward predictive information. Strategy construction follows evidence, not the reverse.
