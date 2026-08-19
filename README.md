# Bybit EU Trading Intelligence & Execution Platform

A project for building an evidence-based crypto trading intelligence platform on top of Bybit EU, with a strict separation between research, validated signals, decision support and later automated execution.

## Product direction

The platform is developed as one shared intelligence engine with four long-term outputs:

1. **Validated proprietary trading edge** — day-trade and swing setups supported by forward/out-of-sample evidence.
2. **Trading Intelligence product / SaaS** — ranked setups, regime, liquidity, microstructure, derivatives context, macro/on-chain/event context and alerts.
3. **API / B2B layer** — machine-readable intelligence for external tools, dashboards and integrations.
4. **Automated execution bot** — a later execution layer that may act only on already validated signals under a separate risk engine.

## Core development principle

`Evidence -> Feature -> Signal -> Validation -> Production -> Execution`

No narrative-only feature, single indicator, candlestick pattern or research hypothesis is promoted into live trading logic without measurable evidence and out-of-sample/forward validation.

## Execution invariants

- Execution universe: **Bybit EU, USDC quote only**.
- Long: **USDC spot only**.
- Short: **verified borrowable USDC spot-margin short only**.
- No futures/perpetual execution.
- OI/funding/liquidations and other derivatives data are context/scoring enrichment only; missing derivatives data must not be a hard execution gate.

## Safety of the product rename

The product name is **Bybit EU Trading Intelligence & Execution Platform**.

Existing technical identifiers are intentionally frozen for compatibility and operational safety. In particular, the GitHub repository name, Python package paths, API routes, Railway project/service identifiers, environment variables, database objects, workflow names and historical strategy/research versions are **not renamed solely for branding**.

See [`docs/PRODUCT_DIRECTION.md`](docs/PRODUCT_DIRECTION.md) for the development roadmap and promotion rules.
