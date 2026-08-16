# Trading Intelligence Knowledge Base

Status: research knowledge base; not production strategy specification.

Last updated: 2026-08-16

Repository cleanup companion: `docs/research/REPOSITORY_CLEANUP_AUDIT.md`.

## Purpose

This file preserves the evidence gathered before designing the next Bybit EU Trading Radar strategy family. It separates empirical evidence, practitioner-derived hypotheses, project measurements, and unverified ideas so that future implementation does not drift into narrative-driven or repeated threshold tuning.

## Non-negotiable project invariants

- Live execution universe is USDC quote only.
- Long execution is USDC spot.
- Live short execution is USDC spot-margin only and only when the asset is actually borrowable/shortable.
- Historical short observations may be used for technical research but do not prove live short executability.
- OI, funding, liquidations, derivatives premium and related derivatives data are context/scoring enrichment, never a hard execution gate by themselves.
- No live `day_worker` strategy/scoring/execution change without a separate evidence-backed promotion decision.
- Workflow SUCCESS is not research completion unless the domain-level report/output is complete.

## Evidence hierarchy

### A. Strong empirical / academic evidence

1. **Time-series momentum / trend persistence**
   - Long-horizon cross-asset literature supports time-series momentum.
   - Recent crypto research is more supportive of time-series momentum than naive cross-sectional momentum once realistic risks are considered.
   - Research implication: measure absolute directional persistence, not merely rank one coin against another.

2. **Order-flow imbalance and liquidity interaction**
   - Market-microstructure literature shows order-flow imbalance has a strong relationship with short-horizon price changes and that price impact depends on market depth.
   - Crypto-specific research reports useful out-of-sample information in signed trade/order-flow variables.
   - Critical distinction: contemporaneous `OFI(t) -> return(t)` is not sufficient evidence. The tradable question is `OFI(t) -> future return(t+1...n)` after costs.

3. **Market state / volatility regime matters**
   - Spread, volatility, liquidity and trading activity are state- and time-dependent.
   - Macro information releases such as FOMC, inflation and employment announcements can create distinct high-volatility/high-volume regimes in crypto.
   - Research implication: the same setup should not be assumed to have identical expectancy in normal, expansion, event and liquidity-stress regimes.

4. **Execution costs and adverse selection are part of the strategy**
   - Prediction is not equivalent to realizable PnL.
   - Maker/taker fees, spread, slippage, latency, fill probability and adverse selection must be modeled separately.
   - A signal should only become a trade when expected edge exceeds realistic execution cost plus a safety buffer.

5. **Backtest-overfitting control is mandatory**
   - Repeatedly selecting the best result from many strategy/threshold variants creates false discoveries.
   - Use preregistered hypotheses, purged/embargoed time splits, block/symbol stability, transaction-cost sensitivity, PBO/CPCV where appropriate, Deflated Sharpe concepts and untouched forward validation.

## Practitioner evidence / hypothesis generators

Practitioner material is not treated as proof of profitability unless independently auditable. It is used to identify mechanisms worth falsifying on our own data.

### Linda Bradford Raschke

Reviewed themes: relative strength, trend vs mean reversion, breakout context, short-term trading and market profile.

Useful ideas:
- Relative strength is regime-dependent, not universally predictive.
- Trend/positive-feedback environments and range/mean-reverting environments require different interpretation.
- Lookback choice materially changes what relative strength measures.

### Adam Grimes

Reviewed themes: pullbacks, trend strength, momentum/thrust and context.

Useful mechanism:
- Genuine directional thrust followed by a controlled countertrend pullback may be more meaningful than a visually attractive chart pattern.
- Climax/exhaustion should not be confused with healthy continuation momentum.
- Context is more important than the isolated pattern.

### Mike Bellafiore / SMB Capital

Reviewed themes: Stocks in Play, relative strength/weakness, RVOL, VWAP pullbacks, tape reading, trade review and scoring.

Useful mechanisms:
- Instrument selection precedes setup selection.
- Catalyst, abnormal relative volume, technical location and tape/order flow are combined rather than treated as independent magic indicators.
- A pullback is more interesting when activity contracts during the pullback and re-expands with renewed directional participation.
- Tape/order-flow concepts such as absorption and aggressive prints can invalidate or confirm a price setup.

### CryptoCred / DonAlt / TechnicalRoundup

Reviewed themes: market structure, failed breakouts, HTF levels, relative strength, instrument choice and no-trade conditions.

