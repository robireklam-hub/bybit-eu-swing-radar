"""Research-only statistical robustness primitives.

Implements threshold-free evidence calculations for:
- Deflated Sharpe Ratio (DSR)
- Probability of Backtest Overfitting (PBO) via CSCV
- local parameter-surface robustness

This module never promotes a feature, mutates live strategy/eligibility, or
authorizes execution. Decision thresholds belong in a frozen preregistered
trial manifest, not in this library.
"""
from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from typing import Any, Mapping, Sequence

SPEC_VERSION = "statistical-robustness-v1"
EULER_MASCHERONI = 0.5772156649015329
MAX_CSCV_COMBINATIONS = 50_000


def spec() -> dict[str, Any]:
    return {
        "spec_version": SPEC_VERSION,
        "research_only": True,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
        "promotion_allowed": False,
        "execution_proof": False,
        "threshold_search": False,
        "requires_preregistered_trial": True,
        "requires_point_in_time_data": True,
        "requires_immutable_oos_before_promotion": True,
        "methods": {
            "deflated_sharpe_ratio": {
                "sharpe_scale": "per_observation",
                "implicit_annualization": False,
                "effective_trial_count": "explicit_caller_input_not_greater_than_recorded_trials",
                "decision_threshold": "not_defined_in_library",
            },
            "probability_backtest_overfitting": {
                "method": "CSCV",
                "contiguous_equal_blocks": True,
                "even_block_count_required": True,
                "decision_threshold": "not_defined_in_library",
            },
            "parameter_surface": {
                "locality": "ordinal_grid_manhattan_distance_1",
                "plateau_tolerance": "optional_caller_input",
                "decision_threshold": "not_defined_in_library",
            },
        },
        "forbidden": [
            "post_hoc_threshold_search",
            "automatic_live_promotion",
            "live_score_mutation",
            "eligibility_mutation",
            "execution_authorization",
            "treating_dsr_or_pbo_alone_as_sufficient_for_promotion",
        ],
    }


