"""Research-only geopolitical news-attention context v1.

This layer measures sourced media attention and short-horizon acceleration for a
fixed geopolitical taxonomy. It is descriptive context only: it is not event
truth, not a trade signal, and never mutates live strategy or execution state.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SPEC_VERSION = "geopolitical-risk-shadow-v1"
PROVIDER = "GDELT DOC 2.0"

TOPICS: dict[str, dict[str, str]] = {
    "armed_conflict": {
        "query": '(war OR invasion OR "military strike" OR shelling OR bombardment OR ceasefire)',
        "description": "Interstate/intrastate armed-conflict and military-escalation coverage.",
    },
    "sanctions_trade": {
        "query": '(sanctions OR sanction OR embargo OR "export controls" OR tariff)',
        "description": "Sanctions, embargo, export-control and tariff coverage.",
    },
    "energy_shipping": {
        "query": '("shipping disruption" OR "shipping attack" OR tanker OR "oil supply disruption" OR "gas supply disruption" OR "strait of hormuz" OR "red sea")',
        "description": "Energy-supply and strategic-shipping disruption coverage.",
    },
    "cyber_infrastructure": {
        "query": '("cyber attack" OR cyberattack OR ransomware OR "critical infrastructure")',
        "description": "Cyberattack and critical-infrastructure disruption coverage.",
    },
    "nuclear_escalation": {
        "query": '("nuclear threat" OR "nuclear weapons" OR "nuclear strike" OR "ballistic missile")',
        "description": "Nuclear and strategic-missile escalation coverage.",
    },
}


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "provider": PROVIDER,
        "provider_role": "global_news_attention_proxy_not_event_ground_truth",
        "lookback": "24h",
        "recent_window": "6h",
        "baseline_window": "preceding 18h",
        "topics": TOPICS,
        "principles": [
            "topic queries are fixed before outcome observation and contain no coin or trade-direction terms",
            "raw article count is normalized by GDELT monitored-article volume when norm is available",
            "recent-vs-baseline acceleration is descriptive and has no threshold-based trade meaning",
            "provider errors and missing bins remain explicit; missing coverage is never treated as zero risk",
            "no outcome, journal, net-R, post-trade, strategy-score, eligibility, or execution labels are read",
            "no composite geopolitical score is produced",
        ],
    }


def parse_gdelt_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S%z"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_timeline_points(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Extract TimelineVolRaw JSON points while tolerating extra GDELT series."""
    points: list[dict[str, Any]] = []
    timeline = payload.get("timeline") or []
    if isinstance(timeline, Mapping):
        timeline = [timeline]
    for series in timeline if isinstance(timeline, list) else []:
        if not isinstance(series, Mapping):
            continue
        data = series.get("data") or []
        if not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, Mapping):
                continue
            dt = parse_gdelt_datetime(row.get("date") or row.get("datetime") or row.get("timestamp"))
            value = _safe_float(row.get("value"))
            norm = _safe_float(row.get("norm"))
            if dt is None or value is None:
                continue
            points.append({
                "at": dt,
                "count": max(value, 0.0),
                "norm": max(norm, 0.0) if norm is not None else None,
            })
    # Multiple series can occasionally contain equivalent dates. Keep the row
    # with the largest raw count to avoid accidental double-counting.
    dedup: dict[datetime, dict[str, Any]] = {}
    for point in points:
        previous = dedup.get(point["at"])
        if previous is None or point["count"] > previous["count"]:
            dedup[point["at"]] = point
    return [dedup[key] for key in sorted(dedup)]


