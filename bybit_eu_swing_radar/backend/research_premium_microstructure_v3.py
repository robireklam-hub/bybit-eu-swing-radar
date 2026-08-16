"""Premium-index microstructure research for Trading Radar v0.7.3.

Research only. Premium-index data is derivatives context and never a hard gate.
All joins are strictly point-in-time; no live strategy/scoring/execution is changed.
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

ANALYSIS_VERSION = "day-trade-premium-microstructure-v3"
TRAIN_DAYS = 90
HOLDOUT_DAYS = 30
MIN_TRAIN_N = 150
MIN_HOLDOUT_N = 50
MAX_PREMIUM_AGE_SECONDS = 90 * 60
TRAILING_Z_POINTS = 24


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _timestamp_seconds(value: Any) -> int | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            raw = float(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            if not math.isfinite(raw):
                return None
            return int(raw / 1000.0) if raw > 10_000_000_000 else int(raw)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = float(value)
        if not math.isfinite(raw):
            return None
        return int(raw / 1000.0) if raw > 10_000_000_000 else int(raw)
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True)
class PremiumPoint:
    ts: int
    close: float


def normalize_premium_klines(rows: list[list[Any]]) -> list[PremiumPoint]:
    points: dict[int, PremiumPoint] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        ts = _timestamp_seconds(row[0])
        close = _number(row[4])
        if ts is None or close is None:
            continue
        points[ts] = PremiumPoint(ts=ts, close=close)
    return [points[key] for key in sorted(points)]


def _latest_index(points: list[PremiumPoint], target_ts: int) -> int | None:
    if not points:
        return None
    timestamps = [point.ts for point in points]
    index = bisect.bisect_right(timestamps, target_ts) - 1
    return index if index >= 0 else None


def enrich_with_premium(
    row: dict[str, Any],
    *,
    derivative_symbol: str | None,
    points: list[PremiumPoint],
    max_age_seconds: int = MAX_PREMIUM_AGE_SECONDS,
) -> dict[str, Any]:
    enriched = dict(row)
    enriched.update(
        {
            "premium_derivative_symbol": derivative_symbol,
            "premium_available": False,
            "premium_close": None,
            "premium_age_seconds": None,
            "premium_change_4h": None,
            "premium_z_24h": None,
            "signed_premium": None,
            "signed_premium_change_4h": None,
            "signed_premium_z_24h": None,
        }
    )
    opened_ts = _timestamp_seconds(row.get("opened_at"))
    if opened_ts is None or derivative_symbol is None or not points:
        return enriched
    index = _latest_index(points, opened_ts)
    if index is None:
        return enriched
    current = points[index]
    if opened_ts - current.ts > max_age_seconds:
        return enriched

    prior_4h_index = _latest_index(points, opened_ts - 4 * 3600)
    prior_4h = points[prior_4h_index] if prior_4h_index is not None else None
    change_4h = current.close - prior_4h.close if prior_4h is not None else None

    window_start = max(0, index - TRAILING_Z_POINTS + 1)
    trailing = [point.close for point in points[window_start : index + 1]]
    z_value = None
    if len(trailing) >= 12:
        mean = statistics.fmean(trailing)
        stdev = statistics.pstdev(trailing)
        if stdev > 0:
            z_value = (current.close - mean) / stdev

    side = str(row.get("side") or "").lower()
    side_sign = 1.0 if side == "long" else -1.0 if side == "short" else None
    enriched["premium_available"] = True
    enriched["premium_close"] = current.close
    enriched["premium_age_seconds"] = opened_ts - current.ts
    enriched["premium_change_4h"] = change_4h
    enriched["premium_z_24h"] = z_value
    if side_sign is not None:
        enriched["signed_premium"] = side_sign * current.close
        enriched["signed_premium_change_4h"] = side_sign * change_4h if change_4h is not None else None
        enriched["signed_premium_z_24h"] = side_sign * z_value if z_value is not None else None
    return enriched


def _quantile(values: list[float], probability: float) -> float:
    rows = sorted(values)
    if not rows:
        raise ValueError("quantile requires values")
    position = (len(rows) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return rows[lower]
    weight = position - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight


def _stats(values: list[float]) -> dict[str, Any]:
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    return {
        "n": len(values),
        "average_net_r": round(statistics.fmean(values), 6) if values else None,
        "median_net_r": round(statistics.median(values), 6) if values else None,
        "positive_rate_pct": round(sum(value > 0 for value in values) / len(values) * 100, 3) if values else None,
        "profit_factor": round(gains / losses, 6) if losses > 0 else None,
        "total_net_r": round(sum(values), 6),
    }


def _evaluate(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    selected: list[float] = []
    complement: list[float] = []
    for row in rows:
        net = _number(row.get("base_net_r"))
        if net is None:
            continue
        (selected if predicate(row) else complement).append(net)
    selected_stats = _stats(selected)
    complement_stats = _stats(complement)
    uplift = None
    if selected_stats["average_net_r"] is not None and complement_stats["average_net_r"] is not None:
        uplift = round(
            float(selected_stats["average_net_r"]) - float(complement_stats["average_net_r"]),
            6,
        )
    return {
        "selected": selected_stats,
        "complement": complement_stats,
        "average_net_r_uplift_vs_complement": uplift,
    }


def build_premium_report(
    rows: list[dict[str, Any]], *, start_at: datetime, development_end_at: datetime
) -> dict[str, Any]:
    train_end = start_at + timedelta(days=TRAIN_DAYS)
    holdout_end = min(development_end_at, train_end + timedelta(days=HOLDOUT_DAYS))
    train: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    reused_validation: list[dict[str, Any]] = []
    for row in rows:
        opened = _parse_time(row.get("opened_at"))
        if opened is None:
            continue
        if start_at <= opened < train_end:
            train.append(row)
        elif train_end <= opened < holdout_end:
            holdout.append(row)
        elif row.get("dataset_split") == "VALIDATION":
            reused_validation.append(row)

    expansion_values = [
        value
        for value in (_number(row.get("expansion_score")) for row in train)
        if value is not None
    ]
    q75 = _quantile(expansion_values, 0.75) if expansion_values else None

    def high_expansion(row: dict[str, Any]) -> bool:
        value = _number(row.get("expansion_score"))
        return q75 is not None and value is not None and value > q75

    def against_side(row: dict[str, Any]) -> bool:
        value = _number(row.get("signed_premium"))
        return value is not None and value <= 0

    def adverse_dislocation(row: dict[str, Any]) -> bool:
        value = _number(row.get("signed_premium_z_24h"))
        return value is not None and value <= -1.0

    def crowded_with_side(row: dict[str, Any]) -> bool:
        value = _number(row.get("signed_premium_z_24h"))
        return value is not None and value >= 1.0

    def adverse_but_reverting(row: dict[str, Any]) -> bool:
        signed = _number(row.get("signed_premium"))
        delta = _number(row.get("signed_premium_change_4h"))
        return signed is not None and delta is not None and signed <= 0 and delta > 0

    rules: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
        (
            "high_expansion_x_premium_against_side",
            "High expansion while the derivative premium is not crowded in the trade direction.",
            lambda row: high_expansion(row) and against_side(row),
        ),
        (
            "high_expansion_x_adverse_premium_dislocation",
            "High expansion with side-adjusted premium at least one trailing 24h sigma against the trade direction.",
            lambda row: high_expansion(row) and adverse_dislocation(row),
        ),
        (
            "high_expansion_x_crowded_premium",
            "High expansion with side-adjusted premium at least one trailing 24h sigma in the trade direction.",
            lambda row: high_expansion(row) and crowded_with_side(row),
        ),
        (
            "high_expansion_x_adverse_premium_reverting",
            "High expansion with adverse premium sign that has moved toward the trade direction over four hours.",
            lambda row: high_expansion(row) and adverse_but_reverting(row),
        ),
    ]

    candidates: list[dict[str, Any]] = []
    predicates: dict[str, Callable[[dict[str, Any]], bool]] = {}
    for name, rationale, predicate in rules:
        predicates[name] = predicate
        result = _evaluate(train, predicate)
        candidates.append(
            {
                "name": name,
                "rationale": rationale,
                "eligible_for_train_selection": int(result["selected"]["n"]) >= MIN_TRAIN_N,
                "train": result,
            }
        )

    eligible = [
        item
        for item in candidates
        if item["eligible_for_train_selection"]
        and item["train"]["average_net_r_uplift_vs_complement"] is not None
    ]
    winner = max(
        eligible,
        key=lambda item: float(item["train"]["average_net_r_uplift_vs_complement"]),
        default=None,
    )
    winner_name = str(winner["name"]) if winner else None
    holdout_result = _evaluate(holdout, predicates[winner_name]) if winner_name else None
    validation_reference = (
        _evaluate(reused_validation, predicates[winner_name]) if winner_name else None
    )
    internal_pass = False
    if holdout_result:
        selected = holdout_result["selected"]
        internal_pass = (
            int(selected["n"]) >= MIN_HOLDOUT_N
            and selected["average_net_r"] is not None
            and float(selected["average_net_r"]) > 0
            and selected["profit_factor"] is not None
            and float(selected["profit_factor"]) > 1
        )

    available = sum(bool(row.get("premium_available")) for row in rows)
    return {
        "analysis_version": ANALYSIS_VERSION,
        "research_only": True,
        "promotion_allowed": False,
        "status": "OK" if train and holdout else "INSUFFICIENT_SPLIT_DATA",
        "coverage": {
            "rows": len(rows),
            "premium_rows": available,
            "premium_coverage_pct": round(available / len(rows) * 100.0, 3) if rows else 0.0,
        },
        "split_policy": {
            "winner_selected_on": "FIRST_90D_DEVELOPMENT_ONLY",
            "train_days": TRAIN_DAYS,
            "internal_holdout_days": HOLDOUT_DAYS,
            "historical_validation_status": "REUSED_REFERENCE_NOT_UNTOUCHED_OOS",
            "validation_threshold_search": False,
            "premium_z_thresholds": [-1.0, 1.0],
        },
        "learned_on_train_only": {
            "expansion_score_q75": round(q75, 8) if q75 is not None else None
        },
        "candidate_train_results": candidates,
        "selected_on_train": winner_name,
        "internal_holdout_result": holdout_result,
        "reused_external_validation_reference": validation_reference,
        "internal_holdout_edge_pass": internal_pass,
        "next_step": (
            "Freeze the premium rule and require a genuinely fresh forward holdout before any live promotion."
            if internal_pass
            else "No positive premium-microstructure holdout edge. Stop feature-threshold tuning of this v0.7.3 family and make an explicit strategy pivot decision."
        ),
        "warnings": [
            "Premium-index data is derivatives context only and never a hard gate.",
            "The 24h z-score uses only premium observations at or before each opportunity.",
            "The original 60d validation has already been inspected and cannot authorize promotion.",
            "Historical shorts remain technical-only and do not establish Bybit EU borrowability.",
            "No live strategy, scoring, trigger, eligibility or execution logic is changed.",
        ],
    }