def _finite_float(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _finite_sequence(values: Sequence[Any], field: str, *, minimum: int = 1) -> list[float]:
    result = [_finite_float(value, field) for value in values]
    if len(result) < minimum:
        raise ValueError(f"{field} requires at least {minimum} observations")
    return result


def sharpe_ratio_per_observation(returns: Sequence[Any]) -> float:
    """Sample mean divided by sample standard deviation, with no annualization."""
    values = _finite_sequence(returns, "returns", minimum=2)
    deviation = statistics.stdev(values)
    if deviation <= 0:
        raise ValueError("returns must have positive sample standard deviation")
    return statistics.fmean(values) / deviation


def _sample_shape(values: Sequence[float]) -> tuple[float, float]:
    if len(values) < 3:
        raise ValueError("at least three returns are required for DSR shape estimates")
    mean = statistics.fmean(values)
    centered = [value - mean for value in values]
    m2 = statistics.fmean(value * value for value in centered)
    if m2 <= 0:
        raise ValueError("returns must have positive variance")
    m3 = statistics.fmean(value ** 3 for value in centered)
    m4 = statistics.fmean(value ** 4 for value in centered)
    skewness = m3 / (m2 ** 1.5)
    kurtosis = m4 / (m2 * m2)
    return skewness, kurtosis


def _expected_max_sharpe(
    trial_sharpes: Sequence[Any],
    *,
    effective_trials: int,
) -> tuple[float, float]:
    trials = _finite_sequence(trial_sharpes, "trial_sharpes", minimum=1)
    if not isinstance(effective_trials, int) or isinstance(effective_trials, bool):
        raise ValueError("effective_trials must be an integer")
    if effective_trials < 1:
        raise ValueError("effective_trials must be at least 1")
    if effective_trials > len(trials):
        raise ValueError("effective_trials cannot exceed recorded trial_sharpes count")
    trial_variance = statistics.pvariance(trials) if len(trials) > 1 else 0.0
    if effective_trials == 1 or trial_variance <= 0:
        return 0.0, trial_variance

    normal = statistics.NormalDist()
    n = float(effective_trials)
    first_probability = 1.0 - 1.0 / n
    second_probability = 1.0 - 1.0 / (n * math.e)
    z_first = normal.inv_cdf(first_probability)
    z_second = normal.inv_cdf(second_probability)
    expected_max = math.sqrt(trial_variance) * (
        (1.0 - EULER_MASCHERONI) * z_first
        + EULER_MASCHERONI * z_second
    )
    return expected_max, trial_variance


def deflated_sharpe_ratio(
    selected_returns: Sequence[Any],
    trial_sharpes: Sequence[Any],
    *,
    effective_trials: int,
) -> dict[str, Any]:
    """Return DSR evidence without applying any promotion threshold."""
    returns = _finite_sequence(selected_returns, "selected_returns", minimum=3)
    selected_sharpe = sharpe_ratio_per_observation(returns)
    skewness, kurtosis = _sample_shape(returns)
    benchmark, trial_variance = _expected_max_sharpe(
        trial_sharpes,
        effective_trials=effective_trials,
    )
    sampling_variance_term = (
        1.0
        - skewness * selected_sharpe
        + ((kurtosis - 1.0) / 4.0) * selected_sharpe * selected_sharpe
    )
    if sampling_variance_term <= 0:
        raise ValueError("DSR sampling variance term must be positive")
    z_score = (
        (selected_sharpe - benchmark)
        * math.sqrt(len(returns) - 1)
        / math.sqrt(sampling_variance_term)
    )
    probability = statistics.NormalDist().cdf(z_score)
    return {
        "spec_version": SPEC_VERSION,
        "method": "DEFLATED_SHARPE_RATIO",
        "observation_count": len(returns),
        "selected_sharpe_per_observation": selected_sharpe,
        "recorded_trial_count": len(trial_sharpes),
        "effective_trial_count": effective_trials,
        "trial_sharpe_variance": trial_variance,
        "expected_max_sharpe_zero_skill": benchmark,
        "skewness": skewness,
        "kurtosis": kurtosis,
        "sampling_variance_term": sampling_variance_term,
        "z_score": z_score,
        "dsr_probability": probability,
        "implicit_annualization": False,
        "decision_threshold": None,
        "automatic_decision": None,
        "research_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
    }


def _panel(returns_panel: Sequence[Sequence[Any]]) -> list[list[float]]:
    if not returns_panel:
        raise ValueError("returns_panel must not be empty")
    rows = [
        _finite_sequence(row, "returns_panel row", minimum=2)
        for row in returns_panel
    ]
    width = len(rows[0])
    if width < 2:
        raise ValueError("returns_panel requires at least two strategy configurations")
    if any(len(row) != width for row in rows):
        raise ValueError("returns_panel rows must have equal strategy count")
    return rows


def _metric(values: Sequence[float], metric: str) -> float:
    if metric == "mean":
        return statistics.fmean(values)
    if metric == "sharpe":
        return sharpe_ratio_per_observation(values)
    raise ValueError("metric must be 'mean' or 'sharpe'")


def _average_ascending_rank(values: Sequence[float], selected_index: int) -> float:
    selected = values[selected_index]
    less = sum(value < selected for value in values)
    equal = sum(value == selected for value in values)
    return 1.0 + less + (equal - 1.0) / 2.0


def probability_backtest_overfitting(
    returns_panel: Sequence[Sequence[Any]],
    *,
    n_blocks: int,
    metric: str = "sharpe",
    max_combinations: int = MAX_CSCV_COMBINATIONS,
) -> dict[str, Any]:
    """CSCV PBO estimate across a fixed panel of candidate configurations."""
    panel = _panel(returns_panel)
    observations = len(panel)
    strategies = len(panel[0])
    if not isinstance(n_blocks, int) or isinstance(n_blocks, bool):
        raise ValueError("n_blocks must be an integer")
    if n_blocks < 4 or n_blocks % 2:
        raise ValueError("n_blocks must be even and at least 4")
    if observations % n_blocks:
        raise ValueError("observation count must be exactly divisible by n_blocks")
    if metric == "sharpe" and observations // 2 < 2:
        raise ValueError("CSCV sharpe metric requires at least two observations per IS/OOS half")
    if max_combinations < 1:
        raise ValueError("max_combinations must be positive")

    split_count = math.comb(n_blocks, n_blocks // 2)
    if split_count > max_combinations:
        raise ValueError(
            f"CSCV split count {split_count} exceeds max_combinations {max_combinations}"
        )
    block_size = observations // n_blocks
    blocks = [
        list(range(index * block_size, (index + 1) * block_size))
        for index in range(n_blocks)
    ]

    logits: list[float] = []
    relative_ranks: list[float] = []
    selected_counts: Counter[int] = Counter()
    split_rows: list[dict[str, Any]] = []
    all_blocks = set(range(n_blocks))
    for is_blocks_tuple in itertools.combinations(range(n_blocks), n_blocks // 2):
        is_blocks = set(is_blocks_tuple)
        oos_blocks = sorted(all_blocks - is_blocks)
        is_indices = [
            row_index
            for block_index in sorted(is_blocks)
            for row_index in blocks[block_index]
        ]
        oos_indices = [
            row_index
            for block_index in oos_blocks
            for row_index in blocks[block_index]
        ]

        is_metrics = [
            _metric([panel[row][strategy] for row in is_indices], metric)
            for strategy in range(strategies)
        ]
        selected = max(
            range(strategies),
            key=lambda strategy: (is_metrics[strategy], -strategy),
        )
        oos_metrics = [
            _metric([panel[row][strategy] for row in oos_indices], metric)
            for strategy in range(strategies)
        ]
        rank = _average_ascending_rank(oos_metrics, selected)
        relative_rank = rank / (strategies + 1.0)
        logit = math.log(relative_rank / (1.0 - relative_rank))
        relative_ranks.append(relative_rank)
        logits.append(logit)
        selected_counts[selected] += 1
        split_rows.append(
            {
                "is_blocks": sorted(is_blocks),
                "oos_blocks": oos_blocks,
                "selected_config_index": selected,
                "selected_is_metric": is_metrics[selected],
                "selected_oos_metric": oos_metrics[selected],
                "oos_relative_rank": relative_rank,
                "logit": logit,
            }
        )

    pbo = sum(value < 0 for value in logits) / len(logits)
    return {
        "spec_version": SPEC_VERSION,
        "method": "CSCV_PBO",
        "metric": metric,
        "observation_count": observations,
        "strategy_configuration_count": strategies,
        "n_blocks": n_blocks,
        "block_size": block_size,
        "split_count": split_count,
        "pbo": pbo,
        "median_logit": statistics.median(logits),
        "mean_oos_relative_rank": statistics.fmean(relative_ranks),
        "selected_config_counts": dict(sorted(selected_counts.items())),
        "splits": split_rows,
        "decision_threshold": None,
        "automatic_decision": None,
        "research_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
    }


def _parameter_value(value: Any, field: str) -> int | float | str:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"parameter {field} must be numeric or string")
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"parameter {field} must be finite")
        return value
    if isinstance(value, str) and value:
        return value
    raise ValueError(f"parameter {field} must be numeric or non-empty string")


def _value_order(values: set[int | float | str]) -> list[int | float | str]:
    numeric = all(isinstance(value, (int, float)) for value in values)
    text = all(isinstance(value, str) for value in values)
    if not (numeric or text):
        raise ValueError("parameter dimension cannot mix numeric and string values")
    return sorted(values)


def analyze_parameter_surface(
    configurations: Sequence[Mapping[str, Any]],
    *,
    selected_config_id: str,
    plateau_relative_tolerance: float | None = None,
) -> dict[str, Any]:
    """Describe local grid stability around one selected configuration."""
    if not configurations:
        raise ValueError("configurations must not be empty")
    if plateau_relative_tolerance is not None:
        plateau_relative_tolerance = _finite_float(
            plateau_relative_tolerance, "plateau_relative_tolerance"
        )
        if plateau_relative_tolerance < 0:
            raise ValueError("plateau_relative_tolerance must be non-negative")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    parameter_names: tuple[str, ...] | None = None
    for raw in configurations:
        config_id = str(raw.get("config_id") or "")
        if not config_id:
            raise ValueError("each configuration requires config_id")
        if config_id in seen_ids:
            raise ValueError("configuration ids must be unique")
        seen_ids.add(config_id)
        parameters_raw = raw.get("parameters")
        if not isinstance(parameters_raw, Mapping) or not parameters_raw:
            raise ValueError("each configuration requires non-empty parameters")
        names = tuple(sorted(str(name) for name in parameters_raw))
        if parameter_names is None:
            parameter_names = names
        elif names != parameter_names:
            raise ValueError("all configurations must have identical parameter dimensions")
        parameters = {
            name: _parameter_value(parameters_raw[name], name)
            for name in parameter_names
        }
        score = _finite_float(raw.get("score"), "score")
        normalized.append(
            {"config_id": config_id, "parameters": parameters, "score": score}
        )

    if selected_config_id not in seen_ids:
        raise ValueError("selected_config_id is not present")
    assert parameter_names is not None

    axes: dict[str, list[int | float | str]] = {}
    axis_index: dict[str, dict[int | float | str, int]] = {}
    for name in parameter_names:
        ordered = _value_order({row["parameters"][name] for row in normalized})
        axes[name] = ordered
        axis_index[name] = {value: index for index, value in enumerate(ordered)}

    def coordinate(row: Mapping[str, Any]) -> tuple[int, ...]:
        params = row["parameters"]
        return tuple(axis_index[name][params[name]] for name in parameter_names)

    by_coordinate = {coordinate(row): row for row in normalized}
    if len(by_coordinate) != len(normalized):
        raise ValueError("parameter coordinates must be unique")
    selected = next(row for row in normalized if row["config_id"] == selected_config_id)
    selected_coordinate = coordinate(selected)

    expected_neighbor_coordinates: list[tuple[int, ...]] = []
    for dimension, name in enumerate(parameter_names):
        axis_size = len(axes[name])
        for delta in (-1, 1):
            candidate = list(selected_coordinate)
            candidate[dimension] += delta
            if 0 <= candidate[dimension] < axis_size:
                expected_neighbor_coordinates.append(tuple(candidate))
    observed_neighbors = [
        by_coordinate[candidate]
        for candidate in expected_neighbor_coordinates
        if candidate in by_coordinate
    ]
    neighbor_scores = [row["score"] for row in observed_neighbors]

    grid_points = math.prod(len(axes[name]) for name in parameter_names)
    grid_coverage = len(normalized) / grid_points if grid_points else 0.0
    expected_neighbor_count = len(expected_neighbor_coordinates)
    observed_neighbor_count = len(observed_neighbors)
    neighbor_coverage = (
        observed_neighbor_count / expected_neighbor_count
        if expected_neighbor_count
        else 1.0
    )

    selected_score = selected["score"]
    plateau_neighbor_count: int | None = None
    plateau_neighbor_fraction: float | None = None
    if plateau_relative_tolerance is not None:
        scale = max(abs(selected_score), 1e-12)
        floor = selected_score - plateau_relative_tolerance * scale
        plateau_neighbor_count = sum(score >= floor for score in neighbor_scores)
        plateau_neighbor_fraction = (
            plateau_neighbor_count / observed_neighbor_count
            if observed_neighbor_count
            else None
        )

    return {
        "spec_version": SPEC_VERSION,
        "method": "LOCAL_PARAMETER_SURFACE",
        "selected_config_id": selected_config_id,
        "selected_score": selected_score,
        "parameter_names": list(parameter_names),
        "axes": axes,
        "selected_coordinate": list(selected_coordinate),
        "configuration_count": len(normalized),
        "full_grid_point_count": grid_points,
        "grid_coverage_fraction": grid_coverage,
        "grid_complete": len(normalized) == grid_points,
        "expected_adjacent_neighbor_count": expected_neighbor_count,
        "observed_adjacent_neighbor_count": observed_neighbor_count,
        "adjacent_neighbor_coverage_fraction": neighbor_coverage,
        "adjacent_neighbors": [
            {
                "config_id": row["config_id"],
                "parameters": row["parameters"],
                "score": row["score"],
                "score_delta_vs_selected": row["score"] - selected_score,
            }
            for row in observed_neighbors
        ],
        "neighbor_score_median": (
            statistics.median(neighbor_scores) if neighbor_scores else None
        ),
        "neighbor_score_min": min(neighbor_scores) if neighbor_scores else None,
        "neighbor_score_max": max(neighbor_scores) if neighbor_scores else None,
        "selected_minus_neighbor_median": (
            selected_score - statistics.median(neighbor_scores)
            if neighbor_scores
            else None
        ),
        "selected_minus_neighbor_min": (
            selected_score - min(neighbor_scores) if neighbor_scores else None
        ),
        "plateau_relative_tolerance": plateau_relative_tolerance,
        "plateau_observed_neighbor_count": plateau_neighbor_count,
        "plateau_observed_neighbor_fraction": plateau_neighbor_fraction,
        "decision_threshold": None,
        "automatic_decision": None,
        "research_only": True,
        "promotion_allowed": False,
        "live_strategy_mutated": False,
        "production_eligibility_mutated": False,
    }
