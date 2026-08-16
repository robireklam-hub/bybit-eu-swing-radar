"""Research-only strategy-family pivot: 5m breakout continuation v5.

This is intentionally independent of the failed v0.7.3 liquidity-sweep entry
family. It uses only point-in-time USDC spot 5m bars to test whether the stable
high-expansion observation is better expressed as continuation rather than
mean-reversion/reclaim.

No live day_worker strategy, scoring, eligibility or execution code is changed.
Historical shorts are technical research only.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from day_worker import DAY_MIN_RR
from diagnostics_v073 import DIAGNOSTIC_BASE_COST_BPS, DIAGNOSTIC_BASE_HORIZON_HOURS, evaluate_path
from worker import Bar

ANALYSIS_VERSION = "day-trade-breakout-continuation-v5"
CHANNEL_LOOKBACK = 12          # prior 60m on 5m bars
STOP_LOOKBACK = 6             # prior 30m local structure
VOLUME_LOOKBACK = 20
VOLUME_RATIO = 1.30           # same fixed confirmation convention already used by research trigger
STRONG_CLOSE_FRACTION = 0.75  # top/bottom quartile of breakout candle
COOLDOWN_BARS = 12            # one event per side per hour at most
TRAIN_DAYS = 90
INTERNAL_HOLDOUT_DAYS = 30
MIN_TRAIN_N = 150
MIN_HOLDOUT_N = 50


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _stats(values: list[float]) -> dict[str, Any]:
    clean = [float(v) for v in values if math.isfinite(float(v))]
    gains = sum(v for v in clean if v > 0)
    losses = abs(sum(v for v in clean if v < 0))
    return {
        "n": len(clean),
        "average_net_r": round(statistics.fmean(clean), 6) if clean else None,
        "median_net_r": round(statistics.median(clean), 6) if clean else None,
        "positive_rate_pct": round(sum(v > 0 for v in clean) / len(clean) * 100.0, 3) if clean else None,
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "total_net_r": round(sum(clean), 6),
    }


def _targets(side: str, entry: float, stop: float) -> tuple[float, float, float] | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    cost = entry * DIAGNOSTIC_BASE_COST_BPS / 10_000.0
    direction = 1.0 if side == "long" else -1.0

    def target(net_r: float) -> float:
        return entry + direction * (net_r * risk + cost)

    return target(1.0), target(DAY_MIN_RR), target(2.5)


def _volume_ratio(bars: list[Bar], index: int) -> float | None:
    if index < VOLUME_LOOKBACK:
        return None
    prior = bars[index - VOLUME_LOOKBACK:index]
    mean = sum(float(bar.volume) for bar in prior) / VOLUME_LOOKBACK
    return float(bars[index].volume) / mean if mean > 0 else None


def _close_location(bar: Bar) -> float | None:
    span = float(bar.high) - float(bar.low)
    if span <= 0:
        return None
    return (float(bar.close) - float(bar.low)) / span


def replay_symbol_breakouts(
    *,
    symbol: str,
    bars: list[Bar],
    start_at: datetime,
    end_at: datetime,
    development_end_at: datetime,
) -> list[dict[str, Any]]:
    """Generate fixed breakout mechanisms with no future data in signal formation."""
    if len(bars) < 100:
        return []
    bars = sorted(bars, key=lambda item: item.start_ms)
    start_ms = int(start_at.timestamp() * 1000)
    end_ms = int(end_at.timestamp() * 1000)
    horizon = DIAGNOSTIC_BASE_HORIZON_HOURS * 12
    last_event: dict[str, int] = {"long": -10_000, "short": -10_000}
    rows: list[dict[str, Any]] = []

    first = max(CHANNEL_LOOKBACK, STOP_LOOKBACK, VOLUME_LOOKBACK)
    for i in range(first, len(bars) - 1):
        bar = bars[i]
        opened_ms = int(bar.start_ms) + 5 * 60 * 1000
        if opened_ms < start_ms:
            continue
        if opened_ms >= end_ms:
            break

        channel = bars[i - CHANNEL_LOOKBACK:i]
        prior_high = max(float(item.high) for item in channel)
        prior_low = min(float(item.low) for item in channel)
        volume_ratio = _volume_ratio(bars, i)
        close_location = _close_location(bar)
        for side in ("long", "short"):
            if i - last_event[side] < COOLDOWN_BARS:
                continue
            breakout = float(bar.close) > prior_high if side == "long" else float(bar.close) < prior_low
            if not breakout:
                continue

            stop_window = bars[i - STOP_LOOKBACK:i]
            stop = min(float(item.low) for item in stop_window) if side == "long" else max(float(item.high) for item in stop_window)
            entry = float(bar.close)
            if entry <= 0 or stop <= 0:
                continue
            if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
                continue
            risk = abs(entry - stop)
            if risk / entry > 0.05 or risk / entry < 0.0005:
                continue

            targets = _targets(side, entry, stop)
            if targets is None:
                continue
            future = bars[i + 1:i + 1 + horizon]
            if not future:
                continue
            path = evaluate_path(side, entry, stop, targets[0], targets[1], targets[2], future)
            if path is None:
                continue
            cost_r = (entry * DIAGNOSTIC_BASE_COST_BPS / 10_000.0) / risk
            net_r = round(float(path["gross_r"]) - cost_r, 6)
            opened_at = datetime.fromtimestamp(opened_ms / 1000.0, tz=timezone.utc)
            split = "DEVELOPMENT" if opened_at < development_end_at else "VALIDATION"
            volume_ok = volume_ratio is not None and volume_ratio >= VOLUME_RATIO
            strong_close = False
            if close_location is not None:
                strong_close = close_location >= STRONG_CLOSE_FRACTION if side == "long" else close_location <= 1.0 - STRONG_CLOSE_FRACTION

            rows.append({
                "symbol": symbol,
                "side": side,
                "opened_at": opened_at,
                "dataset_split": split,
                "entry": entry,
                "stop": stop,
                "risk_pct": round(risk / entry * 100.0, 5),
                "volume_ratio": None if volume_ratio is None else round(volume_ratio, 6),
                "close_location": None if close_location is None else round(close_location, 6),
                "volume_confirmed": volume_ok,
                "strong_close": strong_close,
                "net_r": net_r,
                "gross_r": path["gross_r"],
                "exit_reason": path["exit_reason"],
                "execution_assumption": (
                    "BYBIT_EU_USDC_SPOT_HISTORICAL_KLINE"
                    if side == "long" else "SHORT_TECHNICAL_BORROW_UNVERIFIED"
                ),
            })
            last_event[side] = i
    return rows


def _variant_rows(rows: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    if name == "raw_channel_breakout":
        return rows
    if name == "volume_confirmed_breakout":
        return [row for row in rows if row.get("volume_confirmed")]
    if name == "strong_close_volume_breakout":
        return [row for row in rows if row.get("volume_confirmed") and row.get("strong_close")]
    raise ValueError(f"unknown breakout variant: {name}")


def _variant_stats(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    selected = _variant_rows(rows, name)
    stats = _stats([float(row["net_r"]) for row in selected if _number(row.get("net_r")) is not None])
    return {"event_share_pct": round(len(selected) / len(rows) * 100.0, 3) if rows else None, **stats}


def build_breakout_report(
    rows: list[dict[str, Any]], *, start_at: datetime, development_end_at: datetime
) -> dict[str, Any]:
    train_end = start_at + timedelta(days=TRAIN_DAYS)
    holdout_end = min(development_end_at, train_end + timedelta(days=INTERNAL_HOLDOUT_DAYS))
    train = [row for row in rows if start_at <= row["opened_at"] < train_end]
    holdout = [row for row in rows if train_end <= row["opened_at"] < holdout_end]
    reused_validation = [row for row in rows if row.get("dataset_split") == "VALIDATION"]

    variants = ("raw_channel_breakout", "volume_confirmed_breakout", "strong_close_volume_breakout")
    candidates: list[dict[str, Any]] = []
    for name in variants:
        result = _variant_stats(train, name)
        candidates.append({
            "name": name,
            "eligible_for_train_selection": int(result["n"]) >= MIN_TRAIN_N,
            "train": result,
        })
    eligible = [item for item in candidates if item["eligible_for_train_selection"] and item["train"]["average_net_r"] is not None]
    winner = max(eligible, key=lambda item: float(item["train"]["average_net_r"]), default=None)
    winner_name = None if winner is None else str(winner["name"])
    holdout_result = _variant_stats(holdout, winner_name) if winner_name else None
    external_result = _variant_stats(reused_validation, winner_name) if winner_name else None

    train_pass = False
    holdout_pass = False
    if winner is not None:
        selected = winner["train"]
        train_pass = bool(
            selected["average_net_r"] is not None and selected["average_net_r"] > 0
            and selected["profit_factor"] is not None and selected["profit_factor"] > 1.0
        )
    if holdout_result is not None:
        holdout_pass = bool(
            int(holdout_result["n"]) >= MIN_HOLDOUT_N
            and holdout_result["average_net_r"] is not None and holdout_result["average_net_r"] > 0
            and holdout_result["profit_factor"] is not None and holdout_result["profit_factor"] > 1.0
        )
    edge_pass = train_pass and holdout_pass
    return {
        "analysis_version": ANALYSIS_VERSION,
        "status": "OK" if train and holdout else "INSUFFICIENT_SPLIT_DATA",
        "research_only": True,
        "promotion_allowed": False,
        "split_policy": {
            "train_days": TRAIN_DAYS,
            "internal_holdout_days": INTERNAL_HOLDOUT_DAYS,
            "historical_validation_status": "REUSED_REFERENCE_NOT_UNTOUCHED_OOS",
            "winner_selected_on": "FIRST_90D_DEVELOPMENT_ONLY",
            "validation_threshold_search": False,
        },
        "fixed_mechanism": {
            "channel_lookback_bars": CHANNEL_LOOKBACK,
            "stop_lookback_bars": STOP_LOOKBACK,
            "volume_lookback_bars": VOLUME_LOOKBACK,
            "volume_ratio": VOLUME_RATIO,
            "strong_close_fraction": STRONG_CLOSE_FRACTION,
            "cooldown_bars": COOLDOWN_BARS,
            "cost_bps": DIAGNOSTIC_BASE_COST_BPS,
            "horizon_hours": DIAGNOSTIC_BASE_HORIZON_HOURS,
        },
        "candidate_train_results": candidates,
        "selected_on_train": winner_name,
        "internal_holdout_result": holdout_result,
        "reused_external_validation_reference": external_result,
        "train_edge_pass": train_pass,
        "internal_holdout_edge_pass": holdout_pass,
        "strategy_family_edge_pass": edge_pass,
        "next_step": (
            "Freeze this breakout mechanism and collect genuinely fresh forward OOS before any promotion."
            if edge_pass
            else "No positive train+holdout breakout continuation edge. Do not modify live strategy; the tested day-trade families do not justify promotion."
        ),
        "warnings": [
            "This is a genuinely separate continuation family, not a threshold tweak of the failed sweep family.",
            "Signal formation uses only closed/current and prior USDC spot 5m bars; future bars are used only for outcome evaluation.",
            "Historical shorts are technical-only and do not establish Bybit EU spot-margin borrowability.",
            "The original 60d validation has already been inspected and cannot authorize promotion.",
            "No live strategy, scoring, eligibility or execution code is changed.",
        ],
    }
