"""Fetch and print the production v0.7.3 read-only sensitivity report."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("PRODUCTION_RADAR_API_BASE_URL", "").rstrip("/")
KEY = os.environ.get("PRODUCTION_RADAR_API_KEY", "")
TOP_K = max(1, min(int(os.environ.get("SENSITIVITY_TOP_K", "20")), 50))


def main() -> int:
    if not BASE or not KEY:
        raise RuntimeError("Production API base URL/key are required")
    query = urllib.parse.urlencode({"top_k": TOP_K})
    url = f"{BASE}/v1/day-trade/backtest/diagnostics/v073/sensitivity?{query}"
    request = urllib.request.Request(url, headers={"X-Radar-Key": KEY})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Sensitivity API HTTP {exc.code}: {body[:1000]}") from exc

    print("V073_SENSITIVITY_REPORT=" + json.dumps(payload, sort_keys=True, default=str))
    rankings = payload.get("rankings") or {}
    for side in ("both", "long", "short"):
        items = rankings.get(side) or []
        print(f"TOP_{side.upper()}_COUNT={len(items)}")
        for item in items[:5]:
            dev = ((item.get("development") or {}).get("overall") or {})
            val = ((item.get("validation") or {}).get("overall") or {})
            print(
                "TOP_%s rank=%s config=%s dev_n=%s dev_avg_r=%s dev_pf=%s "
                "val_n=%s val_avg_r=%s val_pf=%s val_status=%s"
                % (
                    side.upper(), item.get("development_rank"), item.get("config_id"),
                    dev.get("sample_size"), dev.get("average_net_r"), dev.get("profit_factor"),
                    val.get("sample_size"), val.get("average_net_r"), val.get("profit_factor"),
                    item.get("validation_status"),
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
