"""Preregistered label-free relative-strength shadow research logic v1."""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any, Iterable

SPEC_VERSION = "relative-strength-shadow-v1"
HORIZONS = (7, 30, 90)
MIN_COMPLETED_DAILY_BARS = 91
ROTATION_DELTA_THRESHOLD = 20.0
STATE_THRESHOLDS = (
    (80.0, "LEADER"),
    (60.0, "OUTPERFORMER"),
    (40.0, "NEUTRAL"),
    (20.0, "UNDERPERFORMER"),
    (0.0, "LAGGARD"),
)


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "universe": {
            "quote": "USDC",
            "market": "Bybit EU spot",
            "selection": "top_24h_turnover_non_stable_bases",
            "btc_anchor_required": True,
        },
        "data": {
            "timeframe": "1D",
            "completed_candles_only": True,
            "minimum_completed_bars": MIN_COMPLETED_DAILY_BARS,
            "horizons_days": list(HORIZONS),
        },
        "relative_strength": {
            "cross_sectional_percentile": True,
            "rs_score": "equal_weight_mean_of_7d_30d_90d_return_percentiles",
            "state_thresholds": {
                "LEADER": ">=80",
                "OUTPERFORMER": ">=60,<80",
                "NEUTRAL": ">=40,<60",
                "UNDERPERFORMER": ">=20,<40",
                "LAGGARD": "<20",
            },
            "rotation_context": {
                "metric": "7d_percentile_minus_30d_percentile",
                "ACCELERATING": f">={ROTATION_DELTA_THRESHOLD}",
                "STABLE": f">-{ROTATION_DELTA_THRESHOLD},<{ROTATION_DELTA_THRESHOLD}",
                "DECELERATING": f"<=-{ROTATION_DELTA_THRESHOLD}",
            },
        },
        "sector_taxonomy": {
            "included": False,
            "status": "NOT_INCLUDED_UNSOURCED",
            "reason": "No unsourced or hand-maintained sector labels are permitted in v1.",
        },
        "prohibited": [
            "outcome_fitting",
            "threshold_search",
            "trade_signal_generation",
            "live_score_mutation",
            "eligibility_mutation",
            "execution_mutation",
        ],
    }


def parse_closed_daily_klines(
    rows: Iterable[Any], *, now_ms: int, interval_ms: int = 86_400_000
) -> list[dict[str, float | int]]:
    bars: list[dict[str, float | int]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        try:
            start_ms = int(row[0])
            close = float(row[4])
        except (TypeError, ValueError):
            continue
        if start_ms + interval_ms > now_ms or close <= 0:
            continue
        bars.append({"start_ms": start_ms, "close": close})
    bars.sort(key=lambda item: int(item["start_ms"]))
    deduped: dict[int, dict[str, float | int]] = {
        int(item["start_ms"]): item for item in bars
    }
    return [deduped[key] for key in sorted(deduped)]


def _return_pct(closes: list[float], periods: int) -> float:
    if len(closes) <= periods:
        raise ValueError(f"insufficient closes for {periods}d return")
    return (closes[-1] / closes[-1 - periods] - 1.0) * 100.0


def _volatility_30d_pct(closes: list[float]) -> float:
    sample = closes[-31:]
    if len(sample) < 31:
        raise ValueError("insufficient closes for 30d volatility")
    log_returns = [math.log(sample[i] / sample[i - 1]) for i in range(1, len(sample))]
    return statistics.pstdev(log_returns) * 100.0


def _max_drawdown_pct(closes: list[float]) -> float:
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        drawdown = (close / peak - 1.0) * 100.0
        worst = min(worst, drawdown)
    return worst


def compute_symbol_metrics(symbol: str, bars: list[dict[str, float | int]]) -> dict[str, Any]:
    if len(bars) < MIN_COMPLETED_DAILY_BARS:
        raise ValueError(
            f"insufficient completed daily bars: {len(bars)}/{MIN_COMPLETED_DAILY_BARS}"
        )
    closes = [float(item["close"]) for item in bars]
    recent_90 = closes[-91:]
    latest = closes[-1]
    high_90 = max(recent_90)
    return {
        "symbol": symbol.upper(),
        "data_points": len(bars),
        "data_as_of_ms": int(bars[-1]["start_ms"]),
        "close": latest,
        "return_7d_pct": _return_pct(closes, 7),
        "return_30d_pct": _return_pct(closes, 30),
        "return_90d_pct": _return_pct(closes, 90),
        "volatility_30d_pct": _volatility_30d_pct(closes),
        "drawdown_from_90d_high_pct": (latest / high_90 - 1.0) * 100.0,
        "max_drawdown_90d_pct": _max_drawdown_pct(recent_90),
    }


def _percentile_map(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): 50.0}
    result: dict[str, float] = {}
    denominator = len(values) - 1
    for symbol, value in values.items():
        less = sum(1 for other in values.values() if other < value)
        equal = sum(1 for other in values.values() if other == value)
        average_zero_based_rank = less + (equal - 1) / 2.0
        result[symbol] = average_zero_based_rank / denominator * 100.0
    return result


