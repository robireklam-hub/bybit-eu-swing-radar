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
    side_direction_score: float | None = None
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
    setup_state: str | None = None
    entry_state: str | None = None
    execution_valid: bool | None = None
    rr_valid: bool | None = None
    reference_entry: float | None = None
    breakout_context: dict[str, Any] | None = None
    hard_stop: dict[str, Any] | None = None
    structure_invalidation: dict[str, Any] | None = None
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


class DayTradeAuditTrigger(BaseModel):
    timeframe: str
    condition: str
    price: float
    requires_close: bool
    volume_confirmation: str
    triggered: bool
    route: str | None = None
    model: str | None = None
    event_bar_time: str | None = None
    age_bars: int | None = None
    validity_bars: int | None = None
    boundary_held: bool | None = None


class DayTradeBarrierSource(BaseModel):
    price: float
    timeframe: str
    swing_type: Literal["SWING_HIGH", "SWING_LOW"]
    pivot_start_ms: int
    pivot_time: str
    confirmed_at: str
    prominence: float
    prominence_atr: float | None = None
    search_window_start: str
    search_window_end: str
    trigger_window_start: str
    trigger_window_excluded: bool
    same_structure_as_trigger: bool


class DayTradeAuditSide(BaseModel):
    symbol: str
    side: Literal["long", "short"]
    category: Literal["STRICT", "WATCH_ONLY"]
    state: DayTradeState
    decision: Literal["TRADE", "WAIT", "NO_TRADE"]
    watch_bucket: str | None = None
    setup_state: str | None = None
    entry_state: str | None = None
    execution_valid: bool | None = None
    rr_valid: bool | None = None
    reference_entry: float | None = None
    hard_stop: dict[str, Any] | None = None
    structure_invalidation: dict[str, Any] | None = None
    tradeable: bool
    shortable: bool
    execution_status: str
    timeframe_conflict: bool
    side_direction_score: float
    setup_score: float
    trigger: DayTradeAuditTrigger
    entry: float
    entry_zone: PriceZone
    stop: float
    tp1: float
    tp2: float
    tp3: float
    expected_rr: float
    expected_rr_without_barrier: float
    expected_rr_with_barrier: float
    target_path_valid: bool
    nearest_structural_barrier: float | None = None
    barrier_rr_gross: float | None = None
    barrier_rr_net: float | None = None
    barrier_before_tp2: bool
    barrier_source: DayTradeBarrierSource | None = None
    volume_ratio_5m: float


class DayTradeSymbolAuditResponse(BaseModel):
    strategy_mode: Literal["DAY_TRADE"] = "DAY_TRADE"
    strategy_version: str
    data_as_of: datetime
    data_as_of_budapest: str
    symbol: str
    long: DayTradeAuditSide | None = None
    short: DayTradeAuditSide | None = None
    notes: list[str] = Field(default_factory=list)


class DayTradeFlowContextResponse(BaseModel):
    strategy_mode: Literal["DAY_TRADE"] = "DAY_TRADE"
    strategy_version: str
    feature_version: str
    symbol: str
    data_as_of: datetime
    data_as_of_budapest: str
    spot_snapshot_as_of: datetime | None = None
    spot_snapshot_age_seconds: float | None = None
    flow_batch_id: str | None = None
    source_commit_sha: str | None = None
    data_quality: DataQuality
    coverage_status: str
    bybit_global_derivatives: dict[str, Any] = Field(default_factory=dict)
    spot_context: dict[str, Any] = Field(default_factory=dict)
    coinalyze_existing: dict[str, Any] = Field(default_factory=dict)
    interpretation: dict[str, Any] = Field(default_factory=dict)
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


BacktestSignalClass = Literal["STRICT", "SHADOW"]


class DayTradeBacktestSignal(BaseModel):
    id: int
    job_id: int
    signal_key: str
    strategy_version: str
    signal_class: BacktestSignalClass
    execution_assumption: str
    included_primary: bool
    primary_exclusion_reason: str | None = None
    symbol: str
    side: Literal["long", "short"]
    opened_at: datetime
    closed_at: datetime
    setup_type: str
    entry_price: float
    trigger_price: float
    stop_price: float
    tp1: float
    tp2: float
    tp3: float
    expected_rr: float
    modeled_tp2_r: float
    expansion_score: float
    direction_score: float
    side_direction_score: float
    quality_score: float
    setup_score: float
    turnover_24h_usdc: float
    modeled_spread_bps: float
    cost_bps: float
    bars_observed: int
    mfe_r: float
    mae_r: float
    exit_price: float
    exit_reason: str
    gross_r: float
    net_r: float
    btc_structure_1h: str | None = None
    btc_structure_4h: str | None = None
    btc_volatility_regime: str | None = None


class BacktestAggregate(BaseModel):
    sample_size: int
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


class BacktestGroupStats(BaseModel):
    key: str
    stats: BacktestAggregate


