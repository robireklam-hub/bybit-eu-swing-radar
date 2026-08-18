from __future__ import annotations

from datetime import datetime, timezone

from research.geopolitical_risk_shadow import (
    SPEC_VERSION,
    TOPICS,
    build_snapshot,
    extract_timeline_points,
    spec,
    summarize_topic,
)


def _payload(*rows: tuple[str, float, float]):
    return {
        "timeline": [
            {
                "series": "Volume Intensity",
                "data": [
                    {"date": date, "value": value, "norm": norm}
                    for date, value, norm in rows
                ],
            }
        ]
    }


def test_spec_is_label_free_context_only_without_composite_score():
    payload = spec()
    assert payload["version"] == SPEC_VERSION
    assert payload["research_only"] is True
    assert payload["label_free"] is True
    assert payload["context_only"] is True
    assert payload["promotion_allowed"] is False
    assert payload["live_strategy_mutated"] is False
    assert set(payload["topics"]) == set(TOPICS)
    text = str(payload).lower()
    assert "composite geopolitical score" in text


def test_extract_timeline_raw_points_reads_value_and_norm_and_deduplicates():
    payload = {
        "timeline": [
            {
                "data": [
                    {"date": "20260818T040000Z", "value": 10, "norm": 1000},
                    {"date": "20260818T041500Z", "value": 8, "norm": 1100},
                ]
            },
            {
                "data": [
                    {"date": "20260818T040000Z", "value": 12, "norm": 1000},
                ]
            },
        ]
    }
    points = extract_timeline_points(payload)
    assert len(points) == 2
    assert points[0]["count"] == 12
    assert points[0]["norm"] == 1000


def test_recent_six_hours_and_preceding_eighteen_hours_are_separated():
    captured = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    points = extract_timeline_points(
        _payload(
            ("20260817T130000Z", 5, 1000),
            ("20260818T050000Z", 10, 1000),
            ("20260818T070000Z", 20, 1000),
            ("20260818T110000Z", 30, 1000),
        )
    )
    summary = summarize_topic(points, captured_at=captured)
    assert summary["lookback_24h"]["article_count"] == 65
    assert summary["baseline_18h"]["article_count"] == 15
    assert summary["recent_6h"]["article_count"] == 50
    assert summary["recent_vs_baseline_share_ratio"] > 1


def test_future_bins_are_not_included():
    captured = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    points = extract_timeline_points(
        _payload(
            ("20260818T110000Z", 10, 1000),
            ("20260818T130000Z", 999, 1000),
        )
    )
    summary = summarize_topic(points, captured_at=captured)
    assert summary["lookback_24h"]["article_count"] == 10
    assert summary["latest_bin_at"].startswith("2026-08-18T11:00:00")


def test_snapshot_preserves_missing_coverage_and_has_no_trade_score():
    captured = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    topic_payloads = {
        name: _payload(("20260818T110000Z", index + 1, 1000))
        for index, name in enumerate(TOPICS)
    }
    statuses = {name: {"status": "LIVE"} for name in TOPICS}
    failed = next(iter(TOPICS))
    statuses[failed] = {"status": "ERROR", "reason": "synthetic"}
    topic_payloads[failed] = {}

    snapshot = build_snapshot(
        topic_payloads,
        statuses,
        captured_at=captured,
        source_commit_sha="abc123",
    )
    assert snapshot["data_quality"] == "PARTIAL"
    assert failed in snapshot["coverage"]["failed_topics"]
    assert snapshot["coverage"]["live_topic_count"] == len(TOPICS) - 1
    assert snapshot["source_commit_sha"] == "abc123"
    assert "risk_score" not in snapshot
    assert "direction" not in snapshot
    assert "trade" not in snapshot
