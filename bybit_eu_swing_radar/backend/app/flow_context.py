"""Bybit EU Trading Radar v0.7.2.2 — derivatives flow context helpers.

IMPORTANT:
- Strategy version remains v0.7.2.
- This module is context-only. It must not alter STRICT/WATCH gates,
  execution eligibility, trigger/stop/TP logic, or borrowability rules.
- Bybit global derivatives and Coinalyze are NOT Bybit EU spot execution data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

STRATEGY_VERSION = "0.7.2"
FEATURE_VERSION = "0.7.2.2"
DERIVATIVES_SCOPE = "DERIVATIVES_CONTEXT_NOT_BYBIT_EU_SPOT"


def safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def iso_from_ms(value: Any) -> str | None:
    number = safe_float(value)
    if number is None or number <= 0:
        return None
    return datetime.fromtimestamp(number / 1000.0, tz=timezone.utc).isoformat()


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _sorted_numeric_history(
    rows: Iterable[dict[str, Any]],
    *,
    timestamp_key: str,
    value_key: str,
) -> list[tuple[int, float]]:
    result: list[tuple[int, float]] = []
    for row in rows:
        ts = safe_float(row.get(timestamp_key))
        value = safe_float(row.get(value_key))
        if ts is None or value is None:
            continue
        result.append((int(ts), value))
    result.sort(key=lambda item: item[0])
    return result


def history_change_pct(
    rows: Iterable[dict[str, Any]],
    horizon_minutes: int,
    *,
    timestamp_key: str,
    value_key: str,
) -> float | None:
    points = _sorted_numeric_history(
        rows,
        timestamp_key=timestamp_key,
        value_key=value_key,
    )
    if len(points) < 2:
        return None

    current_ts, current_value = points[-1]
    target_ts = current_ts - horizon_minutes * 60_000

    previous: float | None = None
    for ts, value in points:
        if ts <= target_ts:
            previous = value
        else:
            break

    if previous is None:
        return None
    return pct_change(current_value, previous)


def calculate_history_changes(
    rows: Iterable[dict[str, Any]],
    *,
    timestamp_key: str,
    value_key: str,
) -> dict[str, float | None]:
    materialized = list(rows)
    return {
        "change_5m_pct": history_change_pct(
            materialized, 5, timestamp_key=timestamp_key, value_key=value_key
        ),
        "change_15m_pct": history_change_pct(
            materialized, 15, timestamp_key=timestamp_key, value_key=value_key
        ),
        "change_1h_pct": history_change_pct(
            materialized, 60, timestamp_key=timestamp_key, value_key=value_key
        ),
        "change_4h_pct": history_change_pct(
            materialized, 240, timestamp_key=timestamp_key, value_key=value_key
        ),
    }


def classify_price_oi_state(
    price_change_pct: float | None,
    oi_change_pct: float | None,
    *,
    price_epsilon_pct: float = 0.10,
    oi_epsilon_pct: float = 0.25,
) -> str:
    if price_change_pct is None or oi_change_pct is None:
        return "MIXED_OR_LOW_SIGNAL"
    if abs(price_change_pct) < price_epsilon_pct:
        return "MIXED_OR_LOW_SIGNAL"
    if abs(oi_change_pct) < oi_epsilon_pct:
        return "MIXED_OR_LOW_SIGNAL"

    if price_change_pct < 0 and oi_change_pct < 0:
        return "PRICE_DOWN_OI_DOWN_DELEVERAGING"
    if price_change_pct > 0 and oi_change_pct < 0:
        return "PRICE_UP_OI_DOWN_COVERING_OR_CLOSING"
    if price_change_pct < 0 and oi_change_pct > 0:
        return "PRICE_DOWN_OI_UP_POSITION_BUILD"
    if price_change_pct > 0 and oi_change_pct > 0:
        return "PRICE_UP_OI_UP_POSITION_BUILD"
    return "MIXED_OR_LOW_SIGNAL"


def funding_sign(rate: float | None) -> str:
    if rate is None or abs(rate) < 1e-15:
        return "ZERO_OR_UNAVAILABLE"
    return "POSITIVE" if rate > 0 else "NEGATIVE"


def spot_snapshot_age_seconds(
    setup_payload: dict[str, Any],
    fallback_updated_at: datetime | None,
    now: datetime | None = None,
) -> float | None:
    now = now or datetime.now(timezone.utc)
    source_time = parse_datetime(setup_payload.get("data_as_of"))
    if source_time is None:
        source_time = fallback_updated_at
    if source_time is None:
        return None
    if source_time.tzinfo is None:
        source_time = source_time.replace(tzinfo=timezone.utc)
    return max(0.0, (now - source_time).total_seconds())


def build_context_payload(
    *,
    setup: dict[str, Any],
    cache_updated_at: datetime | None,
    derivative_symbol: str | None,
    ticker: dict[str, Any] | None,
    oi_history: list[dict[str, Any]] | None,
    kline_history: list[dict[str, Any]] | None,
    price_epsilon_pct: float,
    oi_epsilon_pct: float,
    generated_at: datetime | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    generated_at = generated_at or datetime.now(timezone.utc)
    symbol = str(setup.get("symbol") or "").upper()
    base_asset = str(setup.get("base_asset") or symbol.removesuffix("USDC")).upper()

    age_seconds = spot_snapshot_age_seconds(setup, cache_updated_at, generated_at)
    stale_spot = age_seconds is None or age_seconds > 300.0

    ticker = ticker or {}
    oi_history = oi_history or []
    kline_history = kline_history or []

    oi_changes = calculate_history_changes(
        oi_history,
        timestamp_key="timestamp",
        value_key="openInterest",
    )
    price_changes = calculate_history_changes(
        kline_history,
        timestamp_key="timestamp",
        value_key="close",
    )

    funding_rate = safe_float(ticker.get("fundingRate"))
    funding = {
        "funding_rate_decimal": funding_rate,
        "funding_rate_pct": (funding_rate * 100.0) if funding_rate is not None else None,
        "funding_sign": funding_sign(funding_rate),
        "next_funding_time": iso_from_ms(ticker.get("nextFundingTime")),
    }

    price_oi_state = {
        "state_5m": classify_price_oi_state(
            price_changes["change_5m_pct"],
            oi_changes["change_5m_pct"],
            price_epsilon_pct=price_epsilon_pct,
            oi_epsilon_pct=oi_epsilon_pct,
        ),
        "state_15m": classify_price_oi_state(
            price_changes["change_15m_pct"],
            oi_changes["change_15m_pct"],
            price_epsilon_pct=price_epsilon_pct,
            oi_epsilon_pct=oi_epsilon_pct,
        ),
        "state_1h": classify_price_oi_state(
            price_changes["change_1h_pct"],
            oi_changes["change_1h_pct"],
            price_epsilon_pct=price_epsilon_pct,
            oi_epsilon_pct=oi_epsilon_pct,
        ),
        "state_4h": classify_price_oi_state(
            price_changes["change_4h_pct"],
            oi_changes["change_4h_pct"],
            price_epsilon_pct=price_epsilon_pct,
            oi_epsilon_pct=oi_epsilon_pct,
        ),
    }

    missing_data: list[str] = []
    if derivative_symbol is None:
        missing_data.append("NO_MATCHING_GLOBAL_LINEAR_DERIVATIVE")
    if not oi_history:
        missing_data.append("OPEN_INTEREST_HISTORY_UNAVAILABLE")
    if not kline_history:
        missing_data.append("DERIVATIVE_PRICE_HISTORY_UNAVAILABLE")
    if funding_rate is None:
        missing_data.append("FUNDING_UNAVAILABLE")
    if stale_spot:
        missing_data.append("STALE_SPOT_CONTEXT")
    if error:
        missing_data.append(f"FLOW_FETCH_ERROR:{error}")

    if stale_spot or error:
        data_quality = "DEGRADED"
    elif derivative_symbol is None or missing_data:
        data_quality = "PARTIAL"
    else:
        data_quality = "GOOD"

    current_oi = safe_float(ticker.get("openInterest"))
    current_oi_value = safe_float(ticker.get("openInterestValue"))

    derivatives_context = {
        "scope": DERIVATIVES_SCOPE,
        "matched": derivative_symbol is not None,
        "symbol": derivative_symbol,
        "category": "linear" if derivative_symbol else None,
        "last_price": safe_float(ticker.get("lastPrice")),
        "mark_price": safe_float(ticker.get("markPrice")),
        "index_price": safe_float(ticker.get("indexPrice")),
        "open_interest": {
            "current_size": current_oi,
            "current_value": current_oi_value,
            **oi_changes,
        },
        "price_change": price_changes,
        "price_oi_state": price_oi_state,
        "funding": funding,
    }

    return {
        "strategy_mode": "DAY_TRADE",
        "strategy_version": STRATEGY_VERSION,
        "feature_version": FEATURE_VERSION,
        "symbol": symbol,
        "base_asset": base_asset,
        "quote_asset": "USDC",
        "generated_at": generated_at.isoformat(),
        "spot_snapshot_data_as_of": setup.get("data_as_of"),
        "spot_snapshot_age_seconds": (
            round(age_seconds, 3) if age_seconds is not None else None
        ),
        "data_quality": data_quality,
        "missing_data": missing_data,
        "spot_execution": {
            "venue": "Bybit EU",
            "instrument": symbol,
            "market_type": "spot",
            "long_execution": "USDC_SPOT",
            "short_execution": "USDC_SPOT_MARGIN_ONLY_IF_BORROWABLE",
            "shortable": bool(setup.get("shortable", False)),
            "tradeable": bool(setup.get("tradeable", False)),
            "execution_status": setup.get("execution_status"),
            "execution_modes": list(setup.get("execution_modes") or []),
            "scope": "BYBIT_EU_SPOT_EXECUTION_SOURCE_OF_TRUTH",
        },
        "bybit_global_derivatives": derivatives_context,
        "coinalyze_context": dict(setup.get("derivatives") or {}),
        "context_only": True,
        "hard_gate": False,
        "score_influence_mode": "CONTEXT_ONLY_V0722_FLOW_LAYER",
        "notes": [
            "OI/funding is context enrichment only and is not an execution gate.",
            "Bybit global derivatives and Coinalyze do not prove Bybit EU spot or spot-margin execution availability.",
            "Short execution remains valid only when the Bybit EU USDC spot-margin instrument is borrowable/shortable.",
        ],
    }


def _self_test() -> None:
    oi = [
        {"timestamp": 0, "openInterest": "100"},
        {"timestamp": 300_000, "openInterest": "110"},
        {"timestamp": 900_000, "openInterest": "121"},
        {"timestamp": 3_600_000, "openInterest": "133.1"},
        {"timestamp": 14_400_000, "openInterest": "146.41"},
    ]
    changes = calculate_history_changes(
        oi, timestamp_key="timestamp", value_key="openInterest"
    )
    assert round(changes["change_5m_pct"] or 0, 6) == round((146.41 / 133.1 - 1) * 100, 6)
    assert classify_price_oi_state(-1.0, -1.0) == "PRICE_DOWN_OI_DOWN_DELEVERAGING"
    assert classify_price_oi_state(1.0, -1.0) == "PRICE_UP_OI_DOWN_COVERING_OR_CLOSING"
    assert classify_price_oi_state(-1.0, 1.0) == "PRICE_DOWN_OI_UP_POSITION_BUILD"
    assert classify_price_oi_state(1.0, 1.0) == "PRICE_UP_OI_UP_POSITION_BUILD"


if __name__ == "__main__":
    _self_test()
    print("flow_context self-test: PASS")
