from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


DataQuality = Literal["GOOD", "PARTIAL", "DEGRADED"]
SetupState = Literal["NO_TRADE", "WATCH", "ARMED", "TRIGGERED", "MANAGED", "INVALIDATED", "EXPIRED"]
Side = Literal["long", "short", "neutral"]


class MarketRegime(BaseModel):
    data_as_of: datetime
    data_quality: DataQuality
    btc_regime: str
    btc_structure_1d: str | None = None
    btc_structure_4h: str | None = None
    alt_breadth: float | None = None
    volatility_regime: str
    preferred_side: Literal["long", "short", "neutral"]
    source_quality: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class PriceCondition(BaseModel):
    timeframe: str
    condition: str
    price: float | None = None
    requires_close: bool = True
    volume_confirmation: str | None = None


class PriceZone(BaseModel):
    low: float
    high: float


class Setup(BaseModel):
    symbol: str
    base_asset: str | None = None
    quote_asset: str | None = None
    side: Side
    state: SetupState
    grade: Literal["A", "B", "WATCH", "NO_TRADE"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    last_price: float | None = None
    shortable: bool = False
    execution_modes: list[str] = Field(default_factory=list)
    setup_type: str | None = None
    thesis: list[str] = Field(default_factory=list)
    expansion_score: float
    direction_score: float
    quality_score: float
    setup_score: float
    trigger: PriceCondition | None = None
    entry_zone: PriceZone | None = None
    stop: float | None = None
    invalidation: str | None = None
    targets: list[float] = Field(default_factory=list)
    expected_rr: float | None = None
    expected_holding_days: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    bullish_scenario: str | None = None
    bearish_scenario: str | None = None
    weakest_point: str | None = None
    risks: list[str] = Field(default_factory=list)
    data_quality: DataQuality
    missing_data: list[str] = Field(default_factory=list)
    data_as_of: datetime


class ScanResponse(BaseModel):
    data_as_of: datetime
    data_as_of_budapest: str
    data_quality: DataQuality
    market_regime: MarketRegime
    longs: list[Setup]
    shorts: list[Setup]
    extended_watchlist: list[Setup] = Field(default_factory=list)
    liquidity_blocked: list[Setup] = Field(default_factory=list)
    momentum_radar: list[dict[str, Any]] = Field(default_factory=list)
    universe_stats: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    exclusions: list[dict[str, str]] = Field(default_factory=list)


class WatchlistResponse(BaseModel):
    data_as_of: datetime
    items: list[Setup] = Field(default_factory=list)


class MomentumItem(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str = "USDC"
    side: Literal["long", "short", "neutral"]
    state: Literal["NO_TRADE", "WATCH", "TRIGGERED"]
    stage: str
    momentum_score: float
    last_price: float
    price_change_24h_pct: float
    return_15m_pct: float
    return_1h_pct: float
    return_4h_pct: float
    acceleration_pct: float
    volume_ratio_5m: float
    turnover_acceleration_1h: float
    extension_atr_15m: float
    breakout_confirmed: bool
    chase_risk: bool
    tradeable: bool
    execution_status: str
    turnover_24h_usdc: float
    spread_bps: float
    trigger: dict[str, Any]
    invalidation_price: float | None = None
    bullish_scenario: str
    bearish_scenario: str
    why_now: list[str] = Field(default_factory=list)
    decision: str
    data_as_of: datetime


class MomentumResponse(BaseModel):
    data_as_of: datetime
    eligible_pairs: int | None = None
    scanned_pairs: int
    failed_pairs: int = 0
    failed_symbols: list[str] = Field(default_factory=list)
    calculation_failed_pairs: int = 0
    calculation_failures: list[dict[str, str]] = Field(default_factory=list)
    no_activity_pairs: int = 0
    minimum_score: float
    promotion_minimum_score: float | None = None
    items: list[MomentumItem] = Field(default_factory=list)
