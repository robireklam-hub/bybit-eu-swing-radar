from __future__ import annotations

import json
import os
import time
import urllib.request

BASE = os.environ["PRODUCTION_RADAR_API_BASE_URL"].rstrip("/")
KEY = os.environ["PRODUCTION_RADAR_API_KEY"]
EXPECTED_SHA = os.environ["EXPECTED_SHA"]


def request_json(path: str, method: str = "GET", auth: bool = True, timeout: int = 120):
    headers = {"Accept": "application/json"}
    if auth:
        headers["X-Radar-Key"] = KEY
    request = urllib.request.Request(BASE + path, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def wait_exact_sha() -> None:
    for _ in range(120):
        try:
            version = request_json("/version", auth=False, timeout=15)
            if version.get("commit_sha") == EXPECTED_SHA:
                return
        except Exception:
            pass
        time.sleep(2)
    raise RuntimeError(f"exact production SHA not active: {EXPECTED_SHA}")


def main() -> None:
    wait_exact_sha()
    liquidation = request_json(
        "/v1/research/liquidation-context/capture", method="POST", timeout=120
    )
    assert liquidation.get("source_commit_sha") == EXPECTED_SHA
    liq_coverage = liquidation.get("coverage") or {}
    assert int(liq_coverage.get("available") or 0) >= 1

    positioning = request_json(
        "/v1/research/derivatives-positioning/capture", method="POST", timeout=90
    )
    status = request_json("/v1/research/derivatives-positioning/status", timeout=60)
    latest = status.get("latest") or positioning

    assert latest.get("source_commit_sha") == EXPECTED_SHA
    assert latest.get("research_only") is True
    assert latest.get("label_free") is True
    assert latest.get("live_strategy_mutated") is False
    assert latest.get("promotion_allowed") is False
    coverage = latest.get("coverage") or {}
    total = int(coverage.get("total") or 0)
    liquidation_covered = int(coverage.get("liquidations") or 0)
    assert total == 8
    assert liquidation_covered >= 1

    symbols = latest.get("symbols") or {}
    if isinstance(symbols, list):
        symbols = {str(row.get("symbol")): row for row in symbols if isinstance(row, dict)}
    rows = []
    for symbol, row in sorted(symbols.items()):
        liq = (row or {}).get("liquidations") or {}
        rows.append(
            {
                "symbol": symbol,
                "liquidation_state": liq.get("state"),
                "long_liquidations_usd": liq.get("long_liquidations_usd"),
                "short_liquidations_usd": liq.get("short_liquidations_usd"),
                "coverage": ((row or {}).get("coverage") or {}).get("liquidations"),
            }
        )
    assert sum(1 for row in rows if row["coverage"] is True) == liquidation_covered

    print(
        "DERIVATIVES_LIQUIDATION_INTEGRATION="
        + json.dumps(
            {
                "source_commit_sha": latest.get("source_commit_sha"),
                "liquidation_forward_coverage": liq_coverage,
                "positioning_coverage": coverage,
                "symbols": rows,
            },
            sort_keys=True,
        )
    )
    print("DERIVATIVES POSITIONING LIQUIDATION INTEGRATION VERIFIED.")


if __name__ == "__main__":
    main()