Useful ideas:
- Relative strength should be contextual rather than an automatic long/short signal.
- Instrument selection and market context can be more important than forcing a setup on every symbol.

### Skew / 52kSkew

Reviewed themes: trend, momentum, order flow, OI, funding, CVD and footprint.

Useful ideas:
- OI/funding/CVD are easy to misread without price/context.
- Trend + momentum + order flow is a more coherent framework than a single derivatives threshold.
- Derivatives positioning should describe leverage/crowding/fragility rather than mechanically dictate direction.

### Trader Magus

Reviewed themes: auction-market/order-flow thinking, momentum vs mean reversion, spot vs perp flow.

Useful ideas:
- Spot flow can invalidate an apparently attractive derivatives narrative.
- Bounce quality, active buyer/seller presence and liquidity behavior can provide information invalidation before a conventional price stop.

### StonXBT

Reviewed themes: DOM, iceberg/order-flow behavior, prop-style risk management and trend following.

Useful idea:
- Complex scalping is not automatically superior to capturing cleaner directional trends; microstructure should improve selection/execution rather than create constant trading pressure.

### Andres Granger

Reviewed themes: futures/order-flow scalping, crypto market-neutral strategies, automation and structural inefficiencies.

Useful idea:
- Look for repeatable structural inefficiencies and automate them rather than rely on discretionary pattern stories.

### Jordi Alexander / Selini Capital

Reviewed themes: HFT/market making, strategy evaluation and table selection.

Useful idea:
- `Table selection` is fundamental: first identify where an inefficiency can plausibly exist; do not demand that one universal setup work everywhere.

### Mike Komaransky / Cumberland, Bobby Cho / Cumberland

Reviewed themes: institutional crypto market making, liquidity, arbitrage, venue mechanics and institutional flow.

Useful idea:
- Crypto market structure and flow can matter more than isolated classical technical-analysis patterns.

### Doug Colkitt

Reviewed themes: leverage, perpetual mechanics, liquidation cascades and liquidity withdrawal.

Useful idea:
- Liquidation data should identify forced-flow/liquidity-stress regimes, not become `liquidations high -> buy/sell` logic.

### Greg Magadini and crypto options practitioners

Reviewed themes: volatility trading, options flow and volatility regimes.

Useful idea:
- Edge does not have to originate in directional prediction; volatility/positioning information can improve market-state context even when it is not a direct spot entry signal.

### Wintermute / Arbelos / Deribit institutional interviews

Reviewed themes: OTC flow, options positioning, catalyst trades, volatility surface and institutional positioning.

Useful idea:
- Ask who is forced or motivated to trade and why; flow source can be more informative than a generic indicator value.

## Crypto-specific event/catalyst evidence

Candidate event classes for research:
- FOMC / CPI / employment and other high-information macro releases.
- Listings / cross-listings.
- Token unlocks / emissions changes.
- Protocol upgrades and governance events.
- Hacks / exchange incidents / regulatory events.
- Burns, buybacks and treasury purchases.
- ETF/institutional flows, primarily as BTC/ETH regime context.

Important rule: **event != directional signal**.

The research interpretation is:

`EVENT -> abnormal information-arrival regime -> observe price + volume + liquidity + order flow`

not:

`good news -> long`.

## Project evidence already measured

The current research campaign rejected multiple strategy families. These are measured project results, not external literature claims.

### Original v0.7.3 opportunity population

Broad population showed materially negative expectancy in both discovery and validation. Feature slicing found relative improvements (not positive standalone edge), including high expansion and some volatility-regime effects.

### Interaction / gate-family research

Combinations of high expansion and pretrade-chain conditions did not produce positive holdout expectancy. Promotion was rejected.

### Historical OI/funding enrichment

Historical point-in-time derivatives enrichment achieved high coverage but the selected OI/funding family remained negative on internal holdout. Promotion rejected.

### Premium-index microstructure proxy

Premium-based crowding/microstructure family remained negative on holdout. Promotion rejected.

### Retest / delayed-entry research

Changing the post-confirmation entry mechanism did not establish a robust positive edge.

### Breakout-continuation family

A separate momentum/breakout family also remained materially negative. This argues against continuing to tune breakout thresholds on the same historical population.

### Consequence

Do not spend further research budget on:
- RSI/MACD-style indicator stacking.
- Single candlestick patterns.
- Simple liquidity sweep/reclaim alone.
- Simple breakout alone.
- OI threshold alone.
- Funding threshold alone.
- Premium threshold alone.
- `top relative-strength coin -> long` alone.
- Large score/threshold searches over the already mined dataset.

