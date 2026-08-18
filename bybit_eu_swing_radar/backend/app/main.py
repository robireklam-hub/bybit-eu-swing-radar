import asyncio
import logging
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from app.config import settings
from app.providers.bybit import BybitClient
from app.repository import RadarRepository
from app.swing_candidate_context import attach_swing_candidate_derivatives
from backtest import BACKTEST_JOB_NAME, STRATEGY_VERSION, run_backtest_batch

app = FastAPI(
    title="Bybit EU Trading Radar API",
    version="0.7.3",
    description="Read-only cached USDC swing/day scanner; day-trade strategy v0.7.3 with context-only derivatives Flow feature v0.7.2.2.",
)

repo = RadarRepository()
bybit = BybitClient()
SOURCE_COMMIT_SHA = os.getenv("RAILWAY_GIT_COMMIT_SHA") or None
logger = logging.getLogger(__name__)
_backtest_task: asyncio.Task[None] | None = None


def require_api_key(x_radar_key: str = Header(..., alias="X-Radar-Key")) -> None:
    if x_radar_key != settings.radar_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


async def _run_backtest_batch_background() -> None:
    """Run one research batch inside Railway without blocking the HTTP request."""
    global _backtest_task
    try:
        result = await run_backtest_batch()
        logger.info("v0.7.3 backtest batch finished: %s", result)
    except Exception:
        logger.exception("v0.7.3 backtest batch failed")
    finally:
        _backtest_task = None


@app.get("/version")
async def version() -> dict[str, str | None]:
    """Return immutable build identity without network or database access."""
    return {"commit_sha": SOURCE_COMMIT_SHA}


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
    """Return compact swing rankings plus context-only candidate derivatives."""
    result = await repo.get_top_candidates(limit, include_watchlist)
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No fresh cached scan. Run and populate the background scanner first.",
        )
    scan_payload = await repo.get_cache("latest_scan")
    return attach_swing_candidate_derivatives(result, scan_payload)


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


@app.get("/v1/day-trade/top-candidates", dependencies=[Depends(require_api_key)])
async def day_trade_top_candidates(
    limit: int = Query(3, ge=1, le=5),
    include_watchlist: bool = Query(True),
):
    result = await repo.get_day_trade_top_candidates(limit, include_watchlist)
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No cached day-trade scan. Run the day-trade worker first.",
        )
    return result


@app.get("/v1/day-trade/scan", dependencies=[Depends(require_api_key)])
async def day_trade_scan(
    direction: str = Query("both", pattern="^(long|short|both)$"),
    limit: int = Query(10, ge=1, le=20),
    min_score: float = Query(0, ge=0, le=100),
    include_watchlist: bool = Query(True),
):
    result = await repo.get_day_trade_scan(
        direction, limit, min_score, include_watchlist
    )
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No cached day-trade scan. Run the day-trade worker first.",
        )
    return result


@app.get("/v1/day-trade/setup/{symbol}", dependencies=[Depends(require_api_key)])
async def day_trade_setup(symbol: str):
    result = await repo.get_day_trade_setup(symbol)
    if result is None:
        raise HTTPException(status_code=404, detail="Day-trade setup not found.")
    return result


@app.get("/v1/day-trade/audit/{symbol}", dependencies=[Depends(require_api_key)])
async def day_trade_audit(symbol: str):
    result = await repo.get_day_trade_audit(symbol)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Day-trade audit snapshot not found for this symbol. Wait for the next day worker run.",
        )
    return result


@app.get("/v1/day-trade/status", dependencies=[Depends(require_api_key)])
async def day_trade_status():
    result = await repo.get_day_trade_status()
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No cached day-trade status. Run the day-trade worker first.",
        )
    return result


@app.get("/v1/day-trade/flow/status", dependencies=[Depends(require_api_key)])
async def day_trade_flow_status():
    result = await repo.get_day_trade_flow_status()
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="No cached day-trade flow status. Run the flow worker first.",
        )
    return result


@app.get("/v1/day-trade/flow/{symbol}", dependencies=[Depends(require_api_key)])
async def day_trade_flow(symbol: str):
    result = await repo.get_day_trade_flow(symbol)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Day-trade flow context not found for this symbol. Wait for a fresh day setup and flow-worker run.",
        )
    return result


