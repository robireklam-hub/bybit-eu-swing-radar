from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.research_swing_liquidity_api import compact_orderbook_payload, validate_usdc_symbol


def test_validate_usdc_symbol_is_strictly_usdc_spot_shape():
    assert validate_usdc_symbol("btcusdc") == "BTCUSDC"
    for value in ("BTCUSDT", "USDC", "BTC-USDC", "", "X" * 31 + "USDC"):
        with pytest.raises(HTTPException):
            validate_usdc_symbol(value)


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
