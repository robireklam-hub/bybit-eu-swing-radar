"""Research-only geopolitical Event 2.0 context v2.

Consumes one point-in-time GDELT 2.0 Event Database export file and derives
structured descriptive context. It never produces a trade signal, score,
eligibility decision, or execution instruction.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

SPEC_VERSION = "geopolitical-event-shadow-v2"
PROVIDER = "GDELT 2.0 Event Database"
MIN_EVENT_COLUMNS = 58
MATERIAL_CONFLICT_QUAD_CLASS = 4
SEVERE_NEGATIVE_GOLDSTEIN_CUTOFF = -7.0

# Zero-based indices from the stable core Event Database schema. GDELT 2.0
# retains the core event table while adding related tables/fields; v2 parsing
# intentionally depends only on these long-standing core columns.
IDX_GLOBAL_EVENT_ID = 0
IDX_SQLDATE = 1
IDX_IS_ROOT_EVENT = 25
IDX_EVENT_CODE = 26
IDX_EVENT_BASE_CODE = 27
IDX_EVENT_ROOT_CODE = 28
IDX_QUAD_CLASS = 29
IDX_GOLDSTEIN_SCALE = 30
IDX_NUM_MENTIONS = 31
IDX_NUM_SOURCES = 32
IDX_NUM_ARTICLES = 33
IDX_AVG_TONE = 34
IDX_ACTION_GEO_COUNTRY_CODE = 51
IDX_DATE_ADDED = 56
IDX_SOURCE_URL = 57


def spec() -> dict[str, Any]:
    return {
        "version": SPEC_VERSION,
        "provider": PROVIDER,
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "source_family": "STATIC_GDELT_EVENT_EXPORT",
        "source_resolution": "15_MINUTE_FILE",
        "material_conflict_definition": "QuadClass == 4",
        "severe_negative_goldstein_cutoff": SEVERE_NEGATIVE_GOLDSTEIN_CUTOFF,
        "historical_backfill_allowed": False,
        "principles": [
            "one immutable GDELT Event 2.0 export file is one point-in-time observation",
            "event coding is descriptive context and not verified geopolitical ground truth",
            "no composite geopolitical risk score is produced",
            "no bullish/bearish direction or trade threshold is produced",
            "no journal, net-R, post-trade, live score, eligibility, or execution labels are read",
            "provider/file/parse failures remain explicit and are never interpreted as zero conflict",
            "prospective multi-file baselines may be computed only from previously persisted v2 files",
        ],
    }


def _int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value))
    except (TypeError, ValueError):
        return None


def normalize_event_columns(columns: Sequence[str]) -> dict[str, Any] | None:
    """Normalize the stable core fields needed by v2 from one TSV row."""
    if len(columns) < MIN_EVENT_COLUMNS:
        return None
    global_event_id = str(columns[IDX_GLOBAL_EVENT_ID]).strip()
    quad_class = _int(columns[IDX_QUAD_CLASS])
    if not global_event_id or quad_class is None:
        return None
    return {
        "global_event_id": global_event_id,
        "sql_date": str(columns[IDX_SQLDATE]).strip() or None,
        "is_root_event": _int(columns[IDX_IS_ROOT_EVENT]) == 1,
        "event_code": str(columns[IDX_EVENT_CODE]).strip() or None,
        "event_base_code": str(columns[IDX_EVENT_BASE_CODE]).strip() or None,
        "event_root_code": str(columns[IDX_EVENT_ROOT_CODE]).strip() or None,
        "quad_class": quad_class,
        "goldstein_scale": _float(columns[IDX_GOLDSTEIN_SCALE]),
        "num_mentions": _int(columns[IDX_NUM_MENTIONS]) or 0,
        "num_sources": _int(columns[IDX_NUM_SOURCES]) or 0,
        "num_articles": _int(columns[IDX_NUM_ARTICLES]) or 0,
        "avg_tone": _float(columns[IDX_AVG_TONE]),
        "action_geo_country_code": str(columns[IDX_ACTION_GEO_COUNTRY_CODE]).strip() or None,
        "date_added": str(columns[IDX_DATE_ADDED]).strip() or None,
        "source_url": str(columns[IDX_SOURCE_URL]).strip() or None,
    }


def _top(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {"key": key, "count": count}
        for key, count in counter.most_common(limit)
    ]


def _mean(values: Iterable[float | None]) -> float | None:
    rows = [float(value) for value in values if value is not None]
    return round(fmean(rows), 6) if rows else None


def _share(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(100.0 * numerator / denominator, 6)


def build_snapshot(
    events: Iterable[Mapping[str, Any]],
    *,
    total_rows: int,
    invalid_rows: int,
    source_file: Mapping[str, Any],
    captured_at: datetime | None = None,
    source_commit_sha: str | None = None,
    prospective_file_count: int | None = None,
) -> dict[str, Any]:
    now = (captured_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rows = [dict(item) for item in events]
    valid_count = len(rows)
    root_rows = [item for item in rows if item.get("is_root_event")]
    material_rows = [
        item for item in rows
        if int(item.get("quad_class") or 0) == MATERIAL_CONFLICT_QUAD_CLASS
    ]
    root_material_rows = [
        item for item in root_rows
        if int(item.get("quad_class") or 0) == MATERIAL_CONFLICT_QUAD_CLASS
    ]

    quad_counts = Counter(str(item.get("quad_class")) for item in rows)
    root_codes = Counter(
        str(item["event_root_code"])
        for item in rows if item.get("event_root_code")
    )
    countries = Counter(
        str(item["action_geo_country_code"])
        for item in rows if item.get("action_geo_country_code")
    )
    material_countries = Counter(
        str(item["action_geo_country_code"])
        for item in material_rows if item.get("action_geo_country_code")
    )

    severe_negative = sum(
        1 for item in rows
        if item.get("goldstein_scale") is not None
        and float(item["goldstein_scale"]) <= SEVERE_NEGATIVE_GOLDSTEIN_CUTOFF
    )
    material_severe_negative = sum(
        1 for item in material_rows
        if item.get("goldstein_scale") is not None
        and float(item["goldstein_scale"]) <= SEVERE_NEGATIVE_GOLDSTEIN_CUTOFF
    )

    data_quality = (
        "COMPLETE"
        if valid_count > 0 and invalid_rows == 0
        else "PARTIAL"
        if valid_count > 0
        else "DEGRADED"
    )
    file_count = int(prospective_file_count or 0)

    return {
        "research_only": True,
        "label_free": True,
        "context_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "spec": spec(),
        "captured_at": now.isoformat(),
        "source_commit_sha": source_commit_sha,
        "data_quality": data_quality,
        "source_file": dict(source_file),
        "coverage": {
            "total_rows": int(total_rows),
            "valid_rows": valid_count,
            "invalid_rows": int(invalid_rows),
            "valid_row_pct": _share(valid_count, int(total_rows)),
            "root_event_rows": len(root_rows),
            "prospective_file_count_before_capture": file_count,
            "rolling_24h_baseline_ready": file_count >= 96,
            "rolling_24h_baseline_included": False,
        },
        "event_context": {
            "quad_class_counts": dict(sorted(quad_counts.items())),
            "material_conflict": {
                "event_count": len(material_rows),
                "event_share_pct": _share(len(material_rows), valid_count),
                "root_event_count": len(root_material_rows),
                "root_event_share_pct": _share(len(root_material_rows), len(root_rows)),
                "sum_num_mentions": sum(int(item.get("num_mentions") or 0) for item in material_rows),
                "sum_num_sources": sum(int(item.get("num_sources") or 0) for item in material_rows),
                "sum_num_articles": sum(int(item.get("num_articles") or 0) for item in material_rows),
                "mean_goldstein_scale": _mean(item.get("goldstein_scale") for item in material_rows),
                "mean_avg_tone": _mean(item.get("avg_tone") for item in material_rows),
                "goldstein_le_minus7_count": material_severe_negative,
                "goldstein_le_minus7_share_pct": _share(material_severe_negative, len(material_rows)),
                "top_action_countries": _top(material_countries),
            },
            "all_events": {
                "event_count": valid_count,
                "root_event_count": len(root_rows),
                "sum_num_mentions": sum(int(item.get("num_mentions") or 0) for item in rows),
                "sum_num_sources": sum(int(item.get("num_sources") or 0) for item in rows),
                "sum_num_articles": sum(int(item.get("num_articles") or 0) for item in rows),
                "mean_goldstein_scale": _mean(item.get("goldstein_scale") for item in rows),
                "mean_avg_tone": _mean(item.get("avg_tone") for item in rows),
                "goldstein_le_minus7_count": severe_negative,
                "goldstein_le_minus7_share_pct": _share(severe_negative, valid_count),
                "top_event_root_codes": _top(root_codes),
                "top_action_countries": _top(countries),
            },
        },
        "notes": [
            "QuadClass=4 is retained as GDELT Material Conflict context, not a trade signal.",
            "NumMentions/NumSources/NumArticles are provider fields and sums can overlap across event records.",
            "The -7 Goldstein cutoff is a fixed descriptive tail bin, not a fitted threshold.",
            "A 24h baseline is intentionally not backfilled; it becomes eligible only from prospectively persisted v2 files.",
        ],
    }
