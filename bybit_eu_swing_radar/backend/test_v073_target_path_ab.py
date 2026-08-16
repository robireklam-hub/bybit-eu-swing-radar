from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI

from app.v073_target_path_ab_api import attach_v073_target_path_ab_routes
import target_path_ab_v073 as ab
import target_path_ab_core_v073 as core


INTERVAL = 15 * 60 * 1000


def bar(index, *, high=101.0, low=99.0, close=100.0):
    return SimpleNamespace(
        start_ms=index * INTERVAL,
        high=high,
        low=low,
        close=close,
    )


def analysis_with(bars, atr=2.0):
    return SimpleNamespace(bars_15m=bars, atr_15m=atr)


def test_consumed_nearest_long_barrier_is_skipped_for_farther_fresh_level(monkeypatch):
    monkeypatch.setattr(core, "DAY_BARRIER_LOOKBACK_15M", 96)
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_LEFT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_RIGHT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_MIN_PROMINENCE_ATR", 0.10)

    bars = [bar(i) for i in range(12)]
    bars[2] = bar(2, high=110.0, close=100.0)  # farther barrier, remains fresh
    bars[6] = bar(6, high=105.0, close=100.0)  # nearest barrier
    bars[9] = bar(9, high=106.0, close=106.0)  # closes through 105 -> consumed
    bars[10] = bar(10, high=107.0, close=104.0)  # prevents index 9 becoming pivot

    result = ab.fresh_nearest_structural_barrier(
        analysis_with(bars),
        "long",
        100.0,
        bars[11].start_ms,
        bars[11].start_ms + INTERVAL,
    )
    assert result is not None
    assert result["price"] == 110.0
    assert result["freshness_rule"] == "UNCONSUMED_BY_CLOSED_15M_CLOSE"


def test_wick_through_does_not_consume_long_barrier(monkeypatch):
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_LEFT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_RIGHT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_MIN_PROMINENCE_ATR", 0.10)

    bars = [bar(i) for i in range(10)]
    bars[3] = bar(3, high=105.0, close=100.0)
    bars[6] = bar(6, high=106.0, close=104.5)  # wick above, close below pivot
    bars[7] = bar(7, high=107.0, close=104.0)  # index 6 not a pivot
    result = ab.fresh_nearest_structural_barrier(
        analysis_with(bars),
        "long",
        100.0,
        bars[9].start_ms,
        bars[9].start_ms + INTERVAL,
    )
    assert result is not None
    assert result["price"] == 105.0


def test_consumed_nearest_short_barrier_is_skipped(monkeypatch):
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_LEFT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_RIGHT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_MIN_PROMINENCE_ATR", 0.10)

    bars = [bar(i) for i in range(12)]
    bars[2] = bar(2, low=90.0, close=100.0)   # farther fresh support
    bars[6] = bar(6, low=95.0, close=100.0)   # nearest support
    bars[9] = bar(9, low=94.0, close=94.0)    # closes through 95 -> consumed
    bars[10] = bar(10, low=93.0, close=96.0)  # index 9 not a pivot
    result = ab.fresh_nearest_structural_barrier(
        analysis_with(bars),
        "short",
        100.0,
        bars[11].start_ms,
        bars[11].start_ms + INTERVAL,
    )
    assert result is not None
    assert result["price"] == 90.0



def test_close_after_sweep_but_before_trade_trigger_consumes(monkeypatch):
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_LEFT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_RIGHT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_MIN_PROMINENCE_ATR", 0.10)

    bars = [bar(i) for i in range(10)]
    bars[3] = bar(3, high=105.0, close=100.0)
    bars[7] = bar(7, high=106.0, close=106.0)
    bars[8] = bar(8, high=107.0, close=104.0)
    sweep_start = bars[7].start_ms
    trade_trigger = bars[9].start_ms
    result = ab.fresh_nearest_structural_barrier(
        analysis_with(bars),
        "long",
        100.0,
        sweep_start,
        trade_trigger,
    )
    assert result is None


def test_close_after_trade_trigger_does_not_consume(monkeypatch):
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_LEFT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_PIVOT_RIGHT", 2)
    monkeypatch.setattr(core, "DAY_BARRIER_MIN_PROMINENCE_ATR", 0.10)

    bars = [bar(i) for i in range(10)]
    bars[3] = bar(3, high=105.0, close=100.0)
    bars[7] = bar(7, high=106.0, close=106.0)
    bars[8] = bar(8, high=107.0, close=104.0)
    sweep_start = bars[6].start_ms
    trade_trigger = bars[7].start_ms
    result = ab.fresh_nearest_structural_barrier(
        analysis_with(bars),
        "long",
        100.0,
        sweep_start,
        trade_trigger,
    )
    assert result is not None
    assert result["price"] == 105.0


