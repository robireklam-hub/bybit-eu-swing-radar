from fastapi import FastAPI

from app.v073_research_dataset_api import attach_v073_research_dataset_routes
from research_dataset_v1 import (
    DATASET_VERSION,
    PROFILE_NUMERIC_FEATURES,
    build_profile_report,
    job_parameters,
    profile_numeric_feature,
)

def _row(split, feature, net, **extra):
    row = {
        "dataset_split": split,
        "base_net_r": net,
        "volume_ratio_5m": feature,
        "side": extra.pop("side", "long"),
        "btc_volatility_regime": extra.pop("btc_volatility_regime", "normal"),
        "btc_structure_1h": extra.pop("btc_structure_1h", "bullish"),
        "btc_structure_4h": extra.pop("btc_structure_4h", "bullish"),
        "timeframe_conflict": extra.pop("timeframe_conflict", False),
    }
    for name in PROFILE_NUMERIC_FEATURES:
        row.setdefault(name, feature)
    row.update(extra)
    return row

def test_numeric_profile_learns_cuts_on_discovery_only():
    rows = [_row("DEVELOPMENT", i, 0.1 if i >= 30 else -0.1) for i in range(40)]
    rows += [_row("VALIDATION", 1000 + i, 0.2) for i in range(20)]
    profile = profile_numeric_feature(rows, "volume_ratio_5m")
    assert profile["status"] == "OK"
    cuts = profile["cut_points_discovery_only"]
    assert cuts[2] < 40
    validation = profile["validation_bins"]
    assert sum(item["n"] for item in validation) == 20
    assert validation[-1]["n"] == 20

def test_report_is_exploratory_and_never_promotes():
    rows = [_row("DEVELOPMENT", i, 0.2) for i in range(40)]
    rows += [_row("VALIDATION", i, -0.1) for i in range(40)]
    report = build_profile_report(rows)
    assert report["research_only"] is True
    assert report["live_strategy_mutated"] is False
    assert report["dataset_version"] == DATASET_VERSION
    assert report["interpretation_policy"]["promotion_allowed"] is False
    assert report["counts"]["discovery_evaluable"] == 40
    assert report["counts"]["validation_evaluable"] == 40

def test_job_contract_is_fixed_180d_discovery_validation():
    params = job_parameters(4)
    assert params["lookback_days"] == 180
    assert params["discovery_days"] == 120
    assert params["validation_days"] == 60
    assert params["membership"] == "candidate_built AND pass_structure_5m"
    assert params["hard_gate_filtering"] is False

def test_routes_are_research_only_and_separate():
    app = FastAPI()
    def auth():
        return None
    attach_v073_research_dataset_routes(app, auth)
    paths = {route.path for route in app.routes}
    assert "/v1/day-trade/research/dataset/v1/run-batch" in paths
    assert "/v1/day-trade/research/dataset/v1/status" in paths
    assert "/v1/day-trade/research/dataset/v1/report" in paths
