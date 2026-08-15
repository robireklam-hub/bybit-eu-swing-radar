from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI

from app.v073_target_path_ab_api import attach_v073_target_path_ab_routes
import target_path_ab_v073 as ab

FIFTEEN_MIN_MS = 15 * 60 * 1000


def bar(index, *, high=100.0, low=90.0, close=95.0):
    return SimpleNamespace(
        start_ms=index * FIFTEEN_MIN_MS,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=10.0,
        turnover=1000.0,
    )


def analysis_with_bars(bars):
    return SimpleNamespace(bars_15m=bars, atr_15m=10.0)


def configure_barrier(monkeypatch):
    monkeypatch.setattr(ab, "DAY_BARRIER_LOOKBACK_15M", 96)
    monkeypatch.setattr(ab, "DAY_BARRIER_PIVOT_LEFT", 2)
    monkeypatch.setattr(ab, "DAY_BARRIER_PIVOT_RIGHT", 2)
    monkeypatch.setattr(ab, "DAY_BARRIER_MIN_PROMINENCE_ATR", 0.10)


def test_active_barrier_keeps_unbroken_confirmed_pivot(monkeypatch):
    configure_barrier(monkeypatch)
    bars = [bar(i) for i in range(12)]
    bars[4] = bar(4, high=120.0, low=90.0, close=100.0)
    result = ab.active_structural_barrier(
        analysis_with_bars(bars), "long", 105.0, 11 * FIFTEEN_MIN_MS
    )
    assert result is not None
    assert result["price"] == 120.0
    assert result["active_barrier_rule"] == "NO_CLOSED_15M_BREAK_BEFORE_TRIGGER"


def test_active_barrier_discards_pivot_broken_by_closed_15m(monkeypatch):
    configure_barrier(monkeypatch)
    bars = [bar(i) for i in range(12)]
    bars[4] = bar(4, high=120.0, low=90.0, close=100.0)
    bars[8] = bar(8, high=121.0, low=95.0, close=121.0)
    bars[9] = bar(9, high=122.0, low=95.0, close=119.0)
    bars[10] = bar(10, high=123.0, low=95.0, close=118.0)
    result = ab.active_structural_barrier(
        analysis_with_bars(bars), "long", 105.0, 11 * FIFTEEN_MIN_MS
    )
    assert result is None


def test_active_barrier_ignores_break_not_closed_before_trigger(monkeypatch):
    configure_barrier(monkeypatch)
    bars = [bar(i) for i in range(12)]
    bars[4] = bar(4, high=120.0, low=90.0, close=100.0)
    bars[10] = bar(10, high=125.0, low=95.0, close=121.0)
    result = ab.active_structural_barrier(
        analysis_with_bars(bars), "long", 105.0, 10 * FIFTEEN_MIN_MS
    )
    assert result is not None
    assert result["price"] == 120.0


def _trade(index, side, net_r=0.2):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    opened = start + timedelta(days=index // 2, minutes=index)
    return {
        "symbol": "TESTUSDC",
        "side": side,
        "opened_at": opened.isoformat(),
        "closed_at": (opened + timedelta(hours=1)).isoformat(),
        "block_index": index % 6,
        "entry": 100.0,
        "stop": 99.0,
        "net_r": net_r,
        "gross_r": net_r + 0.02,
        "mfe_r": 1.0,
        "mae_r": 0.5,
        "exit_reason": "TP2" if net_r > 0 else "STOP",
    }


def test_report_separates_hypothesis_from_production_decision():
    a_trades = [_trade(i, "long" if i % 2 == 0 else "short", 0.15) for i in range(60)]
    b_trades = [_trade(i, "long" if i % 2 == 0 else "short", 0.16) for i in range(62)]
    result = {
        "models": {
            "A_CURRENT_BARRIER": {"counters": ab._empty_counter(), "trades": a_trades},
            "B_ACTIVE_BARRIER": {"counters": ab._empty_counter(), "trades": b_trades},
        },
        "diagnostics": {
            "stale_barriers_removed_by_b": 4,
            "target_paths_recovered_by_b": 2,
        },
    }
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = ab.build_report_from_symbol_results(
        [result], start, start + timedelta(days=180), expected_symbols=1
    )
    assert report["hypothesis_criteria"]["decision"] == "SUPPORTED"
    assert report["production_criteria"]["decision"] == "NO_GO"
    assert report["ab_delta"]["stale_barriers_removed"] == 4
    assert report["ab_delta"]["target_paths_recovered"] == 2
    assert report["validation_policy"]["untouched_forward_holdout_required_before_promotion"] is True


def test_job_parameters_are_single_hypothesis_not_grid():
    params = ab.job_parameters(4)
    assert params["model_a"] == "CURRENT_CONFIRMED_15M_PIVOT_BARRIER"
    assert params["model_b"] == "ONLY_UNBROKEN_CONFIRMED_15M_PIVOT_BARRIER"
    assert "grid" not in params
    assert params["net_rr"] == 1.8


def test_routes_are_research_only_and_separate():
    app = FastAPI()

    def auth():
        return None

    attach_v073_target_path_ab_routes(app, auth)
    paths = {route.path for route in app.routes}
    assert "/v1/day-trade/backtest/target-path-ab/v073/run-batch" in paths
    assert "/v1/day-trade/backtest/target-path-ab/v073/status" in paths
    assert "/v1/day-trade/backtest/target-path-ab/v073/report" in paths
