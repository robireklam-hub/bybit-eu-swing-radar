"""Railway/manual entry point for Trading Radar v0.7.3 gate diagnostics.

Recommended start command:
    python diagnostic_worker.py
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback

from diagnostics_v073 import run_diagnostic_batch


async def main() -> None:
    started = time.perf_counter()
    try:
        result = await run_diagnostic_batch()
        result["duration_seconds"] = round(time.perf_counter() - started, 2)
        print("Diagnostic worker complete: " + json.dumps(result, default=str))
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())
