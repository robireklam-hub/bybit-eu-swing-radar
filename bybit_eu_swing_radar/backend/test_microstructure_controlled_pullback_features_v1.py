from datetime import datetime, timedelta, timezone

import pytest

from research.microstructure.controlled_pullback_features_v1 import (
    adapter_contract,
    derive_calibration_feature_rows,
)


def _bucket(symbol: str, at: datetime, *, mid: float, signed: float = 30.0, volume: float = 100.0):
    return {
        "symbol": symbol,
        "bucket_start": at.isoformat(),
        "bucket_seconds": 5,
        "mid": mid,
        "signed_quote_flow": signed,
        "total_quote_volume": volume,
        "bid_added_quote": 50.0,
        "bid_removed_quote": 10.0,
        "ask_added_quote": 10.0,
        "ask_removed_quote": 30.0,
    }


def test_adapter_is_research_only_and_label_blind():
    contract = adapter_contract()
    assert contract["research_only"] is True
    assert contract["label_blind"] is True
    assert contract["outcome_fields_read"] is False
    assert contract["live_strategy_mutation"] is False
    assert contract["gap_interpolation_allowed"] is False


def test_exact_60_second_predecessor_derives_expected_features():
    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    rows = [
        _bucket("BTCUSDC", start, mid=100.0),
        _bucket("BTCUSDC", start + timedelta(seconds=60), mid=101.0),
    ]
    output = derive_calibration_feature_rows(rows)
    assert len(output) == 1
    row = output[0]
    assert row["symbol"] == "BTCUSDC"
    assert row["mid_return_60s_abs"] == pytest.approx(0.01)
    assert row["aggressive_flow_share_abs"] == pytest.approx(0.30)
    assert row["book_pressure_abs"] == pytest.approx(0.60)
    assert set(row) == {
        "symbol",
        "bucket_start",
        "mid_return_60s_abs",
        "aggressive_flow_share_abs",
        "book_pressure_abs",
    }


def test_gap_is_not_interpolated():
    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    rows = [
        _bucket("ETHUSDC", start, mid=100.0),
        _bucket("ETHUSDC", start + timedelta(seconds=55), mid=101.0),
        _bucket("ETHUSDC", start + timedelta(seconds=65), mid=102.0),
    ]
    assert derive_calibration_feature_rows(rows) == []


def test_zero_volume_or_zero_book_churn_is_skipped():
    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    good = _bucket("SOLUSDC", start, mid=100.0)
    zero_volume = _bucket("SOLUSDC", start + timedelta(seconds=60), mid=101.0, volume=0.0)
    assert derive_calibration_feature_rows([good, zero_volume]) == []

    zero_churn = _bucket("SOLUSDC", start + timedelta(seconds=60), mid=101.0)
    for field in ("bid_added_quote", "bid_removed_quote", "ask_added_quote", "ask_removed_quote"):
        zero_churn[field] = 0.0
    assert derive_calibration_feature_rows([good, zero_churn]) == []


def test_unexpected_symbol_or_bucket_interval_fails_closed():
    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="unexpected feature symbol"):
        derive_calibration_feature_rows([_bucket("DOGEUSDC", start, mid=1.0)])

    row = _bucket("BTCUSDC", start, mid=100.0)
    row["bucket_seconds"] = 10
    with pytest.raises(ValueError, match="unexpected bucket_seconds"):
        derive_calibration_feature_rows([row])


def test_direction_sign_does_not_change_calibration_magnitudes():
    start = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    positive = [
        _bucket("BTCUSDC", start, mid=100.0),
        _bucket("BTCUSDC", start + timedelta(seconds=60), mid=101.0, signed=25.0),
    ]
    negative = [
        _bucket("BTCUSDC", start, mid=100.0),
        _bucket("BTCUSDC", start + timedelta(seconds=60), mid=99.0, signed=-25.0),
    ]
    positive_row = derive_calibration_feature_rows(positive)[0]
    negative_row = derive_calibration_feature_rows(negative)[0]
    assert positive_row["mid_return_60s_abs"] == pytest.approx(negative_row["mid_return_60s_abs"], rel=1e-2)
    assert positive_row["aggressive_flow_share_abs"] == negative_row["aggressive_flow_share_abs"]
