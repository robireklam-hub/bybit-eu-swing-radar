"""Read-only v0.7.3 parameter sensitivity over persisted diagnostic events.

DEVELOPMENT ranks configurations. VALIDATION is appended only after development
ranks are frozen and is never part of the ranking score. This module never
changes live strategy state, execution eligibility, or database contents.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from itertools import product
from typing import Any, Iterable

STRATEGY_VERSION = "0.7.3"
FIVE_MIN_SECONDS = 300


@dataclass(frozen=True)
class SensitivityConfig:
    min_volume_ratio: float = 1.30
    min_expansion: float = 55.0
    min_direction: float = 35.0
    min_quality: float = 65.0
    min_setup: float = 70.0
    min_net_rr: float = 1.80
    max_confirmation_bars: int = 6
    max_reclaim_bars: int = 3
    min_sweep_depth_atr: float = 0.10
    max_sweep_depth_atr: float = 1.00
    target_path_mode: str = "STRUCTURAL"


BASELINE_CONFIG = SensitivityConfig()
GRID_VOLUME = (1.00, 1.10, 1.20, 1.30)
GRID_EXPANSION = (45.0, 50.0, 55.0)
GRID_DIRECTION = (25.0, 30.0, 35.0)
GRID_QUALITY = (55.0, 60.0, 65.0)
GRID_SETUP = (60.0, 65.0, 70.0)
GRID_NET_RR = (1.20, 1.50, 1.80)

LIMITATIONS = [
    "Only persisted primary v0.7.3 diagnostic events with evaluable 8h/20bps outcomes are ranked.",
    "Liquidity/execution eligibility remains fixed in production-compatible ranking; Bybit EU execution constraints are never relaxed.",
    "15m structure confirmation remains fixed and is not optimized.",
    "4H conflict remains observational context only and is never a hard gate.",
    "The stored volume ratio permits exact threshold changes, but changing its 20-bar lookback requires historical re-scan.",
    "Reclaim can only be tightened from 3 bars; widening beyond 3 bars requires historical re-scan.",
    "Sweep-to-confirmation can only be tightened from 6 bars; widening beyond 6 bars requires historical re-scan.",
    "The 5m structure-lookback definition cannot be changed from stored event rows and requires historical re-scan.",
    "Sweep-depth bounds can only be tightened inside the persisted 0.10-1.00 ATR envelope.",
    "Lower TP2/net-R targets up to 1.8R are reconstructed from stored MFE under the same stop-first convention; targets above 1.8R require historical re-scan.",
    "Historical short borrowability is unavailable; short results remain technical research, not execution proof.",
]


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass
    return {}


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def config_id(config: SensitivityConfig) -> str:
    raw = json.dumps(asdict(config), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def production_grid() -> list[SensitivityConfig]:
    """972 production-compatible threshold configurations."""
    return [
        SensitivityConfig(
            min_volume_ratio=v,
            min_expansion=e,
            min_direction=d,
            min_quality=q,
            min_setup=s,
            min_net_rr=r,
        )
        for v, e, d, q, s, r in product(
            GRID_VOLUME, GRID_EXPANSION, GRID_DIRECTION,
            GRID_QUALITY, GRID_SETUP, GRID_NET_RR,
        )
    ]


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    return _obj(row.get("candidate_payload"))


def reclaim_delay_bars(row: dict[str, Any]) -> int | None:
    if "_reclaim_delay" in row:
        value = row.get("_reclaim_delay")
        return None if value is None else int(value)
    sweep = _obj(_payload(row).get("sweep_event"))
    start, reclaim = _dt(sweep.get("sweep_time")), _dt(sweep.get("reclaim_time"))
    if start is None or reclaim is None:
        return None
    seconds = (reclaim - start).total_seconds()
    return None if seconds < 0 else int(round(seconds / FIVE_MIN_SECONDS))


def structural_barrier_price(row: dict[str, Any]) -> float | None:
    if "_barrier" in row:
        value = row.get("_barrier")
        return None if value is None else float(value)
    candidate = _obj(_payload(row).get("candidate"))
    metrics = _obj(candidate.get("metrics"))
    barrier = metrics.get("nearest_structural_barrier")
    if not isinstance(barrier, dict):
        return None
    price = _f(barrier.get("price"))
    return price if price > 0 else None


def _cost_r(row: dict[str, Any]) -> float:
    if "_cost_r" in row:
        return float(row["_cost_r"])
    entry, stop = _f(row.get("entry_price")), _f(row.get("stop_price"))
    risk = abs(entry - stop)
    if entry <= 0 or risk <= 0:
        return max(0.0, _f(row.get("base_gross_r")) - _f(row.get("base_net_r")))
    return max(0.0, entry * _f(row.get("base_cost_bps"), 20.0) / 10_000.0 / risk)


def prepare_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["candidate_payload"] = _payload(row)
        row["_reclaim_delay"] = reclaim_delay_bars(row)
        row["_barrier"] = structural_barrier_price(row)
        row["_cost_r"] = _cost_r(row)
        output.append(row)
    return output


def target_path_passes(row: dict[str, Any], config: SensitivityConfig) -> bool:
    if config.target_path_mode == "IGNORE":
        return True
    if config.target_path_mode != "STRUCTURAL":
        raise ValueError(f"unsupported target_path_mode={config.target_path_mode!r}")
    barrier = structural_barrier_price(row)
    if barrier is None:
        return True
    entry, stop = _f(row.get("entry_price")), _f(row.get("stop_price"))
    risk = abs(entry - stop)
    if entry <= 0 or risk <= 0:
        return False
    distance = config.min_net_rr * risk + entry * _f(row.get("base_cost_bps"), 20.0) / 10_000.0
    side = str(row.get("side") or "")
    if side == "long":
        return not (entry < barrier < entry + distance)
    if side == "short":
        return not (entry - distance < barrier < entry)
    return False


def repriced_outcome(row: dict[str, Any], min_net_rr: float) -> dict[str, Any] | None:
    """Exact lower-target reconstruction for 0 < target <= persisted 1.8R."""
    if min_net_rr <= 0 or min_net_rr > 1.8 + 1e-9:
        return None
    if row.get("base_net_r") is None or row.get("base_gross_r") is None:
        return None
    mfe = _f(row.get("base_mfe_r"))
    if mfe + 1e-9 >= min_net_rr + _cost_r(row):
        return {
            "net_r": float(min_net_rr),
            "exit_reason": "TP2_REPRICED" if min_net_rr < 1.8 - 1e-9 else str(row.get("base_exit_reason") or "TP2"),
            "mfe_r": mfe,
            "mae_r": _f(row.get("base_mae_r")),
        }
    return {
        "net_r": _f(row.get("base_net_r")),
        "exit_reason": str(row.get("base_exit_reason") or "TIME_EXIT"),
        "mfe_r": mfe,
        "mae_r": _f(row.get("base_mae_r")),
    }


def passes_config(row: dict[str, Any], config: SensitivityConfig) -> bool:
    if not all(bool(row.get(field)) for field in (
        "candidate_built", "pass_reclaim", "pass_structure_5m",
        "pass_structure_15m", "pass_tradeable", "pass_side_execution_model",
    )):
        return False
    reclaim = reclaim_delay_bars(row)
    confirmation = row.get("bars_from_sweep_to_confirmation")
    depth_raw = row.get("sweep_depth_atr")
    if reclaim is None or reclaim > config.max_reclaim_bars:
        return False
    if confirmation is None or int(confirmation) > config.max_confirmation_bars:
        return False
    if depth_raw is None:
        return False
    depth = _f(depth_raw)
    if depth + 1e-12 < config.min_sweep_depth_atr or depth - 1e-12 > config.max_sweep_depth_atr:
        return False
    thresholds = (
        ("volume_ratio_5m", config.min_volume_ratio),
        ("expansion_score", config.min_expansion),
        ("side_direction_score", config.min_direction),
        ("quality_score", config.min_quality),
        ("setup_score", config.min_setup),
    )
    if any(_f(row.get(field)) + 1e-12 < threshold for field, threshold in thresholds):
        return False
    return target_path_passes(row, config) and repriced_outcome(row, config.min_net_rr) is not None


def _stats(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    if not outcomes:
        return {
            "sample_size": 0, "tp2_count": 0, "stop_count": 0,
            "time_exit_count": 0, "positive_net_rate_pct": None,
            "average_net_r": None, "median_net_r": None, "total_net_r": 0.0,
            "profit_factor": None, "average_mfe_r": None, "average_mae_r": None,
        }
    values = [float(x["net_r"]) for x in outcomes]
    positives = [x for x in values if x > 0]
    negatives = [x for x in values if x < 0]
    pos_sum, neg_sum = sum(positives), abs(sum(negatives))
    tp2 = sum(1 for x in outcomes if str(x["exit_reason"]).startswith("TP2"))
    stop = sum(1 for x in outcomes if "STOP" in str(x["exit_reason"]))
    return {
        "sample_size": len(values),
        "tp2_count": tp2,
        "stop_count": stop,
        "time_exit_count": len(values) - tp2 - stop,
        "positive_net_rate_pct": round(len(positives) / len(values) * 100.0, 3),
        "average_net_r": round(statistics.fmean(values), 6),
        "median_net_r": round(statistics.median(values), 6),
        "total_net_r": round(sum(values), 6),
        "profit_factor": None if neg_sum <= 1e-12 else round(pos_sum / neg_sum, 6),
        "average_mfe_r": round(statistics.fmean(_f(x["mfe_r"]) for x in outcomes), 6),
        "average_mae_r": round(statistics.fmean(_f(x["mae_r"]) for x in outcomes), 6),
    }


def evaluate_config(
    rows: Iterable[dict[str, Any]],
    config: SensitivityConfig,
    *, side: str = "both", split: str = "DEVELOPMENT",
) -> dict[str, Any]:
    selected: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        if split != "all" and row.get("dataset_split") != split:
            continue
        if side != "both" and row.get("side") != side:
            continue
        if passes_config(row, config):
            outcome = repriced_outcome(row, config.min_net_rr)
            if outcome is not None:
                selected.append((row, outcome))
    result = {"overall": _stats([x for _, x in selected]), "by_side": {}}
    for name in ("long", "short"):
        result["by_side"][name] = _stats([x for row, x in selected if row.get("side") == name])
    return result


def _side_view(result: dict[str, Any], side: str) -> dict[str, Any]:
    if side == "both":
        return result
    empty = _stats([])
    stats = result["by_side"][side]
    return {"overall": stats, "by_side": {
        "long": stats if side == "long" else empty,
        "short": stats if side == "short" else empty,
    }}


def _balance(result: dict[str, Any], side: str) -> float:
    if side != "both":
        return 1.0
    long_n = int(result["by_side"]["long"]["sample_size"])
    short_n = int(result["by_side"]["short"]["sample_size"])
    total = long_n + short_n
    return 0.0 if total <= 0 else 2.0 * min(long_n, short_n) / total


def development_rank_score(result: dict[str, Any], side: str) -> float | None:
    n = int(result["overall"]["sample_size"])
    avg = result["overall"].get("average_net_r")
    if n <= 0 or avg is None:
        return None
    return round(float(avg) * math.sqrt(n) * _balance(result, side), 6)


def evidence_status(result: dict[str, Any], side: str) -> str:
    n = int(result["overall"]["sample_size"])
    minimum, preferred = ((100, 300) if side == "both" else (50, 100))
    if n < minimum:
        return "INSUFFICIENT_SAMPLE"
    if n < preferred:
        return "EARLY_SAMPLE"
    return "TARGET_SAMPLE" if n <= 800 else "LARGE_SAMPLE"


def validation_status(result: dict[str, Any], side: str) -> str:
    n = int(result["overall"]["sample_size"])
    minimum = 30 if side == "both" else 15
    if n < minimum:
        return "INSUFFICIENT_VALIDATION_SAMPLE"
    avg, pf = result["overall"].get("average_net_r"), result["overall"].get("profit_factor")
    return "VALIDATION_PASS" if avg is not None and float(avg) > 0 and (pf is None or float(pf) >= 1.0) else "VALIDATION_FAIL"


def _rank(
    rows: list[dict[str, Any]],
    cache: list[tuple[SensitivityConfig, dict[str, Any]]],
    side: str,
    top_k: int,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for config, combined in cache:
        development = _side_view(combined, side)
        evidence = evidence_status(development, side)
        score = development_rank_score(development, side)
        items.append({
            "config_id": config_id(config), "config": asdict(config),
            "development": development, "evidence_status": evidence,
            "development_rank_score": score,
            "development_edge_status": (
                "POSITIVE_DEVELOPMENT_EDGE"
                if evidence != "INSUFFICIENT_SAMPLE"
                and development["overall"].get("average_net_r") is not None
                and float(development["overall"]["average_net_r"]) > 0
                and (development["overall"].get("profit_factor") is None or float(development["overall"]["profit_factor"]) >= 1.0)
                else "NO_POSITIVE_DEVELOPMENT_EDGE"
            ),
        })
    # Prefer statistically usable configs before comparing the development score.
    items.sort(key=lambda x: (
        x["evidence_status"] != "INSUFFICIENT_SAMPLE",
        x["development_rank_score"] is not None,
        x["development_rank_score"] if x["development_rank_score"] is not None else -1e18,
        x["development"]["overall"]["sample_size"],
    ), reverse=True)
    finalists = items[:top_k]
    # Rank is frozen before validation is touched.
    for rank, item in enumerate(finalists, start=1):
        config = SensitivityConfig(**item["config"])
        validation = evaluate_config(rows, config, side=side, split="VALIDATION")
        item["development_rank"] = rank
        item["validation"] = validation
        item["validation_status"] = validation_status(validation, side)
        item["validation_used_in_rank"] = False
    return finalists


def _one_factor(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    dimensions: dict[str, list[Any]] = {
        "min_volume_ratio": list(GRID_VOLUME),
        "min_expansion": list(GRID_EXPANSION),
        "min_direction": list(GRID_DIRECTION),
        "min_quality": list(GRID_QUALITY),
        "min_setup": list(GRID_SETUP),
        "min_net_rr": list(GRID_NET_RR),
        "max_confirmation_bars": [2, 3, 4, 5, 6],
        "max_reclaim_bars": [0, 1, 2, 3],
        "min_sweep_depth_atr": [0.10, 0.20, 0.30],
        "max_sweep_depth_atr": [0.60, 0.80, 1.00],
    }
    output: dict[str, list[dict[str, Any]]] = {}
    for field, values in dimensions.items():
        output[field] = []
        for value in values:
            config = replace(BASELINE_CONFIG, **{field: value})
            result = evaluate_config(rows, config, side="both", split="DEVELOPMENT")
            output[field].append({
                "value": value, "config_id": config_id(config),
                "development": result,
                "development_rank_score": development_rank_score(result, "both"),
            })
    return output


def _target_path_ab(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "STRUCTURAL": evaluate_config(rows, BASELINE_CONFIG, side="both", split="DEVELOPMENT"),
        "IGNORE_DIAGNOSTIC_ONLY": evaluate_config(rows, replace(BASELINE_CONFIG, target_path_mode="IGNORE"), side="both", split="DEVELOPMENT"),
        "warning": "IGNORE is diagnostic-only and excluded from production-compatible ranking.",
    }


def _liquidity_ab(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bypass: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row["pass_tradeable"] = True
        row["pass_side_execution_model"] = True
        bypass.append(row)
    return {
        "CURRENT_EXECUTION_GATE": evaluate_config(rows, BASELINE_CONFIG, side="both", split="DEVELOPMENT"),
        "IGNORE_LIQUIDITY_EXECUTION_DIAGNOSTIC_ONLY": evaluate_config(bypass, BASELINE_CONFIG, side="both", split="DEVELOPMENT"),
        "warning": "Execution bypass is diagnostic-only and excluded from ranking; live Bybit EU execution constraints remain unchanged.",
    }


def build_sensitivity_report(rows: list[dict[str, Any]], *, top_k: int = 20) -> dict[str, Any]:
    top_k = max(1, min(int(top_k), 50))
    usable = prepare_rows([
        row for row in rows
        if bool(row.get("included_primary"))
        and bool(row.get("candidate_built"))
        and row.get("base_net_r") is not None
        and row.get("strategy_version", STRATEGY_VERSION) == STRATEGY_VERSION
    ])
    grid = production_grid()
    development_cache = [
        (config, evaluate_config(usable, config, side="both", split="DEVELOPMENT"))
        for config in grid
    ]
    return {
        "strategy_version": STRATEGY_VERSION,
        "source_primary_events": len(usable),
        "grid_size": len(grid),
        "top_k": top_k,
        "ranking_method": {
            "selection_split": "DEVELOPMENT",
            "validation_used_in_rank": False,
            "sample_priority": "sufficient sample ranks ahead of insufficient sample",
            "score": "average_net_r * sqrt(sample_size) * long_short_balance_factor",
            "preferred_combined_sample": "300-800",
        },
        "baseline": {
            "config": asdict(BASELINE_CONFIG), "config_id": config_id(BASELINE_CONFIG),
            "all": evaluate_config(usable, BASELINE_CONFIG, side="both", split="all"),
            "development": evaluate_config(usable, BASELINE_CONFIG, side="both", split="DEVELOPMENT"),
            "validation": evaluate_config(usable, BASELINE_CONFIG, side="both", split="VALIDATION"),
        },
        "one_factor_development": _one_factor(usable),
        "target_path_ab_development": _target_path_ab(usable),
        "liquidity_gate_ab_development": _liquidity_ab(usable),
        "rankings": {
            "both": _rank(usable, development_cache, "both", top_k),
            "long": _rank(usable, development_cache, "long", top_k),
            "short": _rank(usable, development_cache, "short", top_k),
        },
        "validation_policy": {
            "validation_used_in_rank": False,
            "baseline_validation_was_previously_inspected": True,
            "validation_consumed_by_this_report": True,
            "warning": "Finalist validation is confirmatory only. Any later parameter iteration or promotion requires a new forward/untouched holdout.",
        },
        "limitations": list(LIMITATIONS),
    }