def _state(rs_score: float) -> str:
    for threshold, label in STATE_THRESHOLDS:
        if rs_score >= threshold:
            return label
    return "LAGGARD"


def _rotation_context(delta: float) -> str:
    if delta >= ROTATION_DELTA_THRESHOLD:
        return "ACCELERATING"
    if delta <= -ROTATION_DELTA_THRESHOLD:
        return "DECELERATING"
    return "STABLE"


def build_snapshot(
    analyses: list[dict[str, Any]], *, captured_at: datetime | None = None
) -> dict[str, Any]:
    if not analyses:
        raise ValueError("relative-strength snapshot requires analyses")
    by_symbol = {str(item["symbol"]).upper(): dict(item) for item in analyses}
    if "BTCUSDC" not in by_symbol:
        raise ValueError("BTCUSDC anchor is required")
    if len(by_symbol) < 2:
        raise ValueError("relative-strength snapshot requires at least two symbols")

    percentiles: dict[int, dict[str, float]] = {}
    universe_mean: dict[int, float] = {}
    universe_median: dict[int, float] = {}
    btc_returns: dict[int, float] = {}
    for horizon in HORIZONS:
        field = f"return_{horizon}d_pct"
        values = {symbol: float(item[field]) for symbol, item in by_symbol.items()}
        percentiles[horizon] = _percentile_map(values)
        universe_mean[horizon] = statistics.fmean(values.values())
        universe_median[horizon] = statistics.median(values.values())
        btc_returns[horizon] = float(by_symbol["BTCUSDC"][field])

    enriched: list[dict[str, Any]] = []
    for symbol, item in by_symbol.items():
        row = dict(item)
        horizon_percentiles: list[float] = []
        for horizon in HORIZONS:
            field = f"return_{horizon}d_pct"
            value = float(item[field])
            pct = percentiles[horizon][symbol]
            row[f"percentile_{horizon}d"] = pct
            row[f"relative_to_btc_{horizon}d_pct"] = value - btc_returns[horizon]
            row[f"relative_to_universe_{horizon}d_pct"] = value - universe_mean[horizon]
            horizon_percentiles.append(pct)
        rs_score = statistics.fmean(horizon_percentiles)
        rotation_delta = row["percentile_7d"] - row["percentile_30d"]
        row["rs_score"] = rs_score
        row["state"] = _state(rs_score)
        row["rotation_delta_7d_vs_30d"] = rotation_delta
        row["rotation_context"] = _rotation_context(rotation_delta)
        vol = float(row["volatility_30d_pct"])
        row["return_30d_per_vol_unit"] = (
            float(row["return_30d_pct"]) / vol if vol > 0 else None
        )
        enriched.append(row)

    enriched.sort(key=lambda item: (-float(item["rs_score"]), str(item["symbol"])))
    for rank, item in enumerate(enriched, start=1):
        item["rank"] = rank

    non_btc = [item for item in enriched if item["symbol"] != "BTCUSDC"]
    denominator = len(non_btc) or 1
    breadth: dict[str, Any] = {}
    for horizon in HORIZONS:
        field = f"return_{horizon}d_pct"
        breadth[f"positive_{horizon}d_pct"] = (
            sum(1 for item in non_btc if float(item[field]) > 0) / denominator * 100.0
        )
        breadth[f"outperform_btc_{horizon}d_pct"] = (
            sum(
                1
                for item in non_btc
                if float(item[field]) > btc_returns[horizon]
            )
            / denominator
            * 100.0
        )
        breadth[f"mean_return_{horizon}d_pct"] = universe_mean[horizon]
        breadth[f"median_return_{horizon}d_pct"] = universe_median[horizon]
        breadth[f"btc_return_{horizon}d_pct"] = btc_returns[horizon]

    state_counts: dict[str, int] = {
        label: sum(1 for item in enriched if item["state"] == label)
        for _, label in STATE_THRESHOLDS
    }
    rotation_counts = {
        label: sum(1 for item in enriched if item["rotation_context"] == label)
        for label in ("ACCELERATING", "STABLE", "DECELERATING")
    }
    now = captured_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "promotion_allowed": False,
        "spec": spec(),
        "captured_at": now.astimezone(timezone.utc).isoformat(),
        "universe_size": len(enriched),
        "btc_anchor": next(item for item in enriched if item["symbol"] == "BTCUSDC"),
        "breadth": breadth,
        "state_counts": state_counts,
        "rotation_counts": rotation_counts,
        "leaders": [item["symbol"] for item in enriched[:5]],
        "laggards": [item["symbol"] for item in enriched[-5:]],
        "symbols": enriched,
        "sector_rotation_available": False,
        "sector_metadata_status": "NOT_INCLUDED_UNSOURCED",
    }
