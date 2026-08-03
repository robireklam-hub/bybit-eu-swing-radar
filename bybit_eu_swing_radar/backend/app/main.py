from datetime import datetime, timezone
from fastapi import Depends, FastAPI, Header, HTTPException, Query

from app.config import settings
from app.providers.bybit import BybitClient
from app.repository import RadarRepository

app = FastAPI(
    title="Bybit EU Swing Radar API",
    version="0.1.0",
    description="Read-only cached swing setup API.",
)

repo = RadarRepository()
bybit = BybitClient()


def require_api_key(x_radar_key: str = Header(..., alias="X-Radar-Key")) -> None:
    if x_radar_key != settings.radar_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
async def health() -> dict:
    now = datetime.now(timezone.utc)
    try:
        upstream = await bybit.server_time()
        bybit_ok = upstream.get("retCode") == 0
    except Exception as exc:
        bybit_ok = False
        upstream = {"error": str(exc)}

    return {
        "status": "ok" if bybit_ok else "degraded",
        "checked_at": now.isoformat(),
        "data_as_of": now.isoformat(),
        "message": "Scanner cache must be populated by the background worker.",
        "bybit_public_api": bybit_ok,
        "upstream_detail": upstream if not bybit_ok else None,
    }


@app.get("/v1/scan", dependencies=[Depends(require_api_key)])
async def scan(
    direction: str = Query("both", pattern="^(long|short|both)$"),
    limit: int = Query(3, ge=1, le=10),
    min_score: float = Query(70, ge=0, le=100),
):
    result = await repo.get_latest_scan(direction, limit, min_score)
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No fresh cached scan. Run and populate the background scanner first.",
        )
    return result


@app.get("/v1/market-regime", dependencies=[Depends(require_api_key)])
async def market_regime():
    result = await repo.get_regime()
    if result is None:
        raise HTTPException(status_code=503, detail="No cached market regime.")
    return result


@app.get("/v1/setup/{symbol}", dependencies=[Depends(require_api_key)])
async def setup(symbol: str):
    result = await repo.get_setup(symbol)
    if result is None:
        raise HTTPException(status_code=404, detail="Setup not found.")
    return result


@app.get("/v1/data-status", dependencies=[Depends(require_api_key)])
async def data_status():
    now = datetime.now(timezone.utc)
    return {
        "checked_at": now.isoformat(),
        "sources": [
            {
                "source": "Bybit EU",
                "status": "ok",
                "data_as_of": now.isoformat(),
                "latency_seconds": 0,
                "missing_fields": [],
            },
            {
                "source": "Coinalyze",
                "status": "partial",
                "data_as_of": None,
                "latency_seconds": None,
                "missing_fields": ["Populated by background worker"],
            },
        ],
    }
