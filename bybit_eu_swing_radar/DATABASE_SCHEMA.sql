CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    status TEXT NOT NULL,
    shortable BOOLEAN NOT NULL DEFAULT FALSE,
    execution_modes JSONB NOT NULL DEFAULT '[]',
    bybit_category TEXT NOT NULL,
    coinalyze_symbol TEXT,
    listed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS market_bars (
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    open_time TIMESTAMPTZ NOT NULL,
    open NUMERIC NOT NULL,
    high NUMERIC NOT NULL,
    low NUMERIC NOT NULL,
    close NUMERIC NOT NULL,
    volume NUMERIC,
    turnover NUMERIC,
    is_closed BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (source, symbol, timeframe, open_time)
);

CREATE TABLE IF NOT EXISTS derivative_snapshots (
    symbol TEXT NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    open_interest_usd NUMERIC,
    funding_rate NUMERIC,
    predicted_funding_rate NUMERIC,
    long_short_ratio NUMERIC,
    long_liquidations_usd NUMERIC,
    short_liquidations_usd NUMERIC,
    buy_volume_usd NUMERIC,
    sell_volume_usd NUMERIC,
    source TEXT NOT NULL,
    data_quality TEXT NOT NULL,
    PRIMARY KEY (symbol, captured_at, source)
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    symbol TEXT NOT NULL,
    calculated_at TIMESTAMPTZ NOT NULL,
    features JSONB NOT NULL,
    data_quality TEXT NOT NULL,
    PRIMARY KEY (symbol, calculated_at)
);

CREATE TABLE IF NOT EXISTS setups (
    id UUID PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    state TEXT NOT NULL,
    grade TEXT NOT NULL,
    expansion_score NUMERIC NOT NULL,
    direction_score NUMERIC NOT NULL,
    quality_score NUMERIC NOT NULL,
    setup_score NUMERIC NOT NULL,
    trigger JSONB,
    entry_zone JSONB,
    stop NUMERIC,
    invalidation TEXT,
    targets JSONB,
    expected_rr NUMERIC,
    thesis JSONB,
    risks JSONB,
    feature_snapshot_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_setups_symbol_updated ON setups(symbol, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_setups_state_score ON setups(state, setup_score DESC);

CREATE TABLE IF NOT EXISTS setup_events (
    id UUID PRIMARY KEY,
    setup_id UUID NOT NULL REFERENCES setups(id),
    event_type TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT,
    price NUMERIC,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS setup_outcomes (
    setup_id UUID PRIMARY KEY REFERENCES setups(id),
    triggered_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    outcome TEXT,
    realized_r NUMERIC,
    mfe_r NUMERIC,
    mae_r NUMERIC,
    tp_reached INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS radar_cache (
    cache_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
