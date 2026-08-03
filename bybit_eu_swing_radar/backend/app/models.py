from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field


DataQuality = Literal["GOOD", "PARTIAL", "DEGRADED"]
SetupState = Literal["WATCH", "ARMED", "TRIGGERED", "MANAGED", "INVALIDATED", "EXPIRED"]
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
    exclusions: list[dict[str, str]] = Field(default_factory=list)
