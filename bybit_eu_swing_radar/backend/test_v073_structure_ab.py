from datetime import datetime, timedelta, timezone

from fastapi import FastAPI

import sweep_research
from app.v073_structure_ab_api import attach_v073_structure_ab_routes
import structure_ab_v073 as ab
from sweep_research import ResearchBar, SweepResearchConfig


def bar(index, *, high=100.0, low=90.0, close=95.0, volume=10.0):
    return ResearchBar(
        start_ms=index * sweep_research.FIVE_MIN_MS,
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
        turnover=1000.0,
    )


def test_last_confirmed_pivot_is_fully_pre_sweep():
    bars = [bar(i) for i in range(12)]
    bars[4] = bar(4, high=120.0, low=90.0, close=100.0)
    bars[8] = bar(8, high=130.0, low=90.0, close=100.0)
    pivot = ab.last_confirmed_pivot_before_sweep(bars, 9, "long")
    assert pivot is not None
    assert pivot["index"] == 4
    assert pivot["level"] == 120.0
    assert pivot["bars_before_sweep"] == 5


def test_pivot_has_no_range_fallback():
    bars = [bar(i, high=100.0 + i, low=90.0 + i) for i in range(15)]
    assert ab.last_confirmed_pivot_before_sweep(bars, 12, "long") is None
    assert ab.last_confirmed_pivot_before_sweep(bars, 12, "short") is None


def test_pivot_structure_event_uses_single_fixed_hypothesis(monkeypatch):
    bars = [bar(i) for i in range(32)]
    bars[17] = bar(17, high=110.0, low=90.0, close=99.0)
    bars[18] = bar(18, high=103.0, low=91.0, close=98.0)
    bars[19] = bar(19, high=102.0, low=92.0, close=97.0)
    bars[20] = bar(20, high=101.0, low=80.0, close=96.0)
    bars[21] = bar(21, high=106.0, low=94.0, close=105.0)
    bars[22] = bar(22, high=113.0, low=100.0, close=111.0, volume=20.0)
    baseline = {
        "research_version": "sweep-research-0.1",
        "research_only": True,
        "side": "long",
        "sweep_index": 20,
        "sweep_detected": True,
        "sweep_level": 90.0,
        "sweep_price": 80.0,
        "sweep_depth": 10.0,
        "sweep_depth_atr": 0.5,
        "sweep_time": sweep_research.iso_from_ms(bars[20].start_ms),
        "reclaim_confirmed": True,
        "reclaim_close": 96.0,
        "reclaim_time": sweep_research.iso_from_ms(bars[20].start_ms),
        "structure_shift_5m": False,
        "structure_shift_level_5m": 120.0,
        "structure_shift_time_5m": None,
        "structure_15m_state": "NOT_EVALUATED",
        "structure_confirmed_15m": False,
        "volume_ratio_5m": None,
        "volume_confirmed": False,
        "bars_from_sweep_to_confirmation": None,
        "candidate_entry": None,
        "candidate_invalidation": 80.0,
        "entry_ready": False,
        "failure_reasons": ["NO_5M_STRUCTURE_SHIFT"],
    }
    monkeypatch.setattr(
        ab,
        "fast_classify_15m_structure",
        lambda *_args, **_kwargs: "NEUTRAL_NON_OPPOSING",
    )
    index = {row.start_ms: i for i, row in enumerate(bars)}
    result = ab.pivot_structure_event(
        baseline,
        bars,
        [],
        index,
        config=SweepResearchConfig(volume_confirmation_ratio=1.3),
    )
    assert result["pivot_index"] == 17
    assert result["structure_shift_level_5m"] == 110.0
    assert result["structure_shift_time_5m"] == sweep_research.iso_from_ms(
        bars[22].start_ms
    )
    assert result["candidate_entry"] == 111.0
    assert result["bars_from_sweep_to_confirmation"] == 2
    assert result["entry_ready"] is True
    assert "NO_5M_STRUCTURE_SHIFT" not in result["failure_reasons"]


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
        "structure_level": 100.5,
        "bars_from_sweep_to_confirmation": 2,
    }


def test_fixed_go_contract_can_pass_without_parameter_selection():
    b_trades = []
    for i in range(300):
        value = 0.25 if i % 5 else -0.20
        b_trades.append(_trade(i, "long" if i % 2 == 0 else "short", value))
    symbol_results = [
        {
            "models": {
                "A_RANGE6": {
                    "counters": ab._empty_counter(),
                    "trades": [_trade(i, "long" if i % 2 == 0 else "short", 0.05) for i in range(60)],
                },
                "B_PIVOT2L2R": {
                    "counters": ab._empty_counter(),
                    "trades": b_trades,
                },
            }
        }
    ]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = ab.build_report_from_symbol_results(
        symbol_results,
        start,
        start + timedelta(days=180),
        expected_symbols=1,
    )
    assert report["go_criteria"]["fixed_before_run"] is True
    assert report["go_criteria"]["decision"] == "GO"
    assert all(report["go_criteria"]["checks"].values())


def test_go_is_no_go_for_small_sample():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    symbol_results = [
        {
            "models": {
                "A_RANGE6": {"counters": ab._empty_counter(), "trades": []},
                "B_PIVOT2L2R": {
                    "counters": ab._empty_counter(),
                    "trades": [_trade(i, "long", 0.4) for i in range(20)],
                },
            }
        }
    ]
    report = ab.build_report_from_symbol_results(
        symbol_results,
        start,
        start + timedelta(days=180),
        expected_symbols=1,
    )
    assert report["go_criteria"]["decision"] == "NO_GO"
    assert report["go_criteria"]["checks"]["primary_sample_gte_300"] is False
    assert report["go_criteria"]["checks"]["short_sample_gte_100"] is False


def test_incomplete_universe_blocks_go():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = {
        "models": {
            "A_RANGE6": {"counters": ab._empty_counter(), "trades": []},
            "B_PIVOT2L2R": {"counters": ab._empty_counter(), "trades": []},
        }
    }
    report = ab.build_report_from_symbol_results(
        [result],
        start,
        start + timedelta(days=180),
        expected_symbols=30,
    )
    assert report["go_criteria"]["checks"]["all_symbols_completed"] is False
    assert report["go_criteria"]["decision"] == "NO_GO"


def test_research_module_does_not_mutate_live_trigger_defaults():
    assert sweep_research.DEFAULT_CONFIG.structure_lookback_5m == 6
    assert sweep_research.DEFAULT_CONFIG.reclaim_window_bars == 3
    assert sweep_research.DEFAULT_CONFIG.max_confirmation_bars == 6
    params = ab.job_parameters(4)
    assert params["model_a"] == "PRE_SWEEP_RANGE_EXTREME_6_BARS"
    assert params["model_b"] == "LAST_CONFIRMED_PIVOT_2L2R"
    assert params["pivot_left"] == 2
    assert params["pivot_right"] == 2
    assert "grid" not in params


def test_routes_are_research_only_and_separate():
    app = FastAPI()

    def auth():
        return None

    attach_v073_structure_ab_routes(app, auth)
    paths = {route.path for route in app.routes}
    assert "/v1/day-trade/backtest/structure-ab/v073/run-batch" in paths
    assert "/v1/day-trade/backtest/structure-ab/v073/status" in paths
    assert "/v1/day-trade/backtest/structure-ab/v073/report" in paths
