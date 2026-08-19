import math

import pytest

from research.statistical_robustness import (
    MAX_CSCV_COMBINATIONS,
    analyze_parameter_surface,
    deflated_sharpe_ratio,
    probability_backtest_overfitting,
    sharpe_ratio_per_observation,
    spec,
)


def test_spec_is_threshold_free_research_only():
    payload = spec()
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["production_eligibility_mutated"] is False
    assert payload["promotion_allowed"] is False
    assert payload["execution_proof"] is False
    assert payload["threshold_search"] is False
    assert payload["requires_preregistered_trial"] is True
    assert payload["requires_point_in_time_data"] is True
    assert payload["requires_immutable_oos_before_promotion"] is True


def test_sharpe_has_no_implicit_annualization():
    returns = [0.01, 0.02, -0.01, 0.03]
    expected = sum(returns) / len(returns)
    expected /= math.sqrt(
        sum((value - sum(returns) / len(returns)) ** 2 for value in returns)
        / (len(returns) - 1)
    )
    assert sharpe_ratio_per_observation(returns) == pytest.approx(expected)


def test_dsr_trial_count_increases_zero_skill_hurdle():
    returns = [0.01, 0.02, -0.005, 0.015, 0.0, 0.025, -0.01, 0.018, 0.012, 0.005] * 10
    trial_sharpes = [0.10, 0.20, 0.15, 0.05, 0.30, 0.12, 0.18, 0.22]
    one = deflated_sharpe_ratio(returns, trial_sharpes, effective_trials=1)
    four = deflated_sharpe_ratio(returns, trial_sharpes, effective_trials=4)
    eight = deflated_sharpe_ratio(returns, trial_sharpes, effective_trials=8)
    assert one["expected_max_sharpe_zero_skill"] == 0.0
    assert (
        one["expected_max_sharpe_zero_skill"]
        < four["expected_max_sharpe_zero_skill"]
        < eight["expected_max_sharpe_zero_skill"]
    )
    assert one["dsr_probability"] > four["dsr_probability"] > eight["dsr_probability"]
    assert eight["implicit_annualization"] is False
    assert eight["decision_threshold"] is None
    assert eight["automatic_decision"] is None
    assert eight["promotion_allowed"] is False


def test_dsr_rejects_invalid_trial_count_and_constant_returns():
    with pytest.raises(ValueError, match="cannot exceed"):
        deflated_sharpe_ratio(
            [0.01, 0.02, 0.0, -0.01],
            [0.1, 0.2],
            effective_trials=3,
        )
    with pytest.raises(ValueError, match="positive sample standard deviation"):
        deflated_sharpe_ratio(
            [0.01, 0.01, 0.01, 0.01],
            [0.1, 0.2],
            effective_trials=2,
        )


def test_pbo_stable_configuration_is_zero_with_mean_metric():
    panel = [[2.0, 1.0, 0.0] for _ in range(8)]
    result = probability_backtest_overfitting(panel, n_blocks=4, metric="mean")
    assert result["split_count"] == 6
    assert result["pbo"] == 0.0
    assert result["selected_config_counts"] == {0: 6}
    assert result["decision_threshold"] is None
    assert result["promotion_allowed"] is False


def test_pbo_detects_block_specific_overfit_panel():
    panel = []
    for block in range(4):
        for _ in range(2):
            row = [0.0] * 4
            row[block] = 4.0
            panel.append(row)
    result = probability_backtest_overfitting(panel, n_blocks=4, metric="mean")
    assert result["pbo"] == 1.0
    assert result["median_logit"] < 0
    assert result["mean_oos_relative_rank"] < 0.5


def test_pbo_validates_blocks_and_combination_guard():
    panel = [[float(index), 0.0] for index in range(10)]
    with pytest.raises(ValueError, match="even and at least 4"):
        probability_backtest_overfitting(panel, n_blocks=5, metric="mean")
    with pytest.raises(ValueError, match="exactly divisible"):
        probability_backtest_overfitting(panel, n_blocks=4, metric="mean")

    large_panel = [[float(index % 3), float((index + 1) % 3)] for index in range(20)]
    with pytest.raises(ValueError, match="exceeds max_combinations"):
        probability_backtest_overfitting(
            large_panel,
            n_blocks=20,
            metric="mean",
            max_combinations=MAX_CSCV_COMBINATIONS,
        )


def _surface_grid():
    rows = []
    for x in [1, 2, 3]:
        for y in [10, 20, 30]:
            rows.append(
                {
                    "config_id": f"{x}-{y}",
                    "parameters": {"x": x, "y": y},
                    "score": 1.0 - 0.05 * (abs(x - 2) + abs(y - 20) / 10),
                }
            )
    return rows


def test_parameter_surface_reports_complete_center_neighborhood():
    result = analyze_parameter_surface(
        _surface_grid(),
        selected_config_id="2-20",
        plateau_relative_tolerance=0.10,
    )
    assert result["full_grid_point_count"] == 9
    assert result["grid_complete"] is True
    assert result["grid_coverage_fraction"] == 1.0
    assert result["expected_adjacent_neighbor_count"] == 4
    assert result["observed_adjacent_neighbor_count"] == 4
    assert result["adjacent_neighbor_coverage_fraction"] == 1.0
    assert result["neighbor_score_median"] == pytest.approx(0.95)
    assert result["selected_minus_neighbor_median"] == pytest.approx(0.05)
    assert result["plateau_observed_neighbor_count"] == 4
    assert result["plateau_observed_neighbor_fraction"] == 1.0
    assert result["decision_threshold"] is None
    assert result["automatic_decision"] is None


def test_parameter_surface_keeps_missing_grid_explicit():
    rows = [
        row
        for row in _surface_grid()
        if row["config_id"] not in {"1-20", "2-10"}
    ]
    result = analyze_parameter_surface(
        rows,
        selected_config_id="2-20",
        plateau_relative_tolerance=0.10,
    )
    assert result["grid_complete"] is False
    assert result["grid_coverage_fraction"] == pytest.approx(7 / 9)
    assert result["expected_adjacent_neighbor_count"] == 4
    assert result["observed_adjacent_neighbor_count"] == 2
    assert result["adjacent_neighbor_coverage_fraction"] == 0.5


def test_parameter_surface_validates_tolerance_and_dimensions():
    with pytest.raises(ValueError, match="non-negative"):
        analyze_parameter_surface(
            _surface_grid(),
            selected_config_id="2-20",
            plateau_relative_tolerance=-0.01,
        )
    bad = _surface_grid()
    bad[0] = {
        "config_id": bad[0]["config_id"],
        "parameters": {"x": 1},
        "score": bad[0]["score"],
    }
    with pytest.raises(ValueError, match="identical parameter dimensions"):
        analyze_parameter_surface(bad, selected_config_id="2-20")