def _window_summary(points: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(points)
    count = sum(float(row.get("count") or 0.0) for row in rows)
    norms = [row.get("norm") for row in rows]
    norm = sum(float(value) for value in norms if value is not None)
    share_pct = (100.0 * count / norm) if norm > 0 else None
    return {
        "bins": len(rows),
        "article_count": round(count, 4),
        "monitored_article_count": round(norm, 4) if norm > 0 else None,
        "share_pct": None if share_pct is None else round(share_pct, 8),
    }


def summarize_topic(points: Iterable[Mapping[str, Any]], *, captured_at: datetime) -> dict[str, Any]:
    now = captured_at.astimezone(timezone.utc)
    rows = sorted(
        [dict(row) for row in points if isinstance(row.get("at"), datetime) and row["at"] <= now],
        key=lambda row: row["at"],
    )
    recent_cutoff = now.timestamp() - 6 * 3600
    baseline_cutoff = now.timestamp() - 24 * 3600
    recent = [row for row in rows if row["at"].timestamp() > recent_cutoff]
    baseline = [row for row in rows if baseline_cutoff < row["at"].timestamp() <= recent_cutoff]
    full = [row for row in rows if row["at"].timestamp() > baseline_cutoff]

    recent_summary = _window_summary(recent)
    baseline_summary = _window_summary(baseline)
    full_summary = _window_summary(full)
    recent_share = recent_summary["share_pct"]
    baseline_share = baseline_summary["share_pct"]
    acceleration_ratio = None
    if recent_share is not None and baseline_share is not None and baseline_share > 0:
        acceleration_ratio = recent_share / baseline_share

    max_bin = max(full, key=lambda row: row.get("count") or 0.0, default=None)
    return {
        "latest_bin_at": full[-1]["at"].isoformat() if full else None,
        "lookback_24h": full_summary,
        "recent_6h": recent_summary,
        "baseline_18h": baseline_summary,
        "recent_vs_baseline_share_ratio": None if acceleration_ratio is None else round(acceleration_ratio, 6),
        "max_raw_bin": None if max_bin is None else {
            "at": max_bin["at"].isoformat(),
            "article_count": round(float(max_bin.get("count") or 0.0), 4),
            "monitored_article_count": None if max_bin.get("norm") is None else round(float(max_bin["norm"]), 4),
        },
    }


def build_snapshot(
    topic_payloads: Mapping[str, Mapping[str, Any]],
    source_status: Mapping[str, Mapping[str, Any]],
    *,
    captured_at: datetime | None = None,
    source_commit_sha: str | None = None,
) -> dict[str, Any]:
    now = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    topics: dict[str, Any] = {}
    for name in TOPICS:
        payload = topic_payloads.get(name) or {}
        points = extract_timeline_points(payload)
        topics[name] = {
            "query": TOPICS[name]["query"],
            "description": TOPICS[name]["description"],
            **summarize_topic(points, captured_at=now),
        }

    statuses = {name: dict(value) for name, value in source_status.items()}
    live = sorted(name for name, value in statuses.items() if value.get("status") == "LIVE")
    partial = sorted(name for name, value in statuses.items() if value.get("status") == "PARTIAL")
    failed = sorted(name for name, value in statuses.items() if value.get("status") == "ERROR")
    if len(live) == len(TOPICS):
        quality = "COMPLETE"
    elif live or partial:
        quality = "PARTIAL"
    else:
        quality = "DEGRADED"

    ranked = sorted(
        [
            {
                "topic": name,
                "recent_share_pct": row["recent_6h"]["share_pct"],
                "recent_vs_baseline_share_ratio": row["recent_vs_baseline_share_ratio"],
            }
            for name, row in topics.items()
        ],
        key=lambda row: (row["recent_share_pct"] is not None, row["recent_share_pct"] or -1.0),
        reverse=True,
    )

    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "spec": spec(),
        "captured_at": now.isoformat(),
        "source_commit_sha": source_commit_sha,
        "data_quality": quality,
        "coverage": {
            "source_status": statuses,
            "live_topics": live,
            "partial_topics": partial,
            "failed_topics": failed,
            "topic_count": len(TOPICS),
            "live_topic_count": len(live),
        },
        "topics": topics,
        "recent_attention_ranking": ranked,
        "notes": [
            "GDELT attention is a media-coverage proxy and is not treated as verified geopolitical event ground truth.",
            "No composite risk score, direction, threshold signal, or execution decision is produced.",
        ],
    }
