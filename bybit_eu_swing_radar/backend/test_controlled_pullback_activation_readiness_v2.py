from copy import deepcopy

import pytest

from research.microstructure.controlled_pullback_activation_v2 import activation_snapshot
from scripts.production_controlled_pullback_calibration_readiness_v2 import summarize_activation


def test_v2_monitor_reports_frozen_activation_not_rolling_recalibration():
    status = summarize_activation()

    assert status["activation_performed"] is True
    assert status["rolling_recalibration_performed"] is False
    assert status["threshold_recalibration_allowed"] is False
    assert status["outcome_visible"] is False
    assert status["promotion_allowed"] is False
    assert status["activation_contract_valid"] is True
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
    snapshot = deepcopy(activation_snapshot())
    snapshot["sample_rows_per_symbol"].pop("ETHUSDC")

    with pytest.raises(ValueError, match="activation contract is invalid"):
        summarize_activation(snapshot)


def test_v2_monitor_fails_closed_if_frozen_sample_is_too_small():
    snapshot = activation_snapshot()
    snapshot["sample_rows_per_symbol"]["SOLUSDC"] = 99

    with pytest.raises(ValueError, match="activation contract is invalid"):
        summarize_activation(snapshot)
