from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.research_swing_liquidity_api import (
    compact_orderbook_payload,
    validate_forward_snapshot,
    validate_usdc_symbol,
)


def _snapshot(**overrides):
    payload = {
        "study": "swing-liquidity-validation-v1",
        "research_only": True,
        "label_blind": True,
        "live_gate_unchanged": True,
        "captured_at": "2026-08-18T00:00:00+00:00",
        "scan_data_as_of": "2026-08-17T23:59:00+00:00",
        "candidate_count": 1,
        "candidates": [
            {
                "symbol": "ALTUSDC",
                "side": "long",
                "source_section": "liquidity_blocked",
                "shortable": False,
                "turnover_24h_usdc": 75_000,
                "turnover_tier": "50K_100K",
                "spread_bps": 18.0,
                "spread_tier": "10_20",
                "book_costs": [{"notional_usdc": 500.0, "complete_fill": True}],
            }
        ],
        "orderbooks": {"ALTUSDC": {}},
        "orderbook_errors": {},
    }
    payload.update(overrides)
    return payload


def test_validate_usdc_symbol_is_strictly_usdc_spot_shape():
    assert validate_usdc_symbol("btcusdc") == "BTCUSDC"
    assert validate_usdc_symbol("lausdc") == "LAUSDC"
    for value in ("BTCUSDT", "USDC", "BTC-USDC", "", "X" * 31 + "USDC"):
        with pytest.raises(HTTPException):
            validate_usdc_symbol(value)


def test_forward_snapshot_accepts_short_base_usdc_symbol():
    snapshot = _snapshot()
    snapshot["candidates"][0]["symbol"] = "LAUSDC"
    snapshot["orderbooks"] = {"LAUSDC": {}}
    _, _, candidates = validate_forward_snapshot(snapshot)
    assert candidates[0]["symbol"] == "LAUSDC"


def test_compact_orderbook_payload_is_explicitly_read_only_research():
    payload = compact_orderbook_payload(
        "ALTUSDC",
        {
            "time": 123456789,
            "result": {
                "u": 42,
                "seq": 99,
                "b": [["1.00", "100"]],
                "a": [["1.01", "100"]],
            },
        },
    )
    assert payload["research_only"] is True
    assert payload["live_strategy_mutated"] is False
    assert payload["execution_action"] is False
    assert payload["symbol"] == "ALTUSDC"
    assert payload["bids"] == [["1.00", "100"]]
    assert payload["asks"] == [["1.01", "100"]]


def test_forward_snapshot_validation_is_label_blind_and_usdc_only():
    captured_at, scan_at, candidates = validate_forward_snapshot(_snapshot())
    assert captured_at.isoformat() == "2026-08-18T00:00:00+00:00"
    assert scan_at is not None
    assert candidates[0]["symbol"] == "ALTUSDC"

    bad_label = _snapshot()
    bad_label["candidates"][0]["net_r"] = 1.25
    with pytest.raises(HTTPException, match="forward labels are forbidden"):
        validate_forward_snapshot(bad_label)

    bad_symbol = _snapshot()
    bad_symbol["candidates"][0]["symbol"] = "ALTUSDT"
    with pytest.raises(HTTPException):
        validate_forward_snapshot(bad_symbol)


def test_forward_snapshot_validation_rejects_count_mismatch_and_duplicates():
    with pytest.raises(HTTPException, match="candidate_count does not match"):
        validate_forward_snapshot(_snapshot(candidate_count=2))

    duplicate = _snapshot()
    duplicate["candidate_count"] = 2
    duplicate["candidates"] = duplicate["candidates"] * 2
    with pytest.raises(HTTPException, match="duplicate candidate"):
        validate_forward_snapshot(duplicate)
