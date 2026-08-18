"""Frozen, descriptive derivatives-positioning research classifier.

This module is label-free and research-only. It classifies already-observed
price/OI/funding/liquidation context and never changes strategy scoring,
eligibility, execution, entries, stops, targets, or trade decisions.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SPEC_VERSION = "derivatives-positioning-shadow-v1"
FUNDING_CROWDING_ABS = 0.0001  # 0.01% per funding interval
LIQUIDATION_SKEW_ABS = 0.35

POSITIONING_STATES = (
    "LONG_BUILD",
    "SHORT_BUILD",
    "LONG_DELEVERAGING",
    "SHORT_COVERING",
    "MIXED",
    "INSUFFICIENT_DATA",
)
CROWDING_STATES = (
    "POSITIVE_CROWDED",
    "NEGATIVE_CROWDED",
    "NEUTRAL",
    "UNKNOWN",
)
LIQUIDATION_STATES = (
    "LONG_LIQ_DOMINANT",
    "SHORT_LIQ_DOMINANT",
    "BALANCED",
    "UNAVAILABLE",
)
INTERACTIONS = (
    "TREND_ALIGNED_BUILD",
    "TREND_OPPOSED_BUILD",
    "COMPRESSION_POSITION_BUILD",
    "RANGE_CROWDING",
    "VOLATILITY_UNWIND",
    "OBSERVATION",
)

FLOW_TO_POSITIONING = {
    "PRICE_UP_OI_UP_POSITION_BUILD": "LONG_BUILD",
    "PRICE_DOWN_OI_UP_POSITION_BUILD": "SHORT_BUILD",
    "PRICE_DOWN_OI_DOWN_DELEVERAGING": "LONG_DELEVERAGING",
    "PRICE_UP_OI_DOWN_COVERING_OR_CLOSING": "SHORT_COVERING",
    "MIXED_OR_LOW_SIGNAL": "MIXED",
    "INSUFFICIENT_DATA": "INSUFFICIENT_DATA",
}


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "execution_scope": "NO_EXECUTION_USE",
        "inputs": {
            "price_oi_flow": "existing day_trade_flow cache; Bybit global public linear derivatives plus USDC spot context",
            "funding": "existing day_trade_flow cache; Bybit global public linear derivatives",
            "liquidations": "liquidation-context-shadow-v1 forward snapshot; Coinalyze derivatives context only",
            "market_regime": "market-regime-shadow-v1 forward snapshot",
        },
        "thresholds": {
            "funding_crowding_abs_decimal": FUNDING_CROWDING_ABS,
            "liquidation_skew_abs": LIQUIDATION_SKEW_ABS,
        },
        "positioning_states": list(POSITIONING_STATES),
        "crowding_states": list(CROWDING_STATES),
        "liquidation_states": list(LIQUIDATION_STATES),
        "interactions": list(INTERACTIONS),
        "notes": [
            "Open-interest direction does not identify which side opened or closed; positioning labels combine observed price and OI direction only.",
            "Funding and liquidation data are derivatives context, never Bybit EU spot execution proof.",
            "Missing liquidation data is explicit coverage loss, not a hard gate.",
            "No outcome, journal, net-R, or post-trade label is read by this classifier.",
        ],
    }


def _nullable_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def funding_crowding(rate: float | None) -> str:
    if rate is None:
        return "UNKNOWN"
    if rate >= FUNDING_CROWDING_ABS:
        return "POSITIVE_CROWDED"
    if rate <= -FUNDING_CROWDING_ABS:
        return "NEGATIVE_CROWDED"
    return "NEUTRAL"


def liquidation_skew(long_liq: float | None, short_liq: float | None) -> dict[str, Any]:
    if long_liq is None or short_liq is None:
        return {
            "state": "UNAVAILABLE",
            "skew": None,
            "long_liquidations_usd": long_liq,
            "short_liquidations_usd": short_liq,
            "total_liquidations_usd": None,
        }
    long_value = max(long_liq, 0.0)
    short_value = max(short_liq, 0.0)
    total = long_value + short_value
    skew = 0.0 if total <= 0 else (long_value - short_value) / total
    if skew >= LIQUIDATION_SKEW_ABS:
        state = "LONG_LIQ_DOMINANT"
    elif skew <= -LIQUIDATION_SKEW_ABS:
        state = "SHORT_LIQ_DOMINANT"
    else:
        state = "BALANCED"
    return {
        "state": state,
        "skew": skew,
        "long_liquidations_usd": long_value,
        "short_liquidations_usd": short_value,
        "total_liquidations_usd": total,
    }


def _find_liquidations(value: Any) -> tuple[float | None, float | None]:
    """Find cached Coinalyze liquidation totals without depending on cache schema."""
    if isinstance(value, Mapping):
        long_value = _nullable_float(value.get("long_liquidations_24h_usd"))
        short_value = _nullable_float(value.get("short_liquidations_24h_usd"))
        if long_value is not None or short_value is not None:
            return long_value, short_value
        for nested in value.values():
            found = _find_liquidations(nested)
            if found != (None, None):
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_liquidations(nested)
            if found != (None, None):
                return found
    return None, None


def _interaction(regime: str, direction: str, positioning: str, crowding: str) -> str:
    regime = regime.upper()
    direction = direction.upper()
    if regime == "COMPRESSION" and positioning in {"LONG_BUILD", "SHORT_BUILD"}:
        return "COMPRESSION_POSITION_BUILD"
    if regime == "RANGE" and crowding in {"POSITIVE_CROWDED", "NEGATIVE_CROWDED"}:
        return "RANGE_CROWDING"
    if regime in {"EXPANSION", "HIGH_VOL_STRESS"} and positioning in {
        "LONG_DELEVERAGING", "SHORT_COVERING"
    }:
        return "VOLATILITY_UNWIND"
    if regime == "TREND" and positioning in {"LONG_BUILD", "SHORT_BUILD"}:
        aligned = (direction == "BULL" and positioning == "LONG_BUILD") or (
            direction == "BEAR" and positioning == "SHORT_BUILD"
        )
        return "TREND_ALIGNED_BUILD" if aligned else "TREND_OPPOSED_BUILD"
    return "OBSERVATION"


def classify_symbol(
    symbol: str,
    flow_payload: Mapping[str, Any] | None,
    regime_payload: Mapping[str, Any] | None,
    liquidation_context: Any = None,
) -> dict[str, Any]:
    flow = dict(flow_payload or {})
    regime = dict(regime_payload or {})
    interpretation = flow.get("interpretation") or {}
    derivatives = flow.get("bybit_global_derivatives") or {}
    spot_context = flow.get("spot_context") or {}

    flow_15m = str(interpretation.get("flow_15m") or "INSUFFICIENT_DATA")
    positioning = FLOW_TO_POSITIONING.get(flow_15m, "INSUFFICIENT_DATA")
    funding_rate = _nullable_float(derivatives.get("funding_rate_decimal"))
    crowding = funding_crowding(funding_rate)

    long_liq, short_liq = _find_liquidations(flow.get("coinalyze_existing"))
    if long_liq is None and short_liq is None:
        long_liq, short_liq = _find_liquidations(liquidation_context)
    liq = liquidation_skew(long_liq, short_liq)

    regime_name = str(regime.get("regime") or "UNKNOWN").upper()
    regime_direction = str(regime.get("direction") or "UNKNOWN").upper()
    interaction = _interaction(regime_name, regime_direction, positioning, crowding)

    oi = derivatives.get("open_interest") or {}
    return {
        "symbol": symbol.upper(),
        "positioning_state": positioning,
        "flow_15m": flow_15m,
        "flow_1h": str(interpretation.get("flow_1h") or "INSUFFICIENT_DATA"),
        "price_change_15m_pct": _nullable_float(spot_context.get("return_15m_pct")),
        "price_change_1h_pct": _nullable_float(spot_context.get("return_1h_pct")),
        "oi_change_5m_pct": _nullable_float(oi.get("change_5m_pct")),
        "oi_change_15m_pct": _nullable_float(oi.get("change_15m_pct")),
        "oi_change_1h_pct": _nullable_float(oi.get("change_1h_pct")),
        "oi_change_4h_pct": _nullable_float(oi.get("change_4h_pct")),
        "open_interest_value_quote": _nullable_float(derivatives.get("open_interest_value_quote")),
        "funding_rate_decimal": funding_rate,
        "funding_crowding": crowding,
        "liquidations": liq,
        "market_regime": regime_name,
        "market_direction": regime_direction,
        "regime_interaction": interaction,
        "coverage": {
            "flow": bool(flow),
            "flow_status": flow.get("coverage_status"),
            "funding": funding_rate is not None,
            "liquidations": liq["state"] != "UNAVAILABLE",
            "market_regime": regime_name != "UNKNOWN",
        },
        "derivatives_context_only": True,
        "execution_proof": False,
    }


def build_snapshot(
    rows: Iterable[Mapping[str, Any]],
    *,
    captured_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    items = [dict(row) for row in rows]
    now = captured_at or datetime.now(timezone.utc)
    positioning_counts = Counter(str(row.get("positioning_state")) for row in items)
    crowding_counts = Counter(str(row.get("funding_crowding")) for row in items)
    interaction_counts = Counter(str(row.get("regime_interaction")) for row in items)
    liquidation_covered = sum(
        1 for row in items if (row.get("coverage") or {}).get("liquidations") is True
    )
    flow_covered = sum(1 for row in items if (row.get("coverage") or {}).get("flow") is True)
    regime_covered = sum(
        1 for row in items if (row.get("coverage") or {}).get("market_regime") is True
    )
    return {
        "spec_version": SPEC_VERSION,
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "source_commit_sha": source_commit_sha,
        "research_only": True,
        "label_free": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "symbol_count": len(items),
        "coverage": {
            "flow": flow_covered,
            "market_regime": regime_covered,
            "liquidations": liquidation_covered,
            "total": len(items),
        },
        "positioning_counts": dict(positioning_counts),
        "crowding_counts": dict(crowding_counts),
        "interaction_counts": dict(interaction_counts),
        "symbols": {str(row.get("symbol")): row for row in items},
    }