@app.get(
    "/v1/day-trade/journal/summary",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_journal_summary(
    days: int = Query(30, ge=1, le=365),
    signal_class: str = Query("all", pattern="^(all|STRICT|SHADOW)$"),
):
    return await repo.get_day_trade_journal_summary(days, signal_class)


@app.get(
    "/v1/day-trade/journal/signals",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_journal_signals(
    status: str = Query("all", pattern="^(all|OPEN|CLOSED)$"),
    signal_class: str = Query("all", pattern="^(all|STRICT|SHADOW)$"),
    symbol: str | None = Query(None, min_length=3, max_length=30),
    limit: int = Query(50, ge=1, le=100),
):
    return await repo.get_day_trade_journal_signals(
        status,
        signal_class,
        symbol,
        limit,
    )


@app.post(
    "/v1/day-trade/backtest/run-batch",
    status_code=202,
    dependencies=[Depends(require_api_key)],
)
async def day_trade_backtest_run_batch():
    """Start exactly one v0.7.3 research replay batch inside Railway."""
    global _backtest_task
    if _backtest_task is not None and not _backtest_task.done():
        raise HTTPException(status_code=409, detail="Backtest batch already running")
    _backtest_task = asyncio.create_task(
        _run_backtest_batch_background(),
        name="v073-backtest-batch",
    )
    return {
        "accepted": True,
        "strategy_version": STRATEGY_VERSION,
        "job_name": BACKTEST_JOB_NAME,
        "execution": "railway_background_batch",
    }


@app.get(
    "/v1/day-trade/backtest/status",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_backtest_status():
    return await repo.get_day_trade_backtest_status()


@app.get(
    "/v1/day-trade/backtest/summary",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_backtest_summary(
    signal_class: str = Query("all", pattern="^(all|STRICT|SHADOW)$"),
    side: str = Query("both", pattern="^(both|long|short)$"),
    primary_only: bool = Query(True),
):
    return await repo.get_day_trade_backtest_summary(
        signal_class, side, primary_only
    )


@app.get(
    "/v1/day-trade/backtest/signals",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_backtest_signals(
    signal_class: str = Query("all", pattern="^(all|STRICT|SHADOW)$"),
    side: str = Query("both", pattern="^(both|long|short)$"),
    symbol: str | None = Query(None, min_length=3, max_length=30),
    primary_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=100),
):
    return await repo.get_day_trade_backtest_signals(
        signal_class, side, symbol, primary_only, limit
    )


@app.get(
    "/v1/day-trade/backtest/diagnostics/status",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_diagnostic_status():
    return await repo.get_day_trade_diagnostic_status()


@app.get(
    "/v1/day-trade/backtest/diagnostics/waterfall",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_gate_waterfall(
    side: str = Query("both", pattern="^(both|long|short)$"),
    split: str = Query("all", pattern="^(all|DEVELOPMENT|VALIDATION)$"),
    universe_group: str = Query("all", pattern="^(all|MAJOR_LIQUID|OTHER)$"),
    primary_only: bool = Query(False),
):
    return await repo.get_day_trade_gate_waterfall(
        side, split, universe_group, primary_only
    )


@app.get(
    "/v1/day-trade/backtest/diagnostics/edge",
    dependencies=[Depends(require_api_key)],
)
async def day_trade_edge_diagnostics(
    cohort: str = Query(
        "NEAR_STRICT",
        pattern="^(ALL_VALID_CANDIDATES|LIQUID_EXECUTABLE|SCORE_GATES_PASS|NEAR_STRICT|STRICT_ELIGIBLE|STRICT_TRADE)$",
    ),
    side: str = Query("both", pattern="^(both|long|short)$"),
    split: str = Query("all", pattern="^(all|DEVELOPMENT|VALIDATION)$"),
    universe_group: str = Query("all", pattern="^(all|MAJOR_LIQUID|OTHER)$"),
    primary_only: bool = Query(True),
):
    return await repo.get_day_trade_edge_diagnostics(
        cohort, side, split, universe_group, primary_only
    )


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


# Install diagnostics-only performance patches before the v0.7.3 diagnostics
# route module imports run_diagnostic_batch. Live strategy modules are untouched.
from diagnostics_v073_perf import install_performance_patch

install_performance_patch()

# v0.7.3 research-only gate diagnostics are attached separately so the live
# day-trade strategy/scoring/execution code remains untouched.
from app.v073_diagnostics_api import attach_v073_diagnostic_routes
from app.v073_diagnostics_perf_api import attach_v073_diagnostic_perf_routes

attach_v073_diagnostic_routes(app, require_api_key)
attach_v073_diagnostic_perf_routes(app, require_api_key)

# Research-only Bybit EU order-book proxy used by the prospective swing liquidity
# shadow collector. It has no strategy/scoring/execution mutation path.
from app.research_swing_liquidity_api import attach_research_swing_liquidity_routes

attach_research_swing_liquidity_routes(app, require_api_key)

# Research-only derivatives positioning shadow. It consumes cached context only
# and has no strategy/scoring/eligibility/execution mutation path.
from app.research_derivatives_positioning_api import attach_derivatives_positioning_research

attach_derivatives_positioning_research(app, require_api_key)

# Research-only event/tokenomics shadow. It records catalyst context and
# has no strategy/scoring/eligibility/execution mutation path.
from app.research_event_tokenomics_api import attach_event_tokenomics_research

attach_event_tokenomics_research(app, require_api_key)

# Research-only BTC macro/cycle/ETF shadow. It records descriptive context
# and has no strategy/scoring/eligibility/execution mutation path.
from app.research_btc_macro_cycle_etf_api import attach_btc_macro_cycle_etf_research

attach_btc_macro_cycle_etf_research(app, require_api_key)

# Research-only relative-strength shadow. It ranks the bounded Bybit EU USDC
# universe using closed daily price history and has no live strategy mutation.
from app.research_relative_strength_api import attach_relative_strength_research

attach_relative_strength_research(app, require_api_key)

# Research-only prospective sweep-effect status. It reads already-closed v0.7.3
# journal rows behind a label-blind sample gate and never mutates live strategy.
from app.research_sweep_effect_api import attach_sweep_effect_research

attach_sweep_effect_research(app, require_api_key)

# Research-only ETH on-chain shadow. It records closed-day network context
# and has no strategy/scoring/eligibility/execution mutation path.
from app.research_eth_onchain_api import attach_eth_onchain_research

attach_eth_onchain_research(app, require_api_key)

# Research-only sourced sector-taxonomy / rotation shadow. It aggregates
# provider functional tags over relative strength without live mutation.
from app.research_sector_rotation_api import attach_sector_rotation_research

attach_sector_rotation_research(app, require_api_key)

# Research-only Cross-Layer Context v2. V1 remains immutable; V2 adds sourced
# sector rotation plus BTC/ETH on-chain context without live mutation.
from app.research_cross_layer_context_v2_api import attach_cross_layer_context_v2_research

attach_cross_layer_context_v2_research(app, require_api_key)
