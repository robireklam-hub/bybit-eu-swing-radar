from __future__ import annotations

from datetime import datetime, timedelta, timezone

from research.microstructure.readiness import summarize_readiness


def _row(symbol: str, now: datetime, *, hours: float = 24.0, count: int | None = None,
         ready_ratio: float = 1.0, book_message_ratio: float = 1.0):
    bucket_seconds = 5
    first = now - timedelta(hours=hours)
    expected = int((now - first).total_seconds() // bucket_seconds) + 1
    bucket_count = count if count is not None else expected
    return {
        "symbol": symbol,
        "bucket_count": bucket_count,
        "first_bucket_at": first,
        "last_bucket_at": now - timedelta(seconds=5),
        "book_ready_count": int(bucket_count * ready_ratio),
        "book_message_bucket_count": int(bucket_count * book_message_ratio),
        "trade_bucket_count": int(bucket_count * 0.25),
        "trade_count": 1234,
        "book_message_count": 5678,
    }


def test_readiness_passes_only_when_all_preregistered_quality_gates_pass() -> None:
    now = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
    rows = [_row(symbol, now, hours=25.0) for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC")]
    report = summarize_readiness(rows, ("BTCUSDC", "ETHUSDC", "SOLUSDC"), 5, now=now)

    assert report["research_only"] is True
    assert report["live_strategy_mutated"] is False
    assert report["ready_for_forward_feature_analysis"] is True
    assert report["promotion_allowed"] is False
    assert all(item["ready"] for item in report["symbols"])


def test_readiness_fails_closed_for_missing_symbol() -> None:
    now = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
    report = summarize_readiness([_row("BTCUSDC", now, hours=25.0)], ("BTCUSDC", "ETHUSDC"), 5, now=now)

    assert report["ready_for_forward_feature_analysis"] is False
    eth = next(item for item in report["symbols"] if item["symbol"] == "ETHUSDC")
    assert eth["reasons"] == ["no_buckets"]


def test_readiness_detects_short_history_coverage_and_staleness() -> None:
    now = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
    row = _row("BTCUSDC", now, hours=2.0, ready_ratio=0.80, book_message_ratio=0.70)
    row["bucket_count"] = 500
    row["last_bucket_at"] = now - timedelta(minutes=2)

    report = summarize_readiness([row], ("BTCUSDC",), 5, now=now)
    item = report["symbols"][0]

    assert report["ready_for_forward_feature_analysis"] is False
    assert "insufficient_duration" in item["reasons"]
    assert "insufficient_continuity" in item["reasons"]
    assert "insufficient_book_ready_coverage" in item["reasons"]
    assert "insufficient_book_message_coverage" in item["reasons"]
    assert "stale_or_missing_latest_bucket" in item["reasons"]


def test_trade_bucket_ratio_is_observed_but_not_a_hard_gate() -> None:
    now = datetime(2026, 8, 17, 16, 0, tzinfo=timezone.utc)
    row = _row("BTCUSDC", now, hours=25.0)
    row["trade_bucket_count"] = 0
    row["trade_count"] = 0

    report = summarize_readiness([row], ("BTCUSDC",), 5, now=now)
    item = report["symbols"][0]

    assert item["trade_bucket_ratio"] == 0.0
    assert item["ready"] is True
    assert report["ready_for_forward_feature_analysis"] is True