class DayTradeBacktestStatusResponse(BaseModel):
    generated_at: datetime
    exists: bool
    job: dict[str, Any] = Field(default_factory=dict)
    progress_pct: float
    symbol_status: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DayTradeBacktestSummaryResponse(BaseModel):
    strategy_version: str
    generated_at: datetime
    job: dict[str, Any] = Field(default_factory=dict)
    requested_signal_class: Literal["all", "STRICT", "SHADOW"]
    requested_side: Literal["both", "long", "short"]
    primary_only: bool
    evidence_status: Literal["INSUFFICIENT_SAMPLE", "EARLY_SAMPLE", "EVALUABLE_SAMPLE"]
    strict_primary_sample: int
    overall: BacktestAggregate
    by_signal_class: list[BacktestGroupStats] = Field(default_factory=list)
    by_side: list[BacktestGroupStats] = Field(default_factory=list)
    by_setup_type: list[BacktestGroupStats] = Field(default_factory=list)
    by_execution_assumption: list[BacktestGroupStats] = Field(default_factory=list)
    by_month: list[BacktestGroupStats] = Field(default_factory=list)
    by_setup_score_band: list[BacktestGroupStats] = Field(default_factory=list)
    methodology: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DayTradeBacktestSignalsResponse(BaseModel):
    generated_at: datetime
    count: int
    items: list[DayTradeBacktestSignal] = Field(default_factory=list)


DiagnosticCohort = Literal[
    "ALL_VALID_CANDIDATES",
    "LIQUID_EXECUTABLE",
    "SCORE_GATES_PASS",
    "NEAR_STRICT",
    "STRICT_ELIGIBLE",
    "STRICT_TRADE",
]


class DiagnosticGateStep(BaseModel):
    gate: str
    reached_count: int
    passed_count: int
    failed_count: int
    pass_rate_from_reached_pct: float | None = None
    pass_rate_from_trigger_pct: float | None = None


class DiagnosticCountGroup(BaseModel):
    key: str
    count: int
    pct_of_trigger: float | None = None


class DiagnosticSegment(BaseModel):
    key: str
    trigger_count: int
    candidate_count: int
    near_strict_count: int
    strict_eligible_count: int
    strict_trade_count: int


class DayTradeDiagnosticStatusResponse(BaseModel):
    generated_at: datetime
    exists: bool
    job: dict[str, Any] = Field(default_factory=dict)
    progress_pct: float
    symbol_status: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DayTradeGateWaterfallResponse(BaseModel):
    strategy_version: str
    generated_at: datetime
    job: dict[str, Any] = Field(default_factory=dict)
    requested_side: Literal["both", "long", "short"]
    requested_split: Literal["all", "DEVELOPMENT", "VALIDATION"]
    requested_universe_group: Literal["all", "MAJOR_LIQUID", "OTHER"]
    primary_only: bool
    trigger_count: int
    primary_count: int
    strict_eligible_count: int
    strict_trade_count: int
    waterfall: list[DiagnosticGateStep] = Field(default_factory=list)
    first_failures: list[DiagnosticCountGroup] = Field(default_factory=list)
    by_side: list[DiagnosticSegment] = Field(default_factory=list)
    by_split: list[DiagnosticSegment] = Field(default_factory=list)
    by_universe_group: list[DiagnosticSegment] = Field(default_factory=list)
    methodology: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DiagnosticSensitivityStats(BaseModel):
    horizon_hours: int
    cost_bps: float
    stats: BacktestAggregate


class DiagnosticCohortStats(BaseModel):
    key: DiagnosticCohort
    count: int
    stats: BacktestAggregate


class ExcursionThreshold(BaseModel):
    threshold_r: float
    reached_count: int
    reached_pct: float | None = None


class DayTradeEdgeDiagnosticsResponse(BaseModel):
    strategy_version: str
    generated_at: datetime
    job: dict[str, Any] = Field(default_factory=dict)
    selected_cohort: DiagnosticCohort
    requested_side: Literal["both", "long", "short"]
    requested_split: Literal["all", "DEVELOPMENT", "VALIDATION"]
    requested_universe_group: Literal["all", "MAJOR_LIQUID", "OTHER"]
    primary_only: bool
    base_horizon_hours: int
    base_cost_bps: float
    selected_sample: int
    selected_performance: BacktestAggregate
    cohort_performance: list[DiagnosticCohortStats] = Field(default_factory=list)
    sensitivity: list[DiagnosticSensitivityStats] = Field(default_factory=list)
    by_side: list[BacktestGroupStats] = Field(default_factory=list)
    by_split: list[BacktestGroupStats] = Field(default_factory=list)
    by_universe_group: list[BacktestGroupStats] = Field(default_factory=list)
    by_btc_regime: list[BacktestGroupStats] = Field(default_factory=list)
    by_setup_score_band: list[BacktestGroupStats] = Field(default_factory=list)
    by_expansion_score_band: list[BacktestGroupStats] = Field(default_factory=list)
    by_direction_score_band: list[BacktestGroupStats] = Field(default_factory=list)
    by_quality_score_band: list[BacktestGroupStats] = Field(default_factory=list)
    mfe_thresholds: list[ExcursionThreshold] = Field(default_factory=list)
    mae_thresholds: list[ExcursionThreshold] = Field(default_factory=list)
    methodology: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
