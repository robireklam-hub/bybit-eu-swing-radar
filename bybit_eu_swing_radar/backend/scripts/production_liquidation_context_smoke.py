from __future__ import annotations

import json
import os
import time
import urllib.request

BASE = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
KEY = os.environ["PRODUCTION_RADAR_API_KEY"]
EXPECTED_SHA = os.environ["EXPECTED_SHA"]


def request_json(path: str, method: str = "GET", auth: bool = True, timeout: int = 90):
    headers = {"Accept": "application/json"}
    if auth:
        headers["X-Radar-Key"] = KEY
    request = urllib.request.Request(BASE + path, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait_for_exact_sha() -> dict:
    for _ in range(120):
        try:
            version = request_json("/version", auth=False, timeout=15)
            if version.get("commit_sha") == EXPECTED_SHA:
                return version
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"exact production SHA not active: {EXPECTED_SHA}")


def validate_contract(payload: dict) -> None:
    assert payload.get("research_only") is True
    assert payload.get("context_only") is True
    assert payload.get("label_free") is True
    assert payload.get("live_strategy_mutated") is False
    assert payload.get("execution_proof") is False
    assert payload.get("promotion_allowed") is False
    spec = payload.get("spec") or {}
    assert spec.get("version") == "liquidation-context-shadow-v1"
    assert spec.get("max_symbols") == 8
    assert spec.get("max_market_attempts_per_symbol") == 2
    assert spec.get("max_liquidation_symbol_calls") == 16


def main() -> None:
    wait_for_exact_sha()
    captured = request_json(
        "/v1/research/liquidation-context/capture", method="POST", timeout=120
    )
    status = request_json("/v1/research/liquidation-context/status", timeout=60)
    validate_contract(captured)
    validate_contract(status)

    latest = status.get("latest") or {}
    assert latest.get("source_commit_sha") == EXPECTED_SHA
    assert int(latest.get("symbol_count") or 0) == 8
    coverage = latest.get("coverage") or {}
    assert int(coverage.get("total") or 0) == 8
    assert int(coverage.get("available") or 0) >= 1
    metadata = latest.get("metadata") or {}
    calls = int(metadata.get("liquidation_symbol_calls") or 0)
    assert 1 <= calls <= 16

    symbols = latest.get("symbols") or []
    assert len(symbols) == 8
    for row in symbols:
        assert str(row.get("symbol") or "").endswith("USDC")
        assert row.get("execution_proof") is False
        assert row.get("context_only") is True
        if row.get("coverage"):
            assert row.get("state") in {"AVAILABLE_ACTIVITY", "AVAILABLE_ZERO_ACTIVITY"}
            assert row.get("market_symbol")
            assert row.get("exchange")
            assert row.get("quote_asset") in {"USDT", "USDC", "USD"}
        else:
            assert row.get("state") == "UNAVAILABLE"

    summary = {
        "source_commit_sha": latest.get("source_commit_sha"),
        "captured_at": latest.get("captured_at"),
        "coverage": coverage,
        "liquidation_symbol_calls": calls,
        "markets": [
            {
                "symbol": row.get("symbol"),
                "coverage": row.get("coverage"),
                "state": row.get("state"),
                "market_symbol": row.get("market_symbol"),
                "exchange": row.get("exchange"),
                "quote_asset": row.get("quote_asset"),
                "fallback_used": row.get("fallback_used"),
            }
            for row in symbols
        ],
    }
    print("LIQUIDATION_CONTEXT=" + json.dumps(summary, sort_keys=True))
    print("LIQUIDATION FORWARD CONTEXT V1 VERIFIED.")


if __name__ == "__main__":
    main()