def test_fresh_mode_changes_only_target_path_fields(monkeypatch):
    candidate = {
        "entry": 100.0,
        "stop": 98.0,
        "targets": [102.2, 103.8, 105.2],
        "expected_rr": 0.9,
        "metrics": {
            "target_path_valid": False,
            "nearest_structural_barrier": {"price": 102.0},
            "barrier_before_tp2": True,
            "barrier_net_rr": 0.9,
        },
    }
    monkeypatch.setattr(core, "fresh_nearest_structural_barrier", lambda *_args, **_kwargs: None)
    result = ab._apply_target_path_mode(
        candidate,
        analysis_with([]),
        "long",
        0,
        0,
        ab.MODEL_FRESH,
    )
    assert result["entry"] == candidate["entry"]
    assert result["stop"] == candidate["stop"]
    assert result["targets"] == candidate["targets"]
    assert result["metrics"]["target_path_valid"] is True
    assert result["metrics"]["nearest_structural_barrier"] is None
    assert abs(result["expected_rr"] - 1.8) < 1e-9

def test_ignore_control_changes_only_target_path_fields():
    candidate = {
        "entry": 100.0,
        "stop": 98.0,
        "targets": [102.2, 103.8, 105.2],
        "expected_rr": 0.9,
        "metrics": {
            "target_path_valid": False,
            "nearest_structural_barrier": {"price": 102.0},
            "barrier_before_tp2": True,
            "barrier_net_rr": 0.9,
        },
    }
    result = ab._apply_target_path_mode(
        candidate,
        analysis_with([]),
        "long",
        0,
        0,
        ab.MODEL_IGNORE,
    )
    assert result["entry"] == candidate["entry"]
    assert result["stop"] == candidate["stop"]
    assert result["targets"] == candidate["targets"]
    assert result["metrics"]["target_path_valid"] is True
    assert result["metrics"]["nearest_structural_barrier"] is None
    assert result["metrics"]["ignored_structural_barrier"] == {"price": 102.0}
    assert abs(result["expected_rr"] - 1.8) < 1e-9


def _trade(index, side, net_r):
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
        "mfe_r": 1.5,
        "mae_r": 0.5,
        "exit_reason": "TP2" if net_r > 0 else "STOP",
    }


def test_go_contract_requires_fresh_to_pass_absolute_and_current_comparison():
    fresh = [
        _trade(i, "long" if i % 2 == 0 else "short", 0.30 if i % 5 else -0.20)
        for i in range(300)
    ]
    current = [
        _trade(i, "long" if i % 2 == 0 else "short", 0.15 if i % 5 else -0.20)
        for i in range(300)
    ]
    ignore = [
        _trade(i, "long" if i % 2 == 0 else "short", 0.35 if i % 5 else -0.30)
        for i in range(300)
    ]
    result = {
        "models": {
            ab.MODEL_CURRENT: {"counters": ab._empty_counter(), "trades": current},
            ab.MODEL_FRESH: {"counters": ab._empty_counter(), "trades": fresh},
            ab.MODEL_IGNORE: {"counters": ab._empty_counter(), "trades": ignore},
        }
    }
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = ab.build_report_from_symbol_results(
        [result], start, start + timedelta(days=180), expected_symbols=1
    )
    assert report["go_criteria"]["decision"] == "GO"
    assert report["go_criteria"]["diagnostic_control"] == ab.MODEL_IGNORE
    assert report["go_criteria"]["checks"]["fresh_average_net_r_gt_current"] is True
    assert report["go_criteria"]["checks"]["fresh_profit_factor_gte_current"] is True



def test_ignore_control_cannot_promote_failed_fresh_model():
    current = [_trade(i, "long" if i % 2 == 0 else "short", 0.05) for i in range(60)]
    fresh = [_trade(i, "long" if i % 2 == 0 else "short", -0.20) for i in range(60)]
    ignore = [
        _trade(i, "long" if i % 2 == 0 else "short", 0.30 if i % 5 else -0.20)
        for i in range(300)
    ]
    result = {
        "models": {
            ab.MODEL_CURRENT: {"counters": ab._empty_counter(), "trades": current},
            ab.MODEL_FRESH: {"counters": ab._empty_counter(), "trades": fresh},
            ab.MODEL_IGNORE: {"counters": ab._empty_counter(), "trades": ignore},
        }
    }
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = ab.build_report_from_symbol_results(
        [result], start, start + timedelta(days=180), expected_symbols=1
    )
    assert report["go_criteria"]["decision"] == "NO_GO"
    assert report["go_criteria"]["promotion_candidate"] == ab.MODEL_FRESH
    assert report["go_criteria"]["diagnostic_control"] == ab.MODEL_IGNORE


def test_job_parameters_are_fixed_and_have_no_grid():
    params = ab.job_parameters(4)
    assert params["model_a"] == "CURRENT_STRUCTURAL_TARGET_PATH"
    assert params["model_b"] == "FRESH_UNCONSUMED_BY_CLOSED_15M_CLOSE"
    assert params["model_c"] == "IGNORE_STRUCTURAL_TARGET_PATH_DIAGNOSTIC_ONLY"
    assert params["freshness_rule"]["wick_or_touch_consumes"] is False
    assert params["freshness_rule"]["check_window"] == "after pivot confirmation through the actual 5m trade trigger"
    assert "grid" not in params


def test_routes_are_separate_and_research_only():
    app = FastAPI()

    def auth():
        return None

    attach_v073_target_path_ab_routes(app, auth)
    paths = {route.path for route in app.routes}
    assert "/v1/day-trade/backtest/target-path-ab/v073/run-batch" in paths
    assert "/v1/day-trade/backtest/target-path-ab/v073/status" in paths
    assert "/v1/day-trade/backtest/target-path-ab/v073/report" in paths