## Current strongest research architecture

This is a hypothesis architecture, **not a proven strategy**.

### Layer 1 — Market state

Candidate states:
- NORMAL
- VOLATILITY_EXPANSION
- EVENT_INFORMATION_ARRIVAL
- LIQUIDITY_STRESS / LIQUIDATION_DRIVEN

Purpose: determine whether the market environment is compatible with the mechanism being tested.

### Layer 2 — Instrument state / Coin in Play

Candidate features:
- abnormal volume / relative volume;
- abnormal realized volatility;
- liquidity and spread quality;
- market-relative activity;
- catalyst/event flag;
- relative strength as context/ranking, not primary entry signal.

Purpose: avoid treating every USDC instrument and every minute as equally tradeable.

### Layer 3 — Directional state

Primary candidate: time-series momentum.

Features to investigate:
- momentum magnitude;
- momentum persistence;
- directional efficiency / path smoothness;
- displacement relative to recent volatility;
- HTF structure alignment.

Important distinction: a 4% one-candle jump is not assumed equivalent to a 4% persistent directional move.

### Layer 4 — Setup state

Primary candidate mechanism:

`directional displacement -> controlled pullback -> continuation opportunity`

The pullback should be measured rather than visually described:
- retracement depth;
- duration;
- volatility contraction;
- pullback volume/activity contraction;
- structure retention;
- absence/presence of exhaustion.

### Layer 5 — Microstructure confirmation / invalidation

Candidate live/research features:
- taker buy vs taker sell volume;
- rolling CVD;
- signed trade-flow imbalance;
- trade-flow acceleration/deceleration;
- top-of-book and L5/L10/L50 depth imbalance;
- spread;
- microprice;
- bid/ask depletion;
- bid/ask refill;
- add/cancel intensity proxies;
- absorption/refill proxies;
- short-horizon liquidity stress.

Core predictive test:

`microstructure_state(t) -> future return(t+h)`

after fees/slippage, rather than merely measuring simultaneous price impact.

### Layer 6 — Execution economics

A candidate signal must survive:
- taker fee assumptions;
- maker fee assumptions separately;
- spread;
- expected slippage;
- latency/fill uncertainty where measurable;
- adverse-selection buffer.

Conceptual gate:

`expected_realizable_edge > fee + spread/slippage + adverse_selection_buffer`

## Candidate long hypothesis

> On liquid USDC spot instruments, positive short-horizon expectancy may be concentrated in active instruments showing persistent time-series momentum where the first controlled pullback preserves the directional structure and spot aggressive order flow/liquidity subsequently reaccelerates in the original direction.

Possible sequence:

`COIN_IN_PLAY`
`-> PERSISTENT_POSITIVE_MOMENTUM`
`-> DISPLACEMENT`
`-> CONTROLLED_PULLBACK`
`-> SELL_AGGRESSION_DECELERATES`
`-> BID_LIQUIDITY_STABILIZES_OR_REFILLS`
`-> TAKER_BUY_IMBALANCE_REACCELERATES`
`-> CONTINUATION_ENTRY_CANDIDATE`

This remains unproven until preregistered and tested out of sample.

## Candidate bearish technical hypothesis

Symmetric research may be performed historically:

`COIN_IN_PLAY`
`-> PERSISTENT_NEGATIVE_MOMENTUM`
`-> DOWNSIDE_DISPLACEMENT`
`-> CONTROLLED_BOUNCE`
`-> BUY_AGGRESSION_DECELERATES`
`-> ASK_LIQUIDITY_STABILIZES_OR_REFILLS`
`-> TAKER_SELL_IMBALANCE_REACCELERATES`

Historical bearish technical evidence does **not** authorize live short execution. Live short remains restricted to actually borrowable USDC spot-margin instruments.

## Information invalidation concept

A trade hypothesis may become invalid before a conventional price stop if the information supporting it disappears.

Long-side examples to research:
- pullback destroys displacement origin / structural level;
- renewed sell aggression;
- bid depth disappears rather than refills;
- spread/liquidity stress exceeds execution tolerance;
- expected edge falls below cost buffer.

This must be tested objectively; it is not permission for discretionary stop widening.

## Bybit data capability notes

### Live public order book

Bybit public WebSocket provides spot order-book depths including L1/L50/L200/L1000 with snapshot/delta updates, sequence/update identifiers and timestamps. This makes a live microstructure recorder technically feasible.

### Public trades

Trade messages expose taker side (`Buy`/`Sell`), enabling direct signed aggressive-flow construction without inferring aggressor side solely from price changes.

