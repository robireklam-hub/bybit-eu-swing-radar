"""Day-trade v0.7.6 setup/entry state helpers.

The live v0.7.6 contract separates three questions that v0.7.5 mixed together:

1. Is there a technically valid directional setup?
2. Is the side executable on Bybit EU under the USDC-only rules?
3. Is there an acceptable entry *now* after fresh stop/target/RR/barrier geometry?

A structural breakout context has no fixed 5m-bar TTL. It remains contextually
active while every subsequently closed 5m bar continues to hold the original
range boundary. Entry confirmation is a separate concern; this module does not
turn intrabar/provisional acceptance into an executable TRADE decision.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence


def _iso_from_ms(value: int) -> str:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def active_structural_breakout_context(
    bars_5m: Sequence[Any],
    side: str,
    *,
    lookback_bars: int = 12,
) -> dict[str, Any] | None:
    """Return the active origin of the current uninterrupted range breakout.

    Unlike the v0.7.5 trigger helper, this is *setup context*, not an execution
    trigger. There is intentionally no fixed age/TTL in bars.

    The first genuine crossing in an uninterrupted breakout sequence owns the
    setup origin. Later continuation bars may cross newer rolling 12-bar highs or
    lows, but they must not ratchet the trigger/origin forward. The active event
    dies as soon as any later closed 5m bar loses the original boundary. A later
    genuine crossing may then start a new, independent setup.
    """
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    if lookback_bars < 2 or len(bars_5m) < lookback_bars + 1:
        return None

    active: dict[str, Any] | None = None
    latest_index = len(bars_5m) - 1

    for index in range(lookback_bars, len(bars_5m)):
        current_close = float(bars_5m[index].close)

        if active is not None:
            boundary = float(active["trigger_price"])
            boundary_held = (
                current_close > boundary if side == "long" else current_close < boundary
            )
            if boundary_held:
                # Preserve the original event even if this continuation bar also
                # crosses a newer rolling range boundary. No trigger ratcheting.
                continue
            active = None

        prior = bars_5m[index - lookback_bars:index]
        boundary = (
            max(float(bar.high) for bar in prior)
            if side == "long"
            else min(float(bar.low) for bar in prior)
        )
        previous_close = float(bars_5m[index - 1].close)
        event_bar = bars_5m[index]
        event_close = float(event_bar.close)
        crossed = (
            event_close > boundary and previous_close <= boundary
            if side == "long"
            else event_close < boundary and previous_close >= boundary
        )
        if not crossed:
            continue

        active = {
            "trigger_price": boundary,
            "event_index": index,
            "event_bar_start_ms": int(event_bar.start_ms),
            "event_bar_time": _iso_from_ms(int(event_bar.start_ms)),
            "event_close": event_close,
            "trigger_window_start_ms": int(prior[0].start_ms),
        }

    if active is None:
        return None

    event_index = int(active.pop("event_index"))
    return {
        **active,
        "age_bars": latest_index - event_index,
        "validity_bars": None,
        "boundary_held": True,
        "boundary_held_through_all_closed_bars": True,
        "persistence_mode": "STRUCTURE_HELD_NO_FIXED_BAR_TTL",
        "origin_policy": "FIRST_CROSSING_IN_UNINTERRUPTED_SEQUENCE_NO_RATCHET",
    }


def technical_setup_valid(
    *,
    setup_score: float,
    expansion_score: float,
    side_direction_score: float,
    quality_score: float,
    minimum_setup_score: float,
    minimum_expansion_score: float,
    minimum_direction_score: float,
    minimum_quality_score: float,
) -> bool:
    """Technical setup validity deliberately excludes RR/barriers/execution."""
    return bool(
        setup_score >= minimum_setup_score
        and expansion_score >= minimum_expansion_score
        and side_direction_score >= minimum_direction_score
        and quality_score >= minimum_quality_score
    )


def fresh_entry_zone(
    *,
    current_price: float,
    atr_5m: float,
    side: str,
    width_atr: float = 0.15,
) -> tuple[float, float]:
    """Create a fresh reference entry zone from current price, never stale origin."""
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    if current_price <= 0 or atr_5m <= 0 or width_atr < 0:
        raise ValueError("current_price/atr_5m must be positive and width_atr non-negative")
    width = atr_5m * width_atr
    if side == "long":
        return current_price, current_price + width
    return current_price - width, current_price


def hard_stop_contract(*, stop_price: float, side: str) -> dict[str, Any]:
    """Hard risk stop is price-touch/cross based; no 5m candle close is required."""
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    return {
        "price": float(stop_price),
        "activation": "INTRABAR_TOUCH_OR_CROSS",
        "requires_candle_close": False,
        "condition": (
            "price <= hard stop" if side == "long" else "price >= hard stop"
        ),
    }


def classify_entry_state(
    *,
    setup_valid: bool,
    execution_valid: bool,
    rr_valid: bool,
    target_path_valid: bool,
    barrier_blocked: bool,
    confirmed_trigger: bool,
    persistent_breakout_context: bool,
    extension_atr: float,
    max_provisional_extension_atr: float = 1.0,
) -> str:
    """Classify entry readiness without erasing an otherwise valid setup."""
    if not setup_valid:
        return "NO_SETUP"
    if not execution_valid:
        return "EXECUTION_BLOCKED"
    if barrier_blocked:
        return "BLOCKED_BY_BARRIER"
    if not rr_valid or not target_path_valid:
        return "RR_NOT_READY"
    if confirmed_trigger:
        return "ENTRY_CONFIRMED"
    if persistent_breakout_context:
        if extension_atr <= max_provisional_extension_atr:
            return "ENTRY_PROVISIONAL"
        return "ENTRY_TOO_EXTENDED"
    return "WAIT_TRIGGER"


def setup_state_from_validity(valid: bool) -> str:
    return "VALID" if valid else "INVALID"
