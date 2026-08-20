from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

import pytest

from research.microstructure.controlled_pullback_calibration_v2 import MIN_ROWS_PER_SYMBOL
from scripts.production_controlled_pullback_calibration_readiness_v2 import summarize_payloads


def _rows(symbol: str, count: int):
    rows = []
    base = 1_775_000_000
    for index in range(count + 12):
        at = datetime.fromtimestamp(base + index * 5, tz=timezone.utc).isoformat()
        rows.append(
            {
                "symbol": symbol,
                "bucket_start": at,
                "bucket_seconds": 5,
                "mid": 100.0 + index * 0.01,
                "signed_quote_flow": 25.0,
                "total_quote_volume": 100.0,
                "bid_added_quote": 50.0,
                "bid_removed_quote": 10.0,
                "ask_added_quote": 10.0,
                "ask_removed_quote": 30.0,
            }
        )
    return rows


def _payload(symbol: str, eligible_count: int):
    return {
        "research_only": True,
        "label_blind": True,
        "outcome_fields_read": False,
        "promotion_allowed": False,
        "rows": _rows(symbol, eligible_count),
    }


def test_v2_readiness_reports_identity_and_never_activates():
    payloads = {
        symbol: _payload(symbol, MIN_ROWS_PER_SYMBOL)
        for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC")
    }
    status = summarize_payloads(payloads)
    assert status["calibration_sample_ready"] is True
    assert status["experiment_id"] == "microstructure-controlled-pullback-reacceleration-v2"
    assert status["strategy_version"] == "0.7.5"
    assert status["feature_adapter_id"] == "microstructure-controlled-pullback-feature-adapter-v2"
    assert status["calibration_id"] == "microstructure-controlled-pullback-calibration-v2"
    assert status["calibration_method_frozen_from_parent"] is True
    assert status["outcome_visible"] is False
    assert status["promotion_allowed"] is False
    assert status["activation_performed"] is False


def test_v2_readiness_requires_minimum_sample_for_each_symbol():
    payloads = {
        "BTCUSDC": _payload("BTCUSDC", MIN_ROWS_PER_SYMBOL),
        "ETHUSDC": _payload("ETHUSDC", MIN_ROWS_PER_SYMBOL),
        "SOLUSDC": _payload("SOLUSDC", MIN_ROWS_PER_SYMBOL - 1),
    }
    status = summarize_payloads(payloads)
    assert status["calibration_sample_ready"] is False
    assert status["missing_sample_symbols"] == ["SOLUSDC"]


def test_v2_readiness_rejects_bucket_contract_mutation():
    payloads = {
        symbol: _payload(symbol, MIN_ROWS_PER_SYMBOL)
        for symbol in ("BTCUSDC", "ETHUSDC", "SOLUSDC")
    }
    payloads["ETHUSDC"]["outcome_fields_read"] = True
    with pytest.raises(ValueError, match="label-blind contract failed"):
        summarize_payloads(payloads)


def test_v2_readiness_script_bootstraps_backend_imports_when_run_directly(tmp_path):
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
    assert "FAIL required calibration v2 readiness configuration is missing" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr
