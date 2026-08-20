from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from research.microstructure.controlled_pullback_activation_v2 import activation_snapshot
from scripts.production_controlled_pullback_calibration_readiness_v2 import summarize_activation


def test_v2_monitor_reports_frozen_activation_not_rolling_recalibration():
    status = summarize_activation()

    assert status["activation_performed"] is True
    assert status["rolling_recalibration_performed"] is False
    assert status["threshold_recalibration_allowed"] is False
    assert status["experiment_id"] == "microstructure-controlled-pullback-reacceleration-v2"
    assert status["strategy_version"] == "0.7.5"
    assert status["feature_adapter_id"] == "microstructure-controlled-pullback-feature-adapter-v2"
    assert status["calibration_id"] == "microstructure-controlled-pullback-calibration-v2"
    assert status["outcome_visible"] is False
    assert status["promotion_allowed"] is False
    assert set(status["frozen_sample_rows_per_symbol"]) == {"BTCUSDC", "ETHUSDC", "SOLUSDC"}


def test_v2_monitor_fails_closed_if_activation_outcome_gate_opens():
    snapshot = activation_snapshot()
    snapshot["outcome_visible"] = True
    with pytest.raises(ValueError, match="activation contract is invalid"):
        summarize_activation(snapshot)


def test_v2_monitor_fails_closed_on_recalibration_permission():
    snapshot = activation_snapshot()
    snapshot["threshold_recalibration_allowed"] = True
    with pytest.raises(ValueError, match="activation contract is invalid"):
        summarize_activation(snapshot)


def test_v2_monitor_fails_closed_on_symbol_sample_drift():
    snapshot = activation_snapshot()
    snapshot["sample_rows_per_symbol"].pop("ETHUSDC")
    with pytest.raises(ValueError, match="activation contract is invalid"):
        summarize_activation(snapshot)


def test_v2_monitor_fails_closed_if_frozen_sample_is_too_small():
    snapshot = activation_snapshot()
    snapshot["sample_rows_per_symbol"]["SOLUSDC"] = 99
    with pytest.raises(ValueError, match="activation contract is invalid"):
        summarize_activation(snapshot)


def test_v2_activation_guard_bootstraps_backend_imports_when_run_directly(tmp_path):
    script = Path(__file__).resolve().parent / "scripts" / "production_controlled_pullback_calibration_readiness_v2.py"
    env = os.environ.copy()
    env.pop("PRODUCTION_RADAR_API_BASE_URL", None)
    env.pop("PRODUCTION_RADAR_API_KEY", None)
    env.pop("EXPECTED_SHA", None)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "FAIL required controlled-pullback v2 activation configuration is missing" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
