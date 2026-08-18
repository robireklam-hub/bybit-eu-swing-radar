"""Preregistered BTC On-Chain Context v1 research contract.

Descriptive, label-free context only. It must never mutate live strategy,
eligibility, execution, trigger, entry, stop, target, or scoring behavior.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import fmean
from typing import Any

SPEC_VERSION = "btc-onchain-context-shadow-v1"
COIN_METRICS = (
    "AdrActCnt",
    "TxCnt",
    "FeeTotNtv",
    "HashRate",
    "SplyCur",
)
COMMUNITY_EXCLUDED_METRICS = {
    "DiffMean": "Coin Metrics Community API returns 403 for BTC 1d; difficulty context is sourced separately from mempool.space.",
}


def spec() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "asset": "BTC",
        "coin_metrics": {
            "provider": "Coin Metrics Community API",
            "frequency": "1d",
            "closed_daily_only": True,
            "lookback_days": 100,
            "metrics": list(COIN_METRICS),
            "excluded_metrics": COMMUNITY_EXCLUDED_METRICS,
        },
        "mempool_context": {
            "provider": "mempool.space",
            "endpoints": ["mempool", "recommended_fees", "difficulty_adjustment"],
        },
        "summaries": [
            "latest",
            "latest_date",
            "observations",
            "mean_7d",
            "mean_30d",
            "latest_vs_30d_mean_pct",
            "change_30d_pct",
        ],
        "forbidden": [
            "bull_bear_score",
            "directional_trade_signal",
            "eligibility_gate",
            "execution_proof",
            "outcome_labels",
            "threshold_search",
        ],
    }


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_from_time(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def closed_daily_rows(
    rows: list[dict[str, Any]], *, closed_through: date
) -> list[dict[str, Any]]:
    """Keep only BTC daily rows whose UTC date is fully closed."""
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("asset") or "").lower() != "btc":
            continue
        day = _date_from_time(row.get("time"))
        if day is None or date.fromisoformat(day) > closed_through:
            continue
        output.append({**row, "_day": day})
    output.sort(key=lambda item: item["_day"])
    return output


def summarize_metric(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    points: list[tuple[str, float]] = []
    for row in rows:
        value = _to_float(row.get(metric))
        day = str(row.get("_day") or _date_from_time(row.get("time")) or "")
        if value is None or not day:
            continue
        points.append((day, value))
    points.sort(key=lambda item: item[0])
    if not points:
        return {
            "metric": metric,
            "available": False,
            "latest": None,
            "latest_date": None,
            "observations": 0,
            "mean_7d": None,
            "mean_30d": None,
            "latest_vs_30d_mean_pct": None,
            "change_30d_pct": None,
        }

    values = [value for _, value in points]
    latest = values[-1]
    mean_7d = fmean(values[-7:]) if values else None
    mean_30d = fmean(values[-30:]) if values else None
    latest_vs_30d = None
    if mean_30d not in (None, 0):
        latest_vs_30d = (latest / mean_30d - 1.0) * 100.0
    change_30d = None
    if len(values) >= 31 and values[-31] != 0:
        change_30d = (latest / values[-31] - 1.0) * 100.0
    return {
        "metric": metric,
        "available": True,
        "latest": latest,
        "latest_date": points[-1][0],
        "observations": len(points),
        "mean_7d": mean_7d,
        "mean_30d": mean_30d,
        "latest_vs_30d_mean_pct": latest_vs_30d,
        "change_30d_pct": change_30d,
    }


def summarize_coin_metrics(
    raw_rows: list[dict[str, Any]], *, closed_through: date
) -> tuple[dict[str, Any], list[str]]:
    rows = closed_daily_rows(raw_rows, closed_through=closed_through)
    metrics = {metric: summarize_metric(rows, metric) for metric in COIN_METRICS}
    available = [metric for metric, payload in metrics.items() if payload["available"]]
    return {
        "closed_through": closed_through.isoformat(),
        "row_count": len(rows),
        "metrics": metrics,
        "available_metric_count": len(available),
        "requested_metric_count": len(COIN_METRICS),
    }, available


def compact_mempool(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": payload.get("count"),
        "vsize": payload.get("vsize"),
        "total_fee_sats": payload.get("total_fee"),
    }


def compact_fees(payload: dict[str, Any]) -> dict[str, Any]:
    keys = ("fastestFee", "halfHourFee", "hourFee", "economyFee", "minimumFee")
    return {key: payload.get(key) for key in keys}


def compact_difficulty(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "progressPercent",
        "difficultyChange",
        "estimatedRetargetDate",
        "remainingBlocks",
        "remainingTime",
        "previousRetarget",
        "nextRetargetHeight",
        "timeAvg",
    )
    return {key: payload.get(key) for key in keys if key in payload}


def build_snapshot(
    *,
    coin_metrics: dict[str, Any] | None,
    mempool: dict[str, Any] | None,
    recommended_fees: dict[str, Any] | None,
    difficulty: dict[str, Any] | None,
    source_status: dict[str, Any],
    source_commit_sha: str | None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    now = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    live_sources = sum(1 for item in source_status.values() if item.get("status") == "LIVE")
    total_sources = len(source_status)
    if total_sources and live_sources == total_sources:
        quality = "COMPLETE"
    elif live_sources > 0:
        quality = "PARTIAL"
    else:
        quality = "MISSING"
    return {
        "spec_version": SPEC_VERSION,
        "captured_at": now.isoformat(),
        "source_commit_sha": source_commit_sha,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "data_quality": quality,
        "coin_metrics": coin_metrics,
        "mempool": mempool,
        "recommended_fees": recommended_fees,
        "difficulty_adjustment": difficulty,
        "source_status": source_status,
    }
