from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from sensitivity_v073 import (
    BASELINE_CONFIG,
    SensitivityConfig,
    build_sensitivity_report,
    evaluate_config,
    passes_config,
    production_grid,
    repriced_outcome,
    target_path_passes,
)


def row(
    *, split="DEVELOPMENT", side="long", volume=1.3,
    expansion=55.0, direction=35.0, quality=65.0, setup=70.0,
    barrier=None, mfe=2.1, net=1.8, gross=1.85,
    exit_reason="TP2", conflict=False,
):
    t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    candidate = {
        "metrics": {
            "nearest_structural_barrier": None if barrier is None else {"price": barrier}
        }
    }
    return {
        "strategy_version": "0.7.3", "symbol": "TESTUSDC", "side": side,
        "opened_at": t0, "dataset_split": split, "included_primary": True,
        "candidate_built": True, "pass_reclaim": True,
        "pass_structure_5m": True, "pass_structure_15m": True,
        "pass_tradeable": True, "pass_side_execution_model": True,
        "expansion_score": expansion, "side_direction_score": direction,
        "quality_score": quality, "setup_score": setup,
        "volume_ratio_5m": volume, "entry_price": 100.0,
        "stop_price": 99.0 if side == "long" else 101.0,
        "base_cost_bps": 5.0, "base_exit_reason": exit_reason,
        "base_gross_r": gross, "base_net_r": net, "base_mfe_r": mfe,
        "base_mae_r": 0.5,
        "candidate_payload": {
            "candidate": candidate,
            "sweep_event": {
                "sweep_time": t0.isoformat(),
                "reclaim_time": (t0 + timedelta(minutes=5)).isoformat(),
            },
        },
        "sweep_depth_atr": 0.4, "bars_from_sweep_to_confirmation": 3,
        "timeframe_conflict": conflict,
    }


def test_lower_volume_threshold_expands_exact_sample():
    rows = [row(volume=1.05), row(volume=1.25), row(volume=1.35)]
    strict = evaluate_config(rows, BASELINE_CONFIG, split="DEVELOPMENT")
    relaxed = evaluate_config(rows, SensitivityConfig(min_volume_ratio=1.0), split="DEVELOPMENT")
    assert strict["overall"]["sample_size"] == 1
    assert relaxed["overall"]["sample_size"] == 3


def test_lower_rr_is_repriced_from_mfe_before_stop():
    losing = row(mfe=1.30, net=-1.05, gross=-1.0, exit_reason="STOP")
    outcome_12 = repriced_outcome(losing, 1.2)
    outcome_15 = repriced_outcome(losing, 1.5)
    assert outcome_12 is not None and outcome_12["net_r"] == 1.2
    assert outcome_12["exit_reason"] == "TP2_REPRICED"
    assert outcome_15 is not None and outcome_15["net_r"] == -1.05


def test_structural_target_recomputes_for_lower_rr():
    sample = row(barrier=101.6, mfe=2.1)
    assert target_path_passes(sample, SensitivityConfig(min_net_rr=1.2)) is True
    assert target_path_passes(sample, SensitivityConfig(min_net_rr=1.8)) is False


def test_4h_conflict_is_not_a_gate():
    assert passes_config(row(conflict=True, barrier=None), BASELINE_CONFIG) is True


def test_validation_is_appended_after_development_ranking():
    rows = []
    for _ in range(60):
        rows.append(row(split="DEVELOPMENT", volume=1.05, net=0.4, gross=0.45, mfe=0.8, exit_reason="TIME_EXIT"))
    for _ in range(60):
        rows.append(row(split="DEVELOPMENT", volume=1.35, net=0.2, gross=0.25, mfe=0.8, exit_reason="TIME_EXIT"))
    for _ in range(40):
        rows.append(row(split="VALIDATION", volume=1.05, net=-1.05, gross=-1.0, mfe=0.4, exit_reason="STOP"))
    for _ in range(40):
        rows.append(row(split="VALIDATION", volume=1.35, net=0.2, gross=0.25, mfe=0.8, exit_reason="TIME_EXIT"))
    ranked = build_sensitivity_report(rows, top_k=5)["rankings"]["both"]
    assert ranked
    assert all(item["validation_used_in_rank"] is False for item in ranked)
    assert [item["development_rank"] for item in ranked] == list(range(1, len(ranked) + 1))


def test_target_path_ignore_is_diagnostic_only_not_grid_mutation():
    sample = row(barrier=101.0, mfe=2.0)
    assert target_path_passes(sample, deepcopy(BASELINE_CONFIG)) is False
    assert target_path_passes(sample, SensitivityConfig(target_path_mode="IGNORE")) is True


def test_production_grid_is_972_structural_configs():
    grid = production_grid()
    assert len(grid) == 972
    assert len({(c.min_volume_ratio, c.min_expansion, c.min_direction, c.min_quality, c.min_setup, c.min_net_rr) for c in grid}) == 972
    assert all(c.target_path_mode == "STRUCTURAL" for c in grid)
    assert all(c.max_confirmation_bars == 6 and c.max_reclaim_bars == 3 for c in grid)


def test_baseline_matches_live_v073_threshold_contract():
    assert BASELINE_CONFIG.min_volume_ratio == 1.30
    assert BASELINE_CONFIG.min_expansion == 55.0
    assert BASELINE_CONFIG.min_direction == 35.0
    assert BASELINE_CONFIG.min_quality == 65.0
    assert BASELINE_CONFIG.min_setup == 70.0
    assert BASELINE_CONFIG.min_net_rr == 1.80
    assert BASELINE_CONFIG.max_confirmation_bars == 6
    assert BASELINE_CONFIG.max_reclaim_bars == 3
    assert BASELINE_CONFIG.target_path_mode == "STRUCTURAL"
