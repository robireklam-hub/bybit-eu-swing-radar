from __future__ import annotations

from datetime import datetime, timezone

from research.geopolitical_event_shadow_v2 import (
    MATERIAL_CONFLICT_QUAD_CLASS,
    MIN_EVENT_COLUMNS,
    SPEC_VERSION,
    build_snapshot,
    normalize_event_columns,
    spec,
)


def event_columns(
    event_id: str,
    *,
    root: int = 1,
    event_code: str = "190",
    event_root_code: str = "19",
    quad_class: int = 4,
    goldstein: float = -10.0,
    mentions: int = 5,
    sources: int = 3,
    articles: int = 4,
    tone: float = -4.0,
    country: str = "UP",
) -> list[str]:
    row = [""] * MIN_EVENT_COLUMNS
    row[0] = event_id
    row[1] = "20260818"
    row[25] = str(root)
    row[26] = event_code
    row[27] = event_code[:2]
    row[28] = event_root_code
    row[29] = str(quad_class)
    row[30] = str(goldstein)
    row[31] = str(mentions)
    row[32] = str(sources)
    row[33] = str(articles)
    row[34] = str(tone)
    row[51] = "4"  # ActionGeo_Type
    row[53] = country  # ActionGeo_CountryCode
    row[59] = "20260818123000"  # DATEADDED
    row[60] = "https://example.com/story"  # SOURCEURL
    return row


def test_spec_is_research_only_and_not_promotable():
    payload = spec()
    assert payload["version"] == SPEC_VERSION
    assert payload["research_only"] is True
    assert payload["label_free"] is True
    assert payload["context_only"] is True
    assert payload["promotion_allowed"] is False
    assert payload["live_strategy_mutated"] is False
    assert payload["historical_backfill_allowed"] is False
    assert payload["schema_min_columns"] == 61
    assert payload["material_conflict_definition"] == "QuadClass == 4"


def test_normalize_event_columns_uses_current_v2_geo_positions():
    item = normalize_event_columns(event_columns("123"))
    assert item is not None
    assert item["global_event_id"] == "123"
    assert item["is_root_event"] is True
    assert item["event_root_code"] == "19"
    assert item["quad_class"] == MATERIAL_CONFLICT_QUAD_CLASS
    assert item["goldstein_scale"] == -10.0
    assert item["num_mentions"] == 5
    assert item["num_sources"] == 3
    assert item["num_articles"] == 4
    assert item["action_geo_type"] == 4
    assert item["action_geo_country_code"] == "UP"
    assert item["date_added"] == "20260818123000"
    assert item["source_url"] == "https://example.com/story"


def test_short_or_schema_invalid_rows_are_rejected():
    assert normalize_event_columns(["x"] * 20) is None
    assert normalize_event_columns([""] * 58) is None

    row = event_columns("123")
    row[29] = "not-an-int"
    assert normalize_event_columns(row) is None

    row = event_columns("123")
    row[53] = "4"
    assert normalize_event_columns(row) is None

    row = event_columns("123")
    row[59] = "not-a-dateadded"
    assert normalize_event_columns(row) is None


def test_snapshot_describes_material_conflict_without_score_or_direction():
    events = [
        normalize_event_columns(event_columns("1", quad_class=4, country="UP")),
        normalize_event_columns(event_columns("2", quad_class=4, goldstein=-6, country="UP")),
        normalize_event_columns(event_columns("3", quad_class=1, goldstein=3, country="US")),
    ]
    snapshot = build_snapshot(
        [item for item in events if item is not None],
        total_rows=3,
        invalid_rows=0,
        source_file={
            "source_file_timestamp": "2026-08-18T12:30:00+00:00",
            "download_url": "https://data.gdeltproject.org/gdeltv2/20260818123000.export.CSV.zip",
        },
        captured_at=datetime(2026, 8, 18, 12, 40, tzinfo=timezone.utc),
        source_commit_sha="abc123",
        prospective_file_count=5,
    )
    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["coverage"]["valid_rows"] == 3
    assert snapshot["coverage"]["root_event_rows"] == 3
    material = snapshot["event_context"]["material_conflict"]
    assert material["event_count"] == 2
    assert material["event_share_pct"] == 66.666667
    assert material["goldstein_le_minus7_count"] == 1
    assert material["top_action_countries"][0] == {"key": "UP", "count": 2}
    assert "risk_score" not in snapshot
    assert "trade_direction" not in snapshot
    assert "decision" not in snapshot


def test_invalid_rows_make_quality_partial_but_not_zero_context():
    item = normalize_event_columns(event_columns("1"))
    snapshot = build_snapshot(
        [item] if item else [],
        total_rows=2,
        invalid_rows=1,
        source_file={"source_file_timestamp": "2026-08-18T12:30:00+00:00"},
        captured_at=datetime(2026, 8, 18, 12, 40, tzinfo=timezone.utc),
    )
    assert snapshot["data_quality"] == "PARTIAL"
    assert snapshot["coverage"]["valid_rows"] == 1
    assert snapshot["coverage"]["invalid_rows"] == 1
