"""Trading Radar — liquidity sweep research detector v0.1.

RESEARCH ONLY.
This module does not modify the live v0.7.2 day-trade strategy.

Purpose
-------
Detect a deterministic sequence on CLOSED 5m bars:

    liquidity sweep
    -> reclaim of swept level
    -> 5m local structure shift
    -> 15m non-opposing / confirming structure
    -> optional volume confirmation annotation

The detector deliberately does NOT:
- decide STRICT/WATCH/TRADE;
- check Bybit EU execution eligibility or borrowability;
- use 4H as a gate;
- use OI/funding as a gate or score input;
- modify stops/targets in day_worker.py.

Default research parameters
---------------------------
- liquidity lookback:        12 x 5m = 60 minutes
- minimum sweep depth:       0.10 ATR(14, 5m)
- maximum sweep depth:       1.00 ATR(14, 5m)
- reclaim window:            sweep bar + next 3 closed 5m bars
- 5m structure lookback:     6 bars immediately before the sweep
- max sweep->confirmation:   6 bars = 30 minutes
- volume confirmation:       confirmation bar >= 1.30x prior 20-bar mean volume
- 15m confirmation:          latest fully CLOSED 15m structure must not oppose side

The values above are research starting points, not optimized trading rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Sequence

Side = Literal["long", "short"]

FIVE_MIN_MS = 5 * 60 * 1000
FIFTEEN_MIN_MS = 15 * 60 * 1000
RESEARCH_VERSION = "sweep-research-0.1"


@dataclass(frozen=True)
class SweepResearchConfig:
    liquidity_lookback: int = 12
    atr_period: int = 14
    min_sweep_depth_atr: float = 0.10
    max_sweep_depth_atr: float = 1.00
    reclaim_window_bars: int = 3
    structure_lookback_5m: int = 6
    max_confirmation_bars: int = 6
    structure_lookback_15m: int = 3
    volume_lookback: int = 20
    volume_confirmation_ratio: float = 1.30


DEFAULT_CONFIG = SweepResearchConfig()


@dataclass(frozen=True)
class ResearchBar:
    start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float = 0.0


def _bar(value: Any) -> ResearchBar:
    """Convert worker.Bar, dict, or ResearchBar to the local research shape."""
    if isinstance(value, ResearchBar):
        return value
    if isinstance(value, dict):
        return ResearchBar(
            start_ms=int(value["start_ms"]),
            open=float(value["open"]),
            high=float(value["high"]),
            low=float(value["low"]),
            close=float(value["close"]),
            volume=float(value.get("volume", 0.0)),
            turnover=float(value.get("turnover", 0.0)),
        )
    return ResearchBar(
        start_ms=int(value.start_ms),
        open=float(value.open),
        high=float(value.high),
        low=float(value.low),
        close=float(value.close),
        volume=float(value.volume),
        turnover=float(getattr(value, "turnover", 0.0)),
    )


def normalize_bars(values: Iterable[Any]) -> list[ResearchBar]:
    rows = sorted((_bar(value) for value in values), key=lambda item: item.start_ms)
    # De-duplicate by start time; later occurrence wins deterministically.
    deduped: dict[int, ResearchBar] = {row.start_ms: row for row in rows}
    return [deduped[key] for key in sorted(deduped)]


def iso_from_ms(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat()


def _true_range(current: ResearchBar, previous_close: float | None) -> float:
    if previous_close is None:
        return max(0.0, current.high - current.low)
    return max(
        current.high - current.low,
        abs(current.high - previous_close),
        abs(current.low - previous_close),
    )


def atr_at_index(
    bars: Sequence[ResearchBar],
    index: int,
    period: int = 14,
) -> float | None:
    """Simple ATR over the latest `period` true ranges available at index.

    No future bars are used.
    """
    if index < 0 or index >= len(bars) or period <= 0:
        return None
    start = max(0, index - period + 1)
    values: list[float] = []
    for i in range(start, index + 1):
        previous_close = bars[i - 1].close if i > 0 else None
        values.append(_true_range(bars[i], previous_close))
    if len(values) < period:
        return None
    return sum(values) / len(values)


def volume_ratio_at_index(
    bars: Sequence[ResearchBar],
    index: int,
    lookback: int = 20,
) -> float | None:
    if index <= 0 or lookback <= 0:
        return None
    prior = bars[max(0, index - lookback):index]
    if len(prior) < lookback:
        return None
    baseline = sum(row.volume for row in prior) / len(prior)
    if baseline <= 0:
        return None
    return bars[index].volume / baseline


def aggregate_5m_to_15m(values: Iterable[Any]) -> list[ResearchBar]:
    """Aggregate complete, contiguous 5m bars into fully formed 15m bars."""
    bars = normalize_bars(values)
    groups: dict[int, list[ResearchBar]] = {}
    for row in bars:
        bucket = (row.start_ms // FIFTEEN_MIN_MS) * FIFTEEN_MIN_MS
        groups.setdefault(bucket, []).append(row)

    output: list[ResearchBar] = []
    for bucket in sorted(groups):
        rows = sorted(groups[bucket], key=lambda item: item.start_ms)
        expected = [bucket + i * FIVE_MIN_MS for i in range(3)]
        if len(rows) != 3 or [row.start_ms for row in rows] != expected:
            continue
        output.append(
            ResearchBar(
                start_ms=bucket,
                open=rows[0].open,
                high=max(row.high for row in rows),
                low=min(row.low for row in rows),
                close=rows[-1].close,
                volume=sum(row.volume for row in rows),
                turnover=sum(row.turnover for row in rows),
            )
        )
    return output


def _closed_15m_prefix(
    bars_15m: Sequence[ResearchBar],
    confirmation_close_ms: int,
) -> list[ResearchBar]:
    return [
        row
        for row in bars_15m
        if row.start_ms + FIFTEEN_MIN_MS <= confirmation_close_ms
    ]


def classify_15m_structure(
    bars_15m: Sequence[ResearchBar],
    confirmation_close_ms: int,
    lookback: int = 3,
) -> str:
    """Classify the latest fully closed 15m bar versus prior local range.

    Returns:
      BULLISH_SHIFT
      BEARISH_SHIFT
      NEUTRAL_NON_OPPOSING
      INSUFFICIENT_DATA
    """
    closed = _closed_15m_prefix(bars_15m, confirmation_close_ms)
    if len(closed) < lookback + 1:
        return "INSUFFICIENT_DATA"

    current = closed[-1]
    previous = closed[-lookback - 1:-1]
    prior_high = max(row.high for row in previous)
    prior_low = min(row.low for row in previous)

    if current.close > prior_high:
        return "BULLISH_SHIFT"
    if current.close < prior_low:
        return "BEARISH_SHIFT"
    return "NEUTRAL_NON_OPPOSING"


def _15m_confirms(side: Side, state: str) -> bool:
    if state == "INSUFFICIENT_DATA":
        return False
    if side == "long":
        return state != "BEARISH_SHIFT"
    return state != "BULLISH_SHIFT"


def _empty_result(
    side: Side,
    sweep_index: int | None,
    failure_reasons: list[str],
) -> dict[str, Any]:
    return {
        "research_version": RESEARCH_VERSION,
        "research_only": True,
        "side": side,
        "sweep_index": sweep_index,
        "sweep_detected": False,
        "sweep_level": None,
        "sweep_price": None,
        "sweep_depth": None,
        "sweep_depth_atr": None,
        "sweep_time": None,
        "reclaim_confirmed": False,
        "reclaim_close": None,
        "reclaim_time": None,
        "structure_shift_5m": False,
        "structure_shift_level_5m": None,
        "structure_shift_time_5m": None,
        "structure_15m_state": "NOT_EVALUATED",
        "structure_confirmed_15m": False,
        "volume_ratio_5m": None,
        "volume_confirmed": False,
        "bars_from_sweep_to_confirmation": None,
        "candidate_entry": None,
        "candidate_invalidation": None,
        "entry_ready": False,
        "failure_reasons": failure_reasons,
    }


def evaluate_sweep_at_index(
    bars_5m: Iterable[Any],
    sweep_index: int,
    side: Side,
    *,
    bars_15m: Iterable[Any] | None = None,
    config: SweepResearchConfig = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """Evaluate one possible sweep without using future bars before they close.

    The function may inspect bars after `sweep_index` only up to the configured
    reclaim/confirmation windows, because those closed bars are the confirmation
    sequence being researched.
    """
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")

    bars = normalize_bars(bars_5m)
    if sweep_index < 0 or sweep_index >= len(bars):
        return _empty_result(side, sweep_index, ["SWEEP_INDEX_OUT_OF_RANGE"])

    required_history = max(
        config.liquidity_lookback,
        config.structure_lookback_5m,
        config.atr_period,
    )
    if sweep_index < required_history:
        return _empty_result(side, sweep_index, ["INSUFFICIENT_5M_HISTORY"])

    atr_value = atr_at_index(bars, sweep_index, config.atr_period)
    if atr_value is None or atr_value <= 0:
        return _empty_result(side, sweep_index, ["ATR_UNAVAILABLE"])

    sweep_bar = bars[sweep_index]
    liquidity_window = bars[
        sweep_index - config.liquidity_lookback:sweep_index
    ]
    structure_window = bars[
        sweep_index - config.structure_lookback_5m:sweep_index
    ]

    if side == "long":
        sweep_level = min(row.low for row in liquidity_window)
        sweep_price = sweep_bar.low
        sweep_depth = sweep_level - sweep_price
        structure_level = max(row.high for row in structure_window)
        raw_sweep = sweep_price < sweep_level
    else:
        sweep_level = max(row.high for row in liquidity_window)
        sweep_price = sweep_bar.high
        sweep_depth = sweep_price - sweep_level
        structure_level = min(row.low for row in structure_window)
        raw_sweep = sweep_price > sweep_level

    if not raw_sweep:
        return _empty_result(side, sweep_index, ["NO_LIQUIDITY_SWEEP"])

    sweep_depth_atr = sweep_depth / atr_value
    if sweep_depth_atr < config.min_sweep_depth_atr:
        result = _empty_result(side, sweep_index, ["SWEEP_TOO_SHALLOW"])
        result.update({
            "sweep_level": sweep_level,
            "sweep_price": sweep_price,
            "sweep_depth": sweep_depth,
            "sweep_depth_atr": sweep_depth_atr,
            "sweep_time": iso_from_ms(sweep_bar.start_ms),
        })
        return result
    if sweep_depth_atr > config.max_sweep_depth_atr:
        result = _empty_result(side, sweep_index, ["SWEEP_TOO_DEEP"])
        result.update({
            "sweep_level": sweep_level,
            "sweep_price": sweep_price,
            "sweep_depth": sweep_depth,
            "sweep_depth_atr": sweep_depth_atr,
            "sweep_time": iso_from_ms(sweep_bar.start_ms),
        })
        return result

    result = _empty_result(side, sweep_index, [])
    result.update({
        "sweep_detected": True,
        "sweep_level": sweep_level,
        "sweep_price": sweep_price,
        "sweep_depth": sweep_depth,
        "sweep_depth_atr": sweep_depth_atr,
        "sweep_time": iso_from_ms(sweep_bar.start_ms),
        "structure_shift_level_5m": structure_level,
        "candidate_invalidation": sweep_price,
    })

    # Reclaim: sweep bar itself or up to N subsequent CLOSED 5m bars.
    reclaim_end = min(
        len(bars) - 1,
        sweep_index + config.reclaim_window_bars,
    )
    reclaim_index: int | None = None
    for index in range(sweep_index, reclaim_end + 1):
        close = bars[index].close
        reclaimed = close > sweep_level if side == "long" else close < sweep_level
        if reclaimed:
            reclaim_index = index
            break

    if reclaim_index is None:
        result["failure_reasons"].append("NO_RECLAIM_WITHIN_WINDOW")
        return result

    reclaim_bar = bars[reclaim_index]
    result["reclaim_confirmed"] = True
    result["reclaim_close"] = reclaim_bar.close
    result["reclaim_time"] = iso_from_ms(reclaim_bar.start_ms)

    # Structure confirmation can occur on reclaim bar or later, but must occur
    # within a bounded 30m research window from the sweep.
    confirmation_end = min(
        len(bars) - 1,
        sweep_index + config.max_confirmation_bars,
    )
    confirmation_index: int | None = None
    for index in range(reclaim_index, confirmation_end + 1):
        close = bars[index].close
        shifted = (
            close > structure_level
            if side == "long"
            else close < structure_level
        )
        if shifted:
            confirmation_index = index
            break

    if confirmation_index is None:
        result["failure_reasons"].append("NO_5M_STRUCTURE_SHIFT")
        return result

    confirmation_bar = bars[confirmation_index]
    result["structure_shift_5m"] = True
    result["structure_shift_time_5m"] = iso_from_ms(confirmation_bar.start_ms)
    result["bars_from_sweep_to_confirmation"] = confirmation_index - sweep_index
    result["candidate_entry"] = confirmation_bar.close

    volume_ratio = volume_ratio_at_index(
        bars,
        confirmation_index,
        config.volume_lookback,
    )
    result["volume_ratio_5m"] = volume_ratio
    result["volume_confirmed"] = (
        volume_ratio is not None
        and volume_ratio >= config.volume_confirmation_ratio
    )
    if not result["volume_confirmed"]:
        result["failure_reasons"].append("VOLUME_NOT_CONFIRMED")

    if bars_15m is None:
        fifteen = aggregate_5m_to_15m(bars[:confirmation_index + 1])
    else:
        fifteen = normalize_bars(bars_15m)

    confirmation_close_ms = confirmation_bar.start_ms + FIVE_MIN_MS
    state_15m = classify_15m_structure(
        fifteen,
        confirmation_close_ms,
        config.structure_lookback_15m,
    )
    result["structure_15m_state"] = state_15m
    result["structure_confirmed_15m"] = _15m_confirms(side, state_15m)
    if not result["structure_confirmed_15m"]:
        result["failure_reasons"].append("15M_STRUCTURE_OPPOSES_OR_UNAVAILABLE")

    # "Entry ready" is research classification only. It is NOT a live TRADE gate.
    result["entry_ready"] = bool(
        result["sweep_detected"]
        and result["reclaim_confirmed"]
        and result["structure_shift_5m"]
        and result["structure_confirmed_15m"]
        and result["volume_confirmed"]
    )
    return result


def scan_sweep_setups(
    bars_5m: Iterable[Any],
    side: Side,
    *,
    bars_15m: Iterable[Any] | None = None,
    config: SweepResearchConfig = DEFAULT_CONFIG,
    include_incomplete: bool = True,
) -> list[dict[str, Any]]:
    """Scan all eligible 5m bars for research sweep events.

    This intentionally returns each sweep attempt. Later replay integration can
    apply non-overlap rules without hiding raw event frequency here.
    """
    bars = normalize_bars(bars_5m)
    fifteen = normalize_bars(bars_15m) if bars_15m is not None else None
    start = max(
        config.liquidity_lookback,
        config.structure_lookback_5m,
        config.atr_period,
    )
    output: list[dict[str, Any]] = []
    for index in range(start, len(bars)):
        event = evaluate_sweep_at_index(
            bars,
            index,
            side,
            bars_15m=fifteen,
            config=config,
        )
        if not event["sweep_detected"]:
            continue
        if include_incomplete or event["entry_ready"]:
            output.append(event)
    return output


def latest_sweep_setup(
    bars_5m: Iterable[Any],
    side: Side,
    *,
    bars_15m: Iterable[Any] | None = None,
    config: SweepResearchConfig = DEFAULT_CONFIG,
    entry_ready_only: bool = False,
) -> dict[str, Any] | None:
    events = scan_sweep_setups(
        bars_5m,
        side,
        bars_15m=bars_15m,
        config=config,
        include_incomplete=not entry_ready_only,
    )
    if entry_ready_only:
        events = [event for event in events if event["entry_ready"]]
    return events[-1] if events else None


def config_dict(config: SweepResearchConfig = DEFAULT_CONFIG) -> dict[str, Any]:
    return asdict(config)


__all__ = [
    "DEFAULT_CONFIG",
    "FIVE_MIN_MS",
    "FIFTEEN_MIN_MS",
    "RESEARCH_VERSION",
    "ResearchBar",
    "SweepResearchConfig",
    "aggregate_5m_to_15m",
    "atr_at_index",
    "classify_15m_structure",
    "config_dict",
    "evaluate_sweep_at_index",
    "latest_sweep_setup",
    "normalize_bars",
    "scan_sweep_setups",
    "volume_ratio_at_index",
]
