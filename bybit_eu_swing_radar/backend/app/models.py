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
    side_direction_score: float
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
    raw_usdc_instrument_records: int | None = None
    unique_usdc_instruments: int | None = None
    duplicate_instrument_records: int = 0
    duplicate_symbols: list[str] = Field(default_factory=list)
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



class CompactCandidate(BaseModel):
    symbol: str
    side: Side
    category: Literal["STRICT", "WATCH_ONLY"]
    state: SetupState
    grade: Literal["A", "B", "WATCH", "NO_TRADE"]
    technical_grade: Literal["A", "B", "WATCH", "NO_TRADE"] | None = None
    watch_bucket: str | None = None
    decision: Literal["TRADE", "WAIT", "NO_TRADE"]
    last_price: float | None = None
    setup_score: float
    expansion_score: float
    direction_score: float
    quality_score: float
    shortable: bool = False
    tradeable: bool
    execution_status: str
    execution_modes: list[str] = Field(default_factory=list)
    trigger: PriceCondition | None = None
    entry_zone: PriceZone | None = None
    stop: float | None = None
    invalidation: str | None = None
    targets: list[float] = Field(default_factory=list)
    expected_rr: float | None = None
    turnover_24h_usdc: float | None = None
    spread_bps: float | None = None
    volume_ratio_4h: float | None = None
    liquidity_reasons: list[str] = Field(default_factory=list)
    weakest_point: str | None = None
    data_quality: DataQuality
    missing_data: list[str] = Field(default_factory=list)


class TopCandidatesResponse(BaseModel):
    data_as_of: datetime
    data_as_of_budapest: str
    data_quality: DataQuality
    market_regime: dict[str, Any] = Field(default_factory=dict)
    requested_limit: int
    strict_long_count: int
    strict_short_count: int
    strict_longs: list[CompactCandidate] = Field(default_factory=list)
    strict_shorts: list[CompactCandidate] = Field(default_factory=list)
    watch_only_longs: list[CompactCandidate] = Field(default_factory=list)
    watch_only_shorts: list[CompactCandidate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)



DayTradeState = Literal["NO_TRADE", "WATCH", "ARMED", "TRIGGERED"]


class DayTradeCandidate(BaseModel):
    symbol: str
    base_asset: str
    quote_asset: str = "USDC"
    strategy_mode: Literal["DAY_TRADE"] = "DAY_TRADE"
    side: Literal["long", "short"]
    category: Literal["STRICT", "WATCH_ONLY"]
    state: DayTradeState
    grade: Literal["A", "B", "WATCH", "NO_TRADE"]
    technical_grade: Literal["A", "B", "WATCH", "NO_TRADE"] | None = None
    watch_bucket: str | None = None
    decision: Literal["TRADE", "WAIT", "NO_TRADE"]
    setup_type: str
    last_price: float
    tradeable: bool
    shortable: bool = False
    execution_status: str
    execution_modes: list[str] = Field(default_factory=list)
    expansion_score: float
    direction_score: float
    side_direction_score: float
    quality_score: float
    setup_score: float
    context_4h: str
    structure_1h: str
    structure_15m: str
    timeframe_conflict: bool = False
    trigger: dict[str, Any]
    entry_zone: PriceZone
    stop: float
    invalidation: str
    targets: list[float] = Field(default_factory=list)
    expected_rr: float
    expected_holding_time: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    derivatives: dict[str, Any] = Field(default_factory=dict)
    why_now: list[str] = Field(default_factory=list)
    bullish_scenario: str
    bearish_scenario: str
    weakest_point: str
    risks: list[str] = Field(default_factory=list)
    data_quality: DataQuality
    missing_data: list[str] = Field(default_factory=list)
    data_as_of: datetime


class DayTradeScanResponse(BaseModel):
    strategy_mode: Literal["DAY_TRADE"] = "DAY_TRADE"
    data_as_of: datetime
    data_as_of_budapest: str
    data_quality: DataQuality
    market_regime: dict[str, Any] = Field(default_factory=dict)
    strict_longs: list[DayTradeCandidate] = Field(default_factory=list)
    strict_shorts: list[DayTradeCandidate] = Field(default_factory=list)
    watch_only_longs: list[DayTradeCandidate] = Field(default_factory=list)
    watch_only_shorts: list[DayTradeCandidate] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    exclusions: list[dict[str, str]] = Field(default_factory=list)
    journal: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class DayTradeTopCandidatesResponse(BaseModel):
    strategy_mode: Literal["DAY_TRADE"] = "DAY_TRADE"
    data_as_of: datetime
    data_as_of_budapest: str
    data_quality: DataQuality
    market_regime: dict[str, Any] = Field(default_factory=dict)
    requested_limit: int
    strict_long_count: int
    strict_short_count: int
    strict_longs: list[DayTradeCandidate] = Field(default_factory=list)
    strict_shorts: list[DayTradeCandidate] = Field(default_factory=list)
    watch_only_longs: list[DayTradeCandidate] = Field(default_factory=list)
    watch_only_shorts: list[DayTradeCandidate] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


JournalSignalClass = Literal["STRICT", "SHADOW"]
JournalSignalStatus = Literal["OPEN", "CLOSED"]


class DayTradeJournalSignal(BaseModel):
    id: int
    signal_key: str
    strategy_version: str
    signal_class: JournalSignalClass
    symbol: str
    side: Literal["long", "short"]
    status: JournalSignalStatus
    opened_at: datetime
    expires_at: datetime
    closed_at: datetime | None = None
    setup_type: str
    entry_price: float
    trigger_price: float
    stop_price: float
    tp1: float
    tp2: float
    tp3: float
    expected_rr: float
    modeled_tp2_r: float
    entry_deviation_bps: float
    entry_within_zone: bool
    setup_score: float
    expansion_score: float
    direction_score: float
    side_direction_score: float
    quality_score: float
    bars_observed: int
    mfe_r: float
    mae_r: float
    exit_price: float | None = None
    exit_reason: str | None = None
    gross_r: float | None = None
    net_r: float | None = None
    cost_bps: float


class JournalAggregate(BaseModel):
    sample_size: int
    open_count: int
    closed_count: int
    tp2_count: int
    stop_count: int
    ambiguous_stop_count: int
    time_exit_count: int
    positive_net_count: int
    target_hit_rate_pct: float | None = None
    positive_net_rate_pct: float | None = None
    average_net_r: float | None = None
    median_net_r: float | None = None
    profit_factor: float | None = None
    average_mfe_r: float | None = None
    average_mae_r: float | None = None


class JournalGroupStats(BaseModel):
    key: str
    stats: JournalAggregate


class DayTradeJournalSummaryResponse(BaseModel):
    strategy_version: str
    generated_at: datetime
    window_days: int
    requested_signal_class: Literal["all", "STRICT", "SHADOW"]
    evidence_status: Literal[
        "INSUFFICIENT_SAMPLE", "EARLY_SAMPLE", "EVALUABLE_SAMPLE"
    ]
    strict_closed_sample: int
    overall: JournalAggregate
    by_signal_class: list[JournalGroupStats] = Field(default_factory=list)
    by_side: list[JournalGroupStats] = Field(default_factory=list)
    by_setup_type: list[JournalGroupStats] = Field(default_factory=list)
    latest_run: dict[str, Any] = Field(default_factory=dict)
    methodology: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DayTradeJournalSignalsResponse(BaseModel):
    generated_at: datetime
    count: int
    items: list[DayTradeJournalSignal] = Field(default_factory=list)
