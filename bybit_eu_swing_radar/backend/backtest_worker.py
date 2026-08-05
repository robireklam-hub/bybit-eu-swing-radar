"""Railway cron entry point for Trading Radar historical replay v0.7.0.

Recommended service start command:
    python backtest_worker.py
The process claims a small symbol batch, persists results, and exits.
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback

from backtest import run_backtest_batch


async def main() -> None:
    started = time.perf_counter()
    try:
        result = await run_backtest_batch()
        result["duration_seconds"] = round(time.perf_counter() - started, 2)
        print("Backtest worker complete: " + json.dumps(result, default=str))
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())
