from datetime import date, datetime, timezone

from research.eth_onchain_shadow import (
    COIN_METRICS,
    CORE_METRICS,
    OPTIONAL_METRICS,
    build_snapshot,
    closed_daily_rows,
    spec,
    summarize_coin_metrics,
    summarize_metric,
)


def _rows(days: int = 40):
    rows = []
    for i in range(1, days + 1):
        day = f"2026-07-{i:02d}" if i <= 31 else f"2026-08-{i-31:02d}"
        rows.append(
            {
                "asset": "eth",
                "time": f"{day}T00:00:00.000000000Z",
                "AdrActCnt": str(500_000 + i),
                "TxCnt": str(1_000_000 + i * 10),
                "FeeTotNtv": str(100 + i / 10),
                "SplyCur": str(120_000_000 + i * 100),
                "SplyCurEL": str(120_000_000 + i * 100),
                "FeePrioTotNtv": str(20 + i / 20),
                "ValidatorActOngCnt": str(900_000 + i),
            }
        )
    return rows


def test_spec_is_eth_context_only_without_pow_mining_metrics():
    payload = spec()
    assert payload["research_only"] is True
    assert payload["label_free"] is True
    assert payload["context_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["execution_proof"] is False
    assert payload["promotion_allowed"] is False
    assert payload["asset"] == "ETH"
    assert payload["coin_metrics"]["core_metrics"] == list(CORE_METRICS)
    assert payload["coin_metrics"]["optional_metrics"] == list(OPTIONAL_METRICS)
    assert payload["coin_metrics"]["request_mode"] == "per_metric_fail_transparent"
    assert payload["coin_metrics"]["optional_missing_is_zero"] is False
    assert "HashRate" not in COIN_METRICS
    assert "DiffMean" not in COIN_METRICS
    assert payload["network_semantics"]["consensus"] == "proof_of_stake"
    assert "bull_bear_score" in payload["forbidden"]


def test_closed_daily_rows_excludes_partial_current_day_and_other_assets():
    rows = [
        {"asset": "eth", "time": "2026-08-17T00:00:00.000000000Z", "TxCnt": "1"},
        {"asset": "eth", "time": "2026-08-18T00:00:00.000000000Z", "TxCnt": "2"},
        {"asset": "btc", "time": "2026-08-17T00:00:00.000000000Z", "TxCnt": "3"},
    ]
    result = closed_daily_rows(rows, closed_through=date(2026, 8, 17))
    assert [item["_day"] for item in result] == ["2026-08-17"]
    assert result[0]["asset"] == "eth"


def test_metric_summary_is_deterministic():
    rows = closed_daily_rows(_rows(), closed_through=date(2026, 8, 9))
    summary = summarize_metric(rows, "TxCnt")
    assert summary["available"] is True
    assert summary["latest_date"] == "2026-08-09"
    assert summary["observations"] == 40
    assert summary["latest"] == 1_000_400.0
    assert summary["mean_7d"] == 1_000_370.0
    assert summary["mean_30d"] == 1_000_255.0
    assert summary["change_30d_pct"] is not None


def test_optional_missing_remains_explicit_and_does_not_remove_core_coverage():
    rows = [
        {
            "asset": "eth",
            "time": "2026-08-17T00:00:00.000000000Z",
            "AdrActCnt": "500000",
            "TxCnt": "1000000",
            "FeeTotNtv": "100",
            "SplyCur": "120000000",
        }
    ]
    summary, available = summarize_coin_metrics(rows, closed_through=date(2026, 8, 17))
    assert set(available) == set(CORE_METRICS)
    assert summary["core_available_metric_count"] == len(CORE_METRICS)
    assert summary["core_missing_metrics"] == []
    assert summary["optional_available_metric_count"] == 0
    assert summary["optional_missing_metrics"] == list(OPTIONAL_METRICS)
    for metric in OPTIONAL_METRICS:
        assert summary["metrics"][metric]["available"] is False
        assert summary["metrics"][metric]["latest"] is None


def test_snapshot_complete_with_all_core_metrics_even_if_optional_missing():
    coin_metrics = {
        "available_metric_count": len(CORE_METRICS),
        "core_available_metric_count": len(CORE_METRICS),
        "core_requested_metric_count": len(CORE_METRICS),
    }
    snapshot = build_snapshot(
        coin_metrics=coin_metrics,
        source_status={"coin_metrics": {"status": "LIVE"}},
        source_commit_sha="abc",
        captured_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
    )
    assert snapshot["data_quality"] == "COMPLETE"
    assert snapshot["research_only"] is True
    assert snapshot["live_strategy_mutated"] is False
    assert snapshot["execution_proof"] is False
    assert snapshot["promotion_allowed"] is False


def test_snapshot_partial_when_core_coverage_is_incomplete():
    snapshot = build_snapshot(
        coin_metrics={
            "available_metric_count": 2,
            "core_available_metric_count": 2,
            "core_requested_metric_count": len(CORE_METRICS),
        },
        source_status={"coin_metrics": {"status": "PARTIAL"}},
        source_commit_sha="abc",
        captured_at=datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc),
    )
    assert snapshot["data_quality"] == "PARTIAL"
    assert snapshot["promotion_allowed"] is False