### Derivatives context

Historical OI and funding can be aligned point-in-time and remain context/enrichment only.

### Historical L2 limitation

Full historical L2/order-book replay is not equivalent to the readily available live public stream. Therefore a robust microstructure campaign should either:
1. acquire a reliable archived tick/L2 source, and/or
2. begin recording our own Bybit publicTrade + L2 stream as soon as possible.

The second option also creates genuinely untouched forward data.

## Proposed validation doctrine for the next campaign

Before looking at results:
1. Write the economic/market mechanism in plain language.
2. Freeze a small number of feature definitions and strategy variants.
3. Define entry, exit, invalidation and cost assumptions.
4. Define minimum sample size and promotion thresholds.
5. Separate development, internal holdout and untouched forward/OOS periods.
6. Use purging/embargo where overlapping labels can leak information.
7. Report results by time block, symbol, market regime and liquidity bucket.
8. Report transaction-cost sensitivity.
9. Track number of hypotheses/variants tested.
10. Apply multiple-testing/overfitting controls (PBO/CPCV/Deflated-Sharpe concepts where appropriate).
11. Do not reuse a previously inspected validation window and call it untouched.
12. Promotion requires positive absolute expectancy, not merely improvement over a worse baseline.

## Source / reading map

Representative sources reviewed during the knowledge-building phase include:

### Academic / empirical
- Moskowitz, Ooi, Pedersen — *Time Series Momentum*.
- Hurst, Ooi, Pedersen — long-run trend-following evidence.
- Cont, Kukanov, Stoikov — order-flow imbalance and price impact.
- Bailey et al. / López de Prado — backtest overfitting, PBO and Deflated Sharpe Ratio.
- Recent crypto momentum literature comparing time-series and cross-sectional momentum.
- Recent crypto order-flow / L2 / liquidity-stress studies examining OFI, depth, spread, adverse selection and out-of-sample prediction.
- Intraday crypto macro-announcement studies examining FOMC/inflation/employment information arrival.
- Crypto listing/cross-listing and token-unlock event studies.
- Live crypto execution research using Bybit/Binance orders to study latency, liquidity, fill quality and adverse selection.

### Practitioner / institutional
- Linda Bradford Raschke — relative strength, trend/mean-reversion context.
- Adam Grimes — momentum, pullbacks and contextual price action.
- Mike Bellafiore / SMB Capital — Stocks in Play, RVOL, VWAP pullbacks and tape reading.
- CryptoCred / DonAlt / TechnicalRoundup — crypto structure, instrument selection and relative-strength context.
- Skew / 52kSkew — trend, momentum, order flow, OI/funding/CVD/footprint.
- Trader Magus — auction-market/order-flow thinking and spot/perp interaction.
- StonXBT — DOM/order flow and trend/risk perspective.
- Andres Granger — crypto systematic/market-neutral inefficiencies.
- Jordi Alexander / Selini Capital — market making, HFT and table selection.
- Mike Komaransky / Bobby Cho / Cumberland — institutional crypto liquidity/market structure.
- Doug Colkitt — leverage, perpetuals and liquidation/liquidity mechanics.
- Greg Magadini — crypto options/volatility trading.
- Wintermute, Arbelos and Deribit institutional interviews — OTC/options/institutional flow and volatility context.

## Research discipline

Every future statement should be tagged conceptually as one of:

- **MEASURED PROJECT RESULT** — reproduced in our own dataset/report.
- **EXTERNAL EMPIRICAL EVIDENCE** — supported by research but not yet reproduced here.
- **PRACTITIONER HYPOTHESIS** — useful mechanism suggested by experienced traders.
- **PROJECT HYPOTHESIS** — proposed mechanism awaiting falsification.

Do not silently promote a practitioner claim or external paper result into a production rule.

## Immediate next research actions

1. Preserve this knowledge base and update it as evidence changes.
2. Specify the `Evidence -> Mechanism -> Feature -> Signal -> Validation` map in machine-testable terms.
3. Start a research-only Bybit spot microstructure recorder for public trades and L2 order book so untouched forward data begins accumulating.
4. Audit available archived historical tick/L2 sources before buying/depending on one.
5. Preregister the first momentum + controlled-pullback + order-flow-reacceleration experiment.
6. Keep the live strategy unchanged until promotion criteria are satisfied.

---

This document intentionally favors falsifiable mechanisms over narratives. A plausible story is not an edge; an edge must survive realistic costs and genuinely out-of-sample testing.
