from datetime import datetime, timedelta, timezone

import pytest

from research.microstructure import controlled_pullback_calibration_v1 as cal_v1
from research.microstructure import controlled_pullback_calibration_v2 as cal_v2
from research.microstructure import controlled_pullback_features_v1 as features_v1
from research.microstructure import controlled_pullback_features_v2 as features_v2


START = datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc)
CUTOFF = START - timedelta(seconds=5)


def _bucket(symbol: str, at: datetime, *, mid: float, signed: float = 30.0):
    return {
        "symbol": symbol,
        "bucket_start": at.isoformat(),
        "bucket_seconds": 5,
        "mid": mid,
        "signed_quote_flow": signed,
        "total_quote_volume": 100.0,
        "bid_added_quote": 50.0,
        "bid_removed_quote": 10.0,
        "ask_added_quote": 10.0,
        "ask_removed_quote": 30.0,
    }


def _feature_rows(count: int = cal_v2.MIN_ROWS_PER_SYMBOL):
    rows = []
    for symbol_index, symbol in enumerate(("BTCUSDC", "ETHUSDC", "SOLUSDC"), start=1):
        for index in range(count):
            rows.append(
                {
                    "symbol": symbol,
                    "bucket_start": CUTOFF - timedelta(seconds=5 * (index + 1)),
                    "mid_return_60s_abs": symbol_index * 0.001 + index * 0.00001,
                    "aggressive_flow_share_abs": symbol_index * 0.01 + index * 0.0001,
                    "book_pressure_abs": symbol_index * 0.02 + index * 0.0002,
                }
            )
    return rows


def test_v2_feature_adapter_keeps_v1_transform_but_v075_identity():
    contract = features_v2.adapter_contract()
    assert contract["feature_adapter_id"] == "microstructure-controlled-pullback-feature-adapter-v2"
    assert contract["parent_feature_adapter_id"] == features_v1.FEATURE_ADAPTER_ID
    assert contract["strategy_version"] == "0.7.5"
    assert contract["feature_data_spec_version"] == "microstructure-forward-alignment-v3"
    assert contract["research_only"] is True
    assert contract["label_blind"] is True
    assert contract["outcome_fields_read"] is False
    assert contract["feature_transform_frozen_from_parent"] is True

    start = datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    rows = [
        _bucket("BTCUSDC", start, mid=100.0),
        _bucket("BTCUSDC", start + timedelta(seconds=60), mid=101.0),
    ]
    assert features_v2.derive_calibration_feature_rows(rows) == features_v1.derive_calibration_feature_rows(rows)


def test_v2_calibration_contract_inherits_frozen_rules_with_v075_identity():
    old = cal_v1.calibration_contract()
    new = cal_v2.calibration_contract()
    assert new["calibration_id"] == "microstructure-controlled-pullback-calibration-v2"
    assert new["parent_calibration_id"] == cal_v1.CALIBRATION_ID
    assert new["strategy_version"] == "0.7.5"
    assert new["feature_adapter_id"] == features_v2.FEATURE_ADAPTER_ID
    assert new["distribution_thresholds"] == old["distribution_thresholds"]
    assert new["structural_thresholds"] == old["structural_thresholds"]
    assert new["governance"] == old["governance"]
    assert new["minimum_rows_per_symbol"] == old["minimum_rows_per_symbol"] == 100
    assert new["calibration_method_frozen_from_parent"] is True
    assert new["outcomes_permitted"] is False


def test_v2_thresholds_match_frozen_v1_numeric_method_for_same_features():
    rows = _feature_rows()
    old = cal_v1.derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)
    new = cal_v2.derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)
    assert new["thresholds_by_symbol"] == old["thresholds_by_symbol"]
    assert new["structural_thresholds"] == old["structural_thresholds"]
    assert new["sample_rows_per_symbol"] == old["sample_rows_per_symbol"]
    assert new["strategy_version"] == "0.7.5"
    assert new["outcome_visible"] is False
    assert new["promotion_allowed"] is False
    assert new["threshold_recalibration_allowed"] is False
    assert new["calibration_method_frozen_from_parent"] is True


def test_v2_calibration_rejects_outcome_fields_and_post_cutoff_rows():
    rows = _feature_rows()
    rows[0]["net_r"] = 1.25
    with pytest.raises(ValueError, match="non-feature fields"):
        cal_v2.derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)

    rows = _feature_rows()
    rows[0]["bucket_start"] = CUTOFF
    with pytest.raises(ValueError, match="not strictly pre-activation"):
        cal_v2.derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)


def test_v2_calibration_requires_every_symbol_and_strict_cutoff_before_start():
    rows = _feature_rows()
    rows.pop(0)
    with pytest.raises(ValueError, match="insufficient pre-activation calibration rows for BTCUSDC"):
        cal_v2.derive_thresholds(rows, calibration_until_utc=CUTOFF, forward_start_utc=START)
    with pytest.raises(ValueError, match="strictly before forward start"):
        cal_v2.derive_thresholds([], calibration_until_utc=START, forward_start_utc=START)
