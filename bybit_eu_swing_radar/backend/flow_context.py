"""Day-trade derivatives flow context v0.7.2.2.

Context-only enrichment. This module does NOT change the v0.7.2 STRICT gates,
trade decisions, entries, stops, targets, journal logic, or historical replay.

Primary source: Bybit global public linear-derivatives market data.
Secondary source: the already-cached Coinalyze payload from the live day setup,
when present. Neither source is Bybit EU spot execution data.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo


BUDAPEST = ZoneInfo("Europe/Budapest")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def pct_change(current: float, previous: float) -> float | None:
    if previous == 0 or not math.isfinite(current) or not math.isfinite(previous):
        return None
    return (current / previous - 1.0) * 100.0


def oi_change_for_horizon(
    history: Iterable[dict[str, Any]],
    horizon_minutes: int,
) -> float | None:
    rows = []
    for row in history:
        ts = int(safe_float(row.get("timestamp"), 0.0))
        oi = safe_float(row.get("openInterest"), 0.0)
        if ts > 0 and oi > 0:
            rows.append((ts, oi))
    if len(rows) < 2:
        return None
    rows.sort(key=lambda x: x[0])
    latest_ts, latest_oi = rows[-1]
    target_ts = latest_ts - horizon_minutes * 60_000
    previous = None
    for ts, oi in rows:
        if ts <= target_ts:
            previous = oi
        else:
            break
    if previous is None:
        return None
    return pct_change(latest_oi, previous)


def classify_flow(
    price_change_pct: float | None,
    oi_change_pct: float | None,
    *,
    price_epsilon_pct: float = 0.10,
    oi_epsilon_pct: float = 0.25,
) -> str:
    if price_change_pct is None or oi_change_pct is None:
        return "INSUFFICIENT_DATA"
    price_up = price_change_pct > price_epsilon_pct
    price_down = price_change_pct < -price_epsilon_pct
    oi_up = oi_change_pct > oi_epsilon_pct
    oi_down = oi_change_pct < -oi_epsilon_pct
    if price_down and oi_down:
        return "PRICE_DOWN_OI_DOWN_DELEVERAGING"
    if price_up and oi_down:
        return "PRICE_UP_OI_DOWN_COVERING_OR_CLOSING"
    if price_down and oi_up:
        return "PRICE_DOWN_OI_UP_POSITION_BUILD"
    if price_up and oi_up:
        return "PRICE_UP_OI_UP_POSITION_BUILD"
    return "MIXED_OR_LOW_SIGNAL"


def funding_sign(rate: float | None, neutral_abs: float = 1e-8) -> str:
    if rate is None:
        return "UNKNOWN"
    if rate > neutral_abs:
        return "POSITIVE"
    if rate < -neutral_abs:
        return "NEGATIVE"
    return "NEUTRAL"


def build_flow_payload(
    *,
    spot_symbol: str,
    setup_payload: dict[str, Any],
    derivative_instrument: dict[str, Any] | None,
    derivative_ticker: dict[str, Any] | None,
    oi_history: list[dict[str, Any]] | None,
    generated_at: datetime | None = None,
    price_epsilon_pct: float = 0.10,
    oi_epsilon_pct: float = 0.25,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    metrics = setup_payload.get("metrics") or {}
    price_15m = _nullable_float(metrics.get("return_15m_pct"))
    price_1h = _nullable_float(metrics.get("return_1h_pct"))
    price_4h = _nullable_float(metrics.get("return_4h_pct"))
    existing_coinalyze = setup_payload.get("derivatives") or {}
    spot_age_seconds = _snapshot_age_seconds(setup_payload.get("data_as_of"), now)
    spot_is_stale = spot_age_seconds is not None and spot_age_seconds > 300

    if not derivative_instrument or not derivative_ticker:
        return {
            "strategy_mode": "DAY_TRADE",
            "strategy_version": "0.7.2",
            "feature_version": "0.7.2.2",
            "symbol": spot_symbol,
            "data_as_of": now.isoformat(),
            "data_as_of_budapest": now.astimezone(BUDAPEST).isoformat(),
            "spot_snapshot_as_of": setup_payload.get("data_as_of"),
            "spot_snapshot_age_seconds": spot_age_seconds,
            "data_quality": "DEGRADED" if spot_is_stale else "PARTIAL",
            "coverage_status": "STALE_SPOT_CONTEXT" if spot_is_stale else "NO_BYBIT_GLOBAL_LINEAR_PERPETUAL_MATCH",
            "bybit_global_derivatives": {},
            "coinalyze_existing": existing_coinalyze,
            "interpretation": {"flow_15m": "INSUFFICIENT_DATA", "flow_1h": "INSUFFICIENT_DATA"},
            "notes": _notes(),
        }

    history = oi_history or []
    oi_changes = {
        "change_5m_pct": oi_change_for_horizon(history, 5),
        "change_15m_pct": oi_change_for_horizon(history, 15),
        "change_1h_pct": oi_change_for_horizon(history, 60),
        "change_4h_pct": oi_change_for_horizon(history, 240),
    }
    current_oi = _nullable_float(derivative_ticker.get("openInterest"))
    current_oi_value = _nullable_float(derivative_ticker.get("openInterestValue"))
    funding = _nullable_float(derivative_ticker.get("fundingRate"))
    funding_pct = funding * 100.0 if funding is not None else None
    next_funding_ms = int(safe_float(derivative_ticker.get("nextFundingTime"), 0.0))
    next_funding = (
        datetime.fromtimestamp(next_funding_ms / 1000, tz=timezone.utc).isoformat()
        if next_funding_ms > 0 else None
    )

    flow_15m = classify_flow(
        price_15m, oi_changes["change_15m_pct"],
        price_epsilon_pct=price_epsilon_pct,
        oi_epsilon_pct=oi_epsilon_pct,
    )
    flow_1h = classify_flow(
        price_1h, oi_changes["change_1h_pct"],
        price_epsilon_pct=price_epsilon_pct,
        oi_epsilon_pct=oi_epsilon_pct,
    )

    return {
        "strategy_mode": "DAY_TRADE",
        "strategy_version": "0.7.2",
        "feature_version": "0.7.2.2",
        "symbol": spot_symbol,
        "data_as_of": now.isoformat(),
        "data_as_of_budapest": now.astimezone(BUDAPEST).isoformat(),
        "spot_snapshot_as_of": setup_payload.get("data_as_of"),
        "spot_snapshot_age_seconds": spot_age_seconds,
        "data_quality": "DEGRADED" if spot_is_stale else ("GOOD" if history else "PARTIAL"),
        "coverage_status": "STALE_SPOT_CONTEXT" if spot_is_stale else ("GOOD" if history else "PARTIAL"),
        "bybit_global_derivatives": {
            "source": "Bybit global public V5 linear derivatives",
            "scope": "DERIVATIVES_CONTEXT_NOT_BYBIT_EU_SPOT",
            "symbol": derivative_instrument.get("symbol"),
            "base_coin": derivative_instrument.get("baseCoin"),
            "quote_coin": derivative_instrument.get("quoteCoin"),
            "contract_type": derivative_instrument.get("contractType"),
            "funding_interval_minutes": _nullable_int(derivative_instrument.get("fundingInterval")),
            "last_price": _nullable_float(derivative_ticker.get("lastPrice")),
            "mark_price": _nullable_float(derivative_ticker.get("markPrice")),
            "open_interest_size": current_oi,
            "open_interest_value_quote": current_oi_value,
            "open_interest": oi_changes,
            "funding_rate_decimal": funding,
            "funding_rate_pct": funding_pct,
            "funding_sign": funding_sign(funding),
            "next_funding_time": next_funding,
            "turnover_24h": _nullable_float(derivative_ticker.get("turnover24h")),
        },
        "spot_context": {
            "last_price": _nullable_float(setup_payload.get("last_price")),
            "return_15m_pct": price_15m,
            "return_1h_pct": price_1h,
            "return_4h_pct": price_4h,
            "volume_ratio_5m": _nullable_float(metrics.get("volume_ratio_5m")),
            "volume_ratio_15m": _nullable_float(metrics.get("volume_ratio_15m")),
        },
        "coinalyze_existing": existing_coinalyze,
        "interpretation": {
            "flow_15m": flow_15m,
            "flow_1h": flow_1h,
            "deleveraging_down_15m": flow_15m == "PRICE_DOWN_OI_DOWN_DELEVERAGING",
            "covering_or_closing_up_15m": flow_15m == "PRICE_UP_OI_DOWN_COVERING_OR_CLOSING",
            "position_build_with_price_down_15m": flow_15m == "PRICE_DOWN_OI_UP_POSITION_BUILD",
            "position_build_with_price_up_15m": flow_15m == "PRICE_UP_OI_UP_POSITION_BUILD",
            "price_epsilon_pct": price_epsilon_pct,
            "oi_epsilon_pct": oi_epsilon_pct,
        },
        "notes": _notes(),
    }


def _snapshot_age_seconds(value: Any, now: datetime) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((now - dt.astimezone(timezone.utc)).total_seconds(), 0.0)


def _nullable_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _nullable_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _notes() -> list[str]:
    return [
        "Flow context is informational only and does not change v0.7.2 STRICT gates or trade decisions.",
        "Bybit global derivatives are not Bybit EU spot or Bybit EU spot-margin execution data.",
        "Open-interest direction does not identify which side opened or closed; use price action and funding as context, not proof.",
        "coinalyze_existing is the already-cached secondary derivatives payload and is not Bybit EU-specific unless explicitly marked.",
    ]
