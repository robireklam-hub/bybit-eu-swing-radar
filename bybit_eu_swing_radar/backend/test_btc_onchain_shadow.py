from datetime import date, datetime, timezone

from research.btc_onchain_shadow import (
    COIN_METRICS,
    COMMUNITY_EXCLUDED_METRICS,
    build_snapshot,
    closed_daily_rows,
    compact_difficulty,
    compact_fees,
    compact_mempool,
    spec,
    summarize_coin_metrics,
    summarize_metric,
)


def _rows(days: int = 40):
    rows = []
    for i in range(1, days + 1):
        rows.append(
            {
                "asset": "btc",
                "time": f"2026-07-{i:02d}T00:00:00.000000000Z" if i <= 31 else f"2026-08-{i-31:02d}T00:00:00.000000000Z",
                "AdrActCnt": str(1000 + i),
                "TxCnt": str(2000 + i * 2),
                "FeeTotNtv": str(10 + i / 10),
                "HashRate": str(500 + i),
                "DiffMean": str(100 + i),
                "SplyCur": str(19_000_000 + i * 450),
            }
        )
    return rows


def test_spec_is_context_only_and_has_no_directional_score():
    payload = spec()
    assert payload["research_only"] is True
    assert payload["label_free"] is True
    assert payload["context_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["execution_proof"] is False
    assert payload["promotion_allowed"] is False
    assert "bull_bear_score" in payload["forbidden"]
    assert payload["coin_metrics"]["metrics"] == list(COIN_METRICS)
    assert "DiffMean" not in COIN_METRICS
    assert "DiffMean" in COMMUNITY_EXCLUDED_METRICS
    assert payload["coin_metrics"]["excluded_metrics"] == COMMUNITY_EXCLUDED_METRICS


def test_closed_daily_rows_excludes_partial_current_day():
    rows = [
        {"asset": "btc", "time": "2026-08-17T00:00:00.000000000Z", "TxCnt": "1"},
        {"asset": "btc", "time": "2026-08-18T00:00:00.000000000Z", "TxCnt": "2"},
    ]
    result = closed_daily_rows(rows, closed_through=date(2026, 8, 17))
    assert [item["_day"] for item in result] == ["2026-08-17"]


def test_metric_summary_is_deterministic():
    rows = closed_daily_rows(_rows(), closed_through=date(2026, 8, 9))
    summary = summarize_metric(rows, "TxCnt")
    assert summary["available"] is True
    assert summary["latest_date"] == "2026-08-09"
    assert summary["observations"] == 40
    assert summary["latest"] == 2080.0
    assert summary["mean_7d"] == 2074.0
    assert summary["mean_30d"] == 2051.0
    assert summary["change_30d_pct"] is not None


def test_missing_metric_remains_explicit_not_zero():
    rows = [
        {"asset": "btc", "time": "2026-08-16T00:00:00.000000000Z", "TxCnt": "500000"},
        {"asset": "btc", "time": "2026-08-17T00:00:00.000000000Z", "TxCnt": "510000"},
    ]
    summary, available = summarize_coin_metrics(rows, closed_through=date(2026, 8, 17))
    assert available == ["TxCnt"]
    assert summary["metrics"]["TxCnt"]["available"] is True
    assert summary["metrics"]["AdrActCnt"]["available"] is False
    assert summary["metrics"]["AdrActCnt"]["latest"] is None


def test_compact_current_network_payloads_are_bounded():
    assert compact_mempool({"count": 3, "vsize": 4, "total_fee": 5, "ignored": 6}) == {
        "count": 3,
        "vsize": 4,
        "total_fee_sats": 5,
    }
    fees = compact_fees({"fastestFee": 10, "halfHourFee": 8, "hourFee": 6, "economyFee": 3, "minimumFee": 1, "x": 99})
    assert "x" not in fees
    difficulty = compact_difficulty({"progressPercent": 40, "difficultyChange": -1.2, "remainingBlocks": 1000, "ignored": 1})
    assert "ignored" not in difficulty


def test_snapshot_quality_is_partial_and_never_promotable_when_one_source_missing():
    status = {
        "coin_metrics": {"status": "LIVE"},
        "mempool": {"status": "LIVE"},
        "recommended_fees": {"status": "ERROR"},
        "difficulty_adjustment": {"status": "LIVE"},
    }
    snapshot = build_snapshot(
        coin_metrics={"metrics": {}},
        mempool={"count": 1},
        recommended_fees=None,
        difficulty={"progressPercent": 50},
        source_status=status,
        source_commit_sha="abc",
        captured_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
    )
    assert snapshot["data_quality"] == "PARTIAL"
    assert snapshot["research_only"] is True
    assert snapshot["live_strategy_mutated"] is False
    assert snapshot["execution_proof"] is False
    assert snapshot["promotion_allowed"] is False
