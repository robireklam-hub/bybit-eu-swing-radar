from datetime import datetime, timezone
from types import SimpleNamespace

from day_worker import build_day_coinalyze_source_status, build_day_regime


def _analysis(index: int):
    return SimpleNamespace(
        instrument=SimpleNamespace(symbol=f"T{index}USDC"),
        direction_score=0.0,
        atr_ratio_15m=1.0,
        structure_4h="range",
        structure_1h="range",
        structure_15m="range",
    )


def test_budget_bounded_complete_target_is_good():
    now = datetime.now(timezone.utc)
    source = build_day_coinalyze_source_status(
        now=now,
        request_ok=True,
        request_error=None,
        enriched_count=9,
        complete_count=9,
        target_count=9,
        analysis_count=29,
    )
    assert source["status"] == "ok"
    assert source["coverage"] == "9/9"
    assert source["target_complete_coverage"] == "9/9"
    assert source["analysis_complete_coverage"] == "9/29"
    assert source["analysis_coverage_mode"] == "budget_bounded"
    assert source["budget_bounded"] is True
    assert source["missing_fields"] == []


def test_incomplete_target_remains_partial():
    now = datetime.now(timezone.utc)
    source = build_day_coinalyze_source_status(
        now=now,
        request_ok=False,
        request_error="liquidations missing",
        enriched_count=9,
        complete_count=8,
        target_count=9,
        analysis_count=29,
    )
    assert source["status"] == "partial"
    assert source["coverage"] == "8/9"
    assert source["missing_fields"] == ["liquidations missing"]


def test_regime_uses_target_denominator_not_full_analysis_universe():
    now = datetime.now(timezone.utc)
    regime = build_day_regime(
        [_analysis(i) for i in range(29)],
        now,
        True,
        9,
        True,
        coinalyze_complete_symbols=9,
        coinalyze_target_symbols=9,
    )
    assert regime["source_quality"]["Coinalyze derivatives"] == "GOOD"
    assert regime["data_quality"] == "GOOD"
