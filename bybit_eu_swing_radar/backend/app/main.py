from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from app.config import settings
from app.providers.bybit import BybitClient
from app.repository import RadarRepository

app = FastAPI(
    title="Bybit EU Swing Radar API",
    version="0.4.4",
    description="Read-only cached USDC swing scanner with compact top-candidate and all-pair momentum endpoints.",
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
    worker_status = await repo.get_data_status()
    worker_ok = bool(worker_status and worker_status.get("worker", {}).get("status") == "ok")
    return {
        "status": "ok" if bybit_ok else "degraded",
        "checked_at": now.isoformat(),
        "data_as_of": worker_status.get("checked_at") if worker_status else now.isoformat(),
        "message": "Scanner cache populated." if worker_ok else "Scanner cache must be populated by the background worker.",
        "bybit_public_api": bybit_ok,
        "worker_ready": worker_ok,
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


@app.get("/v1/top-candidates", dependencies=[Depends(require_api_key)])
async def top_candidates(
    limit: int = Query(3, ge=1, le=5),
    include_watchlist: bool = Query(True),
):
    """Return a compact strict long/short ranking without oversized scan payloads."""
    result = await repo.get_top_candidates(limit, include_watchlist)
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


@app.get("/v1/watchlist", dependencies=[Depends(require_api_key)])
async def watchlist(limit: int = Query(20, ge=1, le=20)):
    result = await repo.get_watchlist(limit)
    if result is None:
        raise HTTPException(status_code=503, detail="No cached watchlist.")
    return result


@app.get("/v1/momentum-radar", dependencies=[Depends(require_api_key)])
async def momentum_radar(
    direction: str = Query("both", pattern="^(long|short|both)$"),
    limit: int = Query(20, ge=1, le=150),
    min_score: float = Query(50, ge=0, le=100),
):
    result = await repo.get_momentum_radar(direction, limit, min_score)
    if result is None:
        raise HTTPException(status_code=503, detail="No cached momentum radar. Run the background worker first.")
    return result


@app.get("/v1/data-status", dependencies=[Depends(require_api_key)])
async def data_status():
    cached = await repo.get_data_status()
    if cached is not None:
        return cached
    now = datetime.now(timezone.utc)
    return {
        "checked_at": now.isoformat(),
        "worker": {"status": "not_run"},
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
            {
                "source": "Bybit EU Spot Margin",
                "status": "partial",
                "data_as_of": None,
                "latency_seconds": None,
                "missing_fields": ["Populated by background worker"],
            },
        ],
    }
