"""Preregistered ETH On-Chain Context v1 research contract.

Descriptive, label-free context only. It must never mutate live strategy,
eligibility, execution, trigger, entry, stop, target, or scoring behavior.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from statistics import fmean
from typing import Any

SPEC_VERSION = "eth-onchain-context-shadow-v1"
CORE_METRICS = (
    "AdrActCnt",
    "TxCnt",
    "FeeTotNtv",
    "SplyCur",
)
OPTIONAL_METRICS = (
    "SplyCurEL",
    "FeePrioTotNtv",
    "ValidatorActOngCnt",
)
COIN_METRICS = CORE_METRICS + OPTIONAL_METRICS


def spec() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "live_strategy_mutated": False,
        "execution_proof": False,
        "promotion_allowed": False,
        "asset": "ETH",
        "coin_metrics": {
            "provider": "Coin Metrics Community API",
            "frequency": "1d",
            "closed_daily_only": True,
            "lookback_days": 100,
            "core_metrics": list(CORE_METRICS),
            "optional_metrics": list(OPTIONAL_METRICS),
            "request_mode": "per_metric_fail_transparent",
            "optional_missing_is_zero": False,
        },
        "network_semantics": {
            "consensus": "proof_of_stake",
            "excluded_btc_mining_metrics": ["HashRate", "DiffMean"],
            "reason": "ETH post-Merge network context must not reuse proof-of-work hash-rate or mining-difficulty semantics.",
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
    """Keep only ETH daily rows whose UTC date is fully closed."""
    output: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("asset") or "").lower() != "eth":
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
    mean_7d = fmean(values[-7:])
    mean_30d = fmean(values[-30:])
    latest_vs_30d = None
    if mean_30d != 0:
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
    core_available = [metric for metric in CORE_METRICS if metric in available]
    optional_available = [metric for metric in OPTIONAL_METRICS if metric in available]
    return {
        "closed_through": closed_through.isoformat(),
        "row_count": len(rows),
        "metrics": metrics,
        "available_metric_count": len(available),
        "requested_metric_count": len(COIN_METRICS),
        "core_available_metric_count": len(core_available),
        "core_requested_metric_count": len(CORE_METRICS),
        "core_available_metrics": core_available,
        "core_missing_metrics": [metric for metric in CORE_METRICS if metric not in core_available],
        "optional_available_metric_count": len(optional_available),
        "optional_requested_metric_count": len(OPTIONAL_METRICS),
        "optional_available_metrics": optional_available,
        "optional_missing_metrics": [metric for metric in OPTIONAL_METRICS if metric not in optional_available],
    }, available


def build_snapshot(
    *,
    coin_metrics: dict[str, Any] | None,
    source_status: dict[str, Any],
    source_commit_sha: str | None,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    now = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    core_available = int((coin_metrics or {}).get("core_available_metric_count") or 0)
    core_requested = int((coin_metrics or {}).get("core_requested_metric_count") or len(CORE_METRICS))
    any_available = int((coin_metrics or {}).get("available_metric_count") or 0)
    if core_requested and core_available == core_requested:
        quality = "COMPLETE"
    elif any_available > 0:
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
        "source_status": source_status,
    }
