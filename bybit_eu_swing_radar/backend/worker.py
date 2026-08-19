"""Bybit EU Swing Radar one-shot background worker.

Scope:
- Bybit EU SPOT markets only
- Quote asset must be USDC
- Long execution: USDC spot
- Short execution: USDC spot margin only, and only when the base asset is
  borrowable according to Bybit's public Spot Margin VIP data and the USDC
  instrument currently exposes margin trading
- Coinalyze is used as contextual derivatives data; it is not treated as
  Bybit-EU-specific unless the selected Coinalyze market is Bybit.

Railway cron start command:
    python worker.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import asyncpg
import httpx

from app.swing_priority import select_compact_priority_sections


BUDAPEST = ZoneInfo("Europe/Budapest")
INTERVAL_MS = {
    "5": 5 * 60 * 1000,
    "15": 15 * 60 * 1000,
    "60": 60 * 60 * 1000,
    "240": 4 * 60 * 60 * 1000,
    "D": 24 * 60 * 60 * 1000,
}
STABLE_BASES = {
    "USDC", "USDT", "DAI", "FDUSD", "TUSD", "USDE", "USDS", "PYUSD",
    "EUR", "EURC", "BUSD", "USD1", "RLUSD", "USDD", "USDQ",
}


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid float environment variable {name}={raw!r}") from exc


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid integer environment variable {name}={raw!r}") from exc


DATABASE_URL = os.getenv("DATABASE_URL", "")
BYBIT_BASE_URL = os.getenv("BYBIT_BASE_URL", "https://api.bybit.eu").rstrip("/")
COINALYZE_BASE_URL = os.getenv("COINALYZE_BASE_URL", "https://api.coinalyze.net/v1").rstrip("/")
COINALYZE_API_KEY = os.getenv("COINALYZE_API_KEY", "")
MIN_TURNOVER_USDC = env_float("MIN_TURNOVER_USDC", 100_000.0)
MAX_SPREAD_BPS = env_float("MAX_SPREAD_BPS", 50.0)
DISCOVERY_MAX_SPREAD_BPS = env_float("DISCOVERY_MAX_SPREAD_BPS", 200.0)
MAX_UNIVERSE = min(max(env_int("MAX_UNIVERSE", 30), 10), 50)
TOP_LIQUID_DISCOVERY = min(max(env_int("TOP_LIQUID_DISCOVERY", 15), 5), MAX_UNIVERSE)
COINALYZE_ENRICH_LIMIT = min(max(env_int("COINALYZE_ENRICH_LIMIT", 9), 1), 9)
KLINE_LIMIT = min(max(env_int("KLINE_LIMIT", 220), 100), 1000)
HTTP_CONCURRENCY = min(max(env_int("HTTP_CONCURRENCY", 5), 1), 10)
MOMENTUM_HTTP_CONCURRENCY = min(max(env_int("MOMENTUM_HTTP_CONCURRENCY", 4), 1), 8)
MOMENTUM_OUTPUT_LIMIT = min(max(env_int("MOMENTUM_OUTPUT_LIMIT", 120), 20), 150)
MOMENTUM_DEEP_LIMIT = min(max(env_int("MOMENTUM_DEEP_LIMIT", 10), 3), 20)
MOMENTUM_MIN_SCORE = env_float("MOMENTUM_MIN_SCORE", 50.0)
MOMENTUM_SYMBOL_RETRY_PASSES = min(max(env_int("MOMENTUM_SYMBOL_RETRY_PASSES", 2), 0), 4)
BYBIT_MAX_RETRIES = min(max(env_int("BYBIT_MAX_RETRIES", 4), 1), 8)
BYBIT_RETRY_BASE_SECONDS = max(env_float("BYBIT_RETRY_BASE_SECONDS", 0.75), 0.1)
DEFAULT_DISCOVERY_SYMBOLS = {
    "BTCUSDC", "ETHUSDC", "SOLUSDC", "XRPUSDC",
    "AVAXUSDC", "APTUSDC", "ADAUSDC", "LINKUSDC", "DOGEUSDC",
    "SUIUSDC", "LTCUSDC", "DOTUSDC", "BNBUSDC", "BCHUSDC",
    "AAVEUSDC", "UNIUSDC", "NEARUSDC", "INJUSDC", "ATOMUSDC",
}
DISCOVERY_SYMBOLS = {
    item.strip().upper()
    for item in os.getenv("DISCOVERY_SYMBOLS", ",".join(sorted(DEFAULT_DISCOVERY_SYMBOLS))).split(",")
    if item.strip()
}


@dataclass(frozen=True)
class Bar:
    start_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float


@dataclass
class Instrument:
    symbol: str
    base: str
    quote: str
    margin_trading: str
    tick_size: float
    turnover_24h: float
    volume_24h: float
    last_price: float
    bid: float
    ask: float
    spread_bps: float
    price_change_24h_pct: float
    tradeable: bool
    liquidity_reasons: list[str]
    discovery_source: str


@dataclass
class Analysis:
    instrument: Instrument
    bars_1h: list[Bar]
    bars_4h: list[Bar]
    bars_1d: list[Bar]
    atr_4h: float
    ema20_4h: float
    ema50_4h: float
    ema20_1d: float
    ema50_1d: float
    range_high: float
    range_low: float
    recent_high: float
    recent_low: float
    volume_ratio: float
    bb_width_percentile: float
    atr_ratio: float
    expansion_score: float
    direction_score: float
    quality_score: float
    relative_strength_4h: float
    structure_4h: str
    structure_1d: str
    derivatives: dict[str, Any]
    missing_data: list[str]
    shortable: bool = False
    max_borrowing_amount: float = 0.0


@dataclass
class MomentumAnalysis:
    instrument: Instrument
    bars_5m: list[Bar]
    bars_15m: list[Bar]
    score: float
    side: str
    return_15m_pct: float
    return_1h_pct: float
    return_4h_pct: float
    previous_1h_return_pct: float
    acceleration_pct: float
    volume_ratio_5m: float
    turnover_acceleration_1h: float
    atr_15m: float
    extension_atr: float
    breakout_price: float
    breakout_confirmed: bool
    distance_to_breakout_atr: float
    chase_risk: bool
    stage: str
    missing_data: list[str]


class BybitAPI:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def public_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a Bybit public endpoint with bounded retry/backoff."""
        last_error: Exception | None = None
        for attempt in range(BYBIT_MAX_RETRIES):
            try:
                response = await self.client.get(f"{BYBIT_BASE_URL}{path}", params=params)

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt + 1 < BYBIT_MAX_RETRIES:
                        await asyncio.sleep(BYBIT_RETRY_BASE_SECONDS * (2 ** attempt))
                        continue

                response.raise_for_status()
                payload = response.json()
                ret_code = int(payload.get("retCode", 0))

                if ret_code in {10000, 10006, 10016} and attempt + 1 < BYBIT_MAX_RETRIES:
                    await asyncio.sleep(BYBIT_RETRY_BASE_SECONDS * (2 ** attempt))
                    continue

                if ret_code != 0:
                    raise RuntimeError(f"Bybit error {ret_code}: {payload.get('retMsg')}")
                return payload
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError, RuntimeError) as exc:
                last_error = exc
                if attempt + 1 >= BYBIT_MAX_RETRIES:
                    raise
                await asyncio.sleep(BYBIT_RETRY_BASE_SECONDS * (2 ** attempt))

        raise RuntimeError(f"Bybit request failed after retries: {last_error}")


    async def instruments(self) -> list[dict[str, Any]]:
        payload = await self.public_get("/v5/market/instruments-info", {"category": "spot"})
        return payload["result"]["list"]

    async def tickers(self) -> list[dict[str, Any]]:
        payload = await self.public_get("/v5/market/tickers", {"category": "spot"})
        return payload["result"]["list"]

    async def klines(self, symbol: str, interval: str, limit: int = KLINE_LIMIT) -> list[Bar]:
        payload = await self.public_get(
            "/v5/market/kline",
            {"category": "spot", "symbol": symbol, "interval": interval, "limit": limit},
        )
        now_ms = int(time.time() * 1000)
        rows = list(reversed(payload["result"]["list"]))
        bars: list[Bar] = []
        for row in rows:
            start_ms = int(row[0])
            if start_ms + INTERVAL_MS[interval] > now_ms:
                continue
            bars.append(
                Bar(
                    start_ms=start_ms,
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    turnover=float(row[6]),
                )
            )
        return bars

    async def vip_margin_data(self) -> dict[str, dict[str, Any]]:
        """Return public Spot Margin borrowability for the No-VIP tier.

        This endpoint does not require an API key. It reports whether a coin is
        borrowable and its maximum borrowing amount. Pair-level
        ``marginTrading`` from instruments-info is checked separately because
        Bybit can temporarily set it to ``none`` when lending inventory is
        unavailable.
        """
        payload = await self.public_get(
            "/v5/spot-margin-trade/data",
            {"vipLevel": "No VIP"},
        )
        raw_result = payload.get("result", {})
        groups = raw_result.get("vipCoinList") or raw_result.get("list") or []
        rows: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            if "currency" in group:
                rows.append(group)
            else:
                nested = group.get("list", [])
                if isinstance(nested, list):
                    rows.extend(item for item in nested if isinstance(item, dict))

        result: dict[str, dict[str, Any]] = {}
        for item in rows:
            currency = str(item.get("currency", "")).upper()
            if currency:
                result[currency] = item
        return result


class CoinalyzeAPI:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.headers = {"api_key": COINALYZE_API_KEY}

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not COINALYZE_API_KEY:
            raise RuntimeError("COINALYZE_API_KEY is not configured")
        response = await self.client.get(
            f"{COINALYZE_BASE_URL}{path}", params=params, headers=self.headers
        )
        if response.status_code == 429:
            try:
                wait_seconds = math.ceil(float(response.headers.get("Retry-After", "60")))
            except (TypeError, ValueError):
                wait_seconds = 60
            await asyncio.sleep(max(wait_seconds, 1))
            response = await self.client.get(
                f"{COINALYZE_BASE_URL}{path}", params=params, headers=self.headers
            )
        response.raise_for_status()
        return response.json()

    async def future_markets(self) -> list[dict[str, Any]]:
        return await self.get("/future-markets")

    async def exchanges(self) -> list[dict[str, Any]]:
        return await self.get("/exchanges")

    async def batch_current(self, endpoint: str, symbols: list[str], convert_to_usd: bool = False) -> Any:
        params: dict[str, Any] = {"symbols": ",".join(symbols)}
        if convert_to_usd:
            params["convert_to_usd"] = "true"
        return await self.get(endpoint, params)

    async def batch_history(
        self,
        endpoint: str,
        symbols: list[str],
        from_ts: int,
        to_ts: int,
        convert_to_usd: bool = False,
        interval: str = "4hour",
    ) -> Any:
        params: dict[str, Any] = {
            "symbols": ",".join(symbols),
            "interval": interval,
            "from": from_ts,
            "to": to_ts,
        }
        if convert_to_usd:
            params["convert_to_usd"] = "true"
        return await self.get(endpoint, params)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def mean(values: Iterable[float]) -> float:
    values_list = list(values)
    return statistics.fmean(values_list) if values_list else 0.0


def ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    seed_count = min(period, len(values))
    current = mean(values[:seed_count])
    multiplier = 2.0 / (period + 1.0)
    for value in values[seed_count:]:
        current = (value - current) * multiplier + current
    return current


def true_ranges(bars: list[Bar]) -> list[float]:
    if len(bars) < 2:
        return []
    result: list[float] = []
    previous_close = bars[0].close
    for bar in bars[1:]:
        result.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
        previous_close = bar.close
    return result


def atr(bars: list[Bar], period: int = 14) -> float:
    ranges = true_ranges(bars)
    if not ranges:
        return 0.0
    return mean(ranges[-period:])


def rolling_atr_values(bars: list[Bar], period: int = 14) -> list[float]:
    ranges = true_ranges(bars)
    if len(ranges) < period:
        return []
    return [mean(ranges[index - period:index]) for index in range(period, len(ranges) + 1)]


def bollinger_width(closes: list[float], period: int = 20) -> float:
    if len(closes) < period:
        return 0.0
    window = closes[-period:]
    avg = mean(window)
    if avg <= 0:
        return 0.0
    std = statistics.pstdev(window)
    return (4.0 * std) / avg


def rolling_bollinger_widths(closes: list[float], period: int = 20) -> list[float]:
    widths: list[float] = []
    for index in range(period, len(closes) + 1):
        window = closes[index - period:index]
        avg = mean(window)
        widths.append((4.0 * statistics.pstdev(window) / avg) if avg > 0 else 0.0)
    return widths


def percentile_rank(values: list[float], current: float) -> float:
    if not values:
        return 50.0
    less_or_equal = sum(1 for value in values if value <= current)
    return 100.0 * less_or_equal / len(values)


def return_pct(values: list[float], periods: int) -> float:
    if len(values) <= periods or values[-periods - 1] <= 0:
        return 0.0
    return (values[-1] / values[-periods - 1] - 1.0) * 100.0


def structure_label(close: float, ema20_value: float, ema50_value: float) -> str:
    if close > ema20_value > ema50_value:
        return "bullish HH/HL-compatible"
    if close < ema20_value < ema50_value:
        return "bearish LH/LL-compatible"
    return "mixed/range"


def round_to_tick(value: float, tick_size: float) -> float:
    if tick_size <= 0:
        return float(f"{value:.8g}")
    steps = round(value / tick_size)
    rounded = steps * tick_size
    decimals = max(0, min(12, int(round(-math.log10(tick_size))) + 2)) if tick_size < 1 else 2
    return round(rounded, decimals)


def build_universe(
    instruments: list[dict[str, Any]], tickers: list[dict[str, Any]]
) -> tuple[list[Instrument], list[dict[str, str]], dict[str, Any], list[Instrument]]:
    """Build a broad analysis universe and a strict execution subset.

    All analyzed instruments are Bybit EU spot pairs quoted in USDC. The strict
    ``tradeable`` flag requires the configured turnover and spread thresholds.
    Lower-liquidity mandatory symbols remain in discovery as WATCH_ONLY, never
    as executable signals.
    """
    ticker_map = {str(item.get("symbol", "")).upper(): item for item in tickers}
    candidates: list[Instrument] = []
    exclusions: list[dict[str, str]] = []

    # Bybit EU can return more than one instrument-info row for the same spot
    # symbol. Momentum coverage must compare unique markets with unique markets,
    # not raw records with a symbol-keyed result dictionary.
    raw_usdc_instrument_records = 0
    normalized_by_symbol: dict[str, dict[str, Any]] = {}
    duplicate_symbols: set[str] = set()

    def record_rank(item: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            1 if item.get("status") == "Trading" else 0,
            1 if str(item.get("stTag", "0")) != "1" else 0,
            1 if str(item.get("marginTrading", "none")).lower() != "none" else 0,
            1 if safe_float(item.get("priceFilter", {}).get("tickSize"), 0.0) > 0 else 0,
        )

    for raw in instruments:
        symbol = str(raw.get("symbol", "")).upper()
        quote = str(raw.get("quoteCoin", "")).upper()
        if quote != "USDC" or not symbol:
            continue
        raw_usdc_instrument_records += 1

        existing = normalized_by_symbol.get(symbol)
        if existing is None:
            normalized_by_symbol[symbol] = raw
            continue

        duplicate_symbols.add(symbol)
        if record_rank(raw) > record_rank(existing):
            normalized_by_symbol[symbol] = raw

    unique_usdc_instruments = len(normalized_by_symbol)
    duplicate_instrument_records = raw_usdc_instrument_records - unique_usdc_instruments
    active_usdc_count = 0

    for symbol, raw in normalized_by_symbol.items():
        base = str(raw.get("baseCoin", "")).upper()
        quote = str(raw.get("quoteCoin", "")).upper()
        if raw.get("status") != "Trading":
            exclusions.append({"symbol": symbol, "reason": "Not Trading"})
            continue
        active_usdc_count += 1
        if base in STABLE_BASES:
            exclusions.append({"symbol": symbol, "reason": "Stable/fiat base asset excluded"})
            continue
        if str(raw.get("stTag", "0")) == "1":
            exclusions.append({"symbol": symbol, "reason": "Special-treatment instrument excluded"})
            continue

        ticker = ticker_map.get(symbol)
        if not ticker:
            exclusions.append({"symbol": symbol, "reason": "Missing ticker"})
            continue
        turnover = safe_float(ticker.get("turnover24h"))
        volume = safe_float(ticker.get("volume24h"))
        last = safe_float(ticker.get("lastPrice"))
        price_change_24h_pct = safe_float(ticker.get("price24hPcnt")) * 100.0
        bid = safe_float(ticker.get("bid1Price"))
        ask = safe_float(ticker.get("ask1Price"))
        if min(last, bid, ask) <= 0:
            exclusions.append({"symbol": symbol, "reason": "Invalid bid/ask/last price"})
            continue
        spread_bps = ((ask - bid) / ((ask + bid) / 2.0)) * 10_000.0
        if spread_bps > DISCOVERY_MAX_SPREAD_BPS:
            exclusions.append({
                "symbol": symbol,
                "reason": f"Spread {spread_bps:.1f} bps above discovery safety limit",
            })
            continue

        liquidity_reasons: list[str] = []
        if turnover < MIN_TURNOVER_USDC:
            liquidity_reasons.append(f"24h turnover below {MIN_TURNOVER_USDC:.0f} USDC")
        if spread_bps > MAX_SPREAD_BPS:
            liquidity_reasons.append(f"Spread {spread_bps:.1f} bps above executable limit")
        tradeable = not liquidity_reasons
        tick_size = safe_float(raw.get("priceFilter", {}).get("tickSize"), 0.0)
        candidates.append(
            Instrument(
                symbol=symbol,
                base=base,
                quote=quote,
                margin_trading=str(raw.get("marginTrading", "none")),
                tick_size=tick_size,
                turnover_24h=turnover,
                volume_24h=volume,
                last_price=last,
                bid=bid,
                ask=ask,
                spread_bps=spread_bps,
                price_change_24h_pct=price_change_24h_pct,
                tradeable=tradeable,
                liquidity_reasons=liquidity_reasons,
                discovery_source="mandatory" if symbol in DISCOVERY_SYMBOLS else "market",
            )
        )

    candidates.sort(key=lambda item: item.turnover_24h, reverse=True)
    tradeable = [item for item in candidates if item.tradeable]
    mandatory = [item for item in candidates if item.symbol in DISCOVERY_SYMBOLS]
    top_liquid = candidates[:TOP_LIQUID_DISCOVERY]

    selected: list[Instrument] = []
    seen: set[str] = set()
    for group in (tradeable, mandatory, top_liquid):
        for item in group:
            if item.symbol in seen:
                continue
            selected.append(item)
            seen.add(item.symbol)
            if len(selected) >= MAX_UNIVERSE:
                break
        if len(selected) >= MAX_UNIVERSE:
            break

    # BTC is required for relative-strength context whenever listed.
    btc = next((item for item in candidates if item.symbol == "BTCUSDC"), None)
    if btc and btc.symbol not in seen:
        if len(selected) >= MAX_UNIVERSE:
            selected[-1] = btc
        else:
            selected.append(btc)

    stats = {
        "raw_usdc_instrument_records": raw_usdc_instrument_records,
        "unique_usdc_instruments": unique_usdc_instruments,
        "duplicate_instrument_records": duplicate_instrument_records,
        "duplicate_symbols": sorted(duplicate_symbols),
        "active_usdc_pairs": active_usdc_count,
        "eligible_discovery_pairs": len(candidates),
        "analysis_universe_size": len(selected),
        "tradeable_universe_size": sum(1 for item in selected if item.tradeable),
        "liquidity_blocked_size": sum(1 for item in selected if not item.tradeable),
        "mandatory_requested": len(DISCOVERY_SYMBOLS),
        "mandatory_found": sum(1 for item in selected if item.symbol in DISCOVERY_SYMBOLS),
        "minimum_turnover_usdc": MIN_TURNOVER_USDC,
        "max_executable_spread_bps": MAX_SPREAD_BPS,
        "max_discovery_spread_bps": DISCOVERY_MAX_SPREAD_BPS,
    }
    return selected, exclusions, stats, candidates


async def fetch_analysis_bars(
    api: BybitAPI, instrument: Instrument, semaphore: asyncio.Semaphore
) -> tuple[Instrument, list[Bar], list[Bar], list[Bar]] | None:
    try:
        async with semaphore:
            bars_1h, bars_4h, bars_1d = await asyncio.gather(
                api.klines(instrument.symbol, "60"),
                api.klines(instrument.symbol, "240"),
                api.klines(instrument.symbol, "D"),
            )
        if len(bars_1h) < 80 or len(bars_4h) < 80 or len(bars_1d) < 60:
            return None
        return instrument, bars_1h, bars_4h, bars_1d
    except Exception as exc:
        print(f"WARN {instrument.symbol}: kline fetch failed: {exc}", file=sys.stderr)
        return None


async def fetch_momentum_bars(
    api: BybitAPI, instrument: Instrument, semaphore: asyncio.Semaphore
) -> tuple[Instrument, list[Bar], list[Bar]] | None:
    """Fetch lightweight closed-candle data for every eligible USDC pair."""
    try:
        # One semaphore permit now means one active Bybit request. The previous
        # implementation started two simultaneous requests per symbol.
        async with semaphore:
            bars_5m = await api.klines(instrument.symbol, "5", limit=72)
            bars_15m = await api.klines(instrument.symbol, "15", limit=80)

        if len(bars_5m) < 18 or len(bars_15m) < 12:
            print(
                f"WARN {instrument.symbol}: insufficient momentum history "
                f"(5m={len(bars_5m)}, 15m={len(bars_15m)})",
                file=sys.stderr,
            )
            return None
        return instrument, bars_5m, bars_15m
    except Exception as exc:
        print(f"WARN {instrument.symbol}: momentum fetch failed: {exc}", file=sys.stderr)
        return None


def bars_return_pct(bars: list[Bar], periods: int) -> float:
    if len(bars) <= periods or bars[-periods - 1].close <= 0:
        return 0.0
    return (bars[-1].close / bars[-periods - 1].close - 1.0) * 100.0


def average_bar_value(bars: list[Bar], field: str) -> float:
    return mean(getattr(bar, field) for bar in bars) if bars else 0.0


def analyze_momentum(instrument: Instrument, bars_5m: list[Bar], bars_15m: list[Bar]) -> MomentumAnalysis:
    current = bars_5m[-1].close
    atr_15m = atr(bars_15m, 14)
    if current <= 0:
        raise RuntimeError("Invalid momentum price")

    # A flat or almost inactive USDC market can legitimately have zero 15m ATR.
    # It still belongs to the all-market audit and must be returned explicitly
    # as NO_ACTIVITY rather than silently disappearing from coverage.
    if atr_15m <= 0:
        return MomentumAnalysis(
            instrument=instrument,
            bars_5m=bars_5m,
            bars_15m=bars_15m,
            score=0.0,
            side="neutral",
            return_15m_pct=bars_return_pct(bars_5m, 3),
            return_1h_pct=bars_return_pct(bars_15m, 4),
            return_4h_pct=bars_return_pct(bars_15m, 16),
            previous_1h_return_pct=0.0,
            acceleration_pct=0.0,
            volume_ratio_5m=0.0,
            turnover_acceleration_1h=0.0,
            atr_15m=0.0,
            extension_atr=0.0,
            breakout_price=current,
            breakout_confirmed=False,
            distance_to_breakout_atr=0.0,
            chase_risk=False,
            stage="NO_ACTIVITY",
            missing_data=["15m ATR is zero; no usable short-term price movement"],
        )

    return_15m = bars_return_pct(bars_5m, 3)
    return_1h = bars_return_pct(bars_15m, 4)
    return_4h = bars_return_pct(bars_15m, 16)
    previous_1h = 0.0
    if len(bars_15m) >= 9 and bars_15m[-9].close > 0:
        previous_1h = (bars_15m[-5].close / bars_15m[-9].close - 1.0) * 100.0
    acceleration = return_1h - previous_1h

    recent_5m_volume = average_bar_value(bars_5m[-3:], "volume")
    baseline_5m_volume = average_bar_value(bars_5m[-27:-3], "volume")
    volume_ratio_5m = recent_5m_volume / baseline_5m_volume if baseline_5m_volume > 0 else 1.0

    recent_hour_turnover = sum(bar.turnover for bar in bars_15m[-4:])
    prior_hours = [
        sum(bar.turnover for bar in bars_15m[start:start + 4])
        for start in range(max(0, len(bars_15m) - 28), len(bars_15m) - 4, 4)
        if len(bars_15m[start:start + 4]) == 4
    ]
    baseline_hour_turnover = mean(prior_hours[-6:])
    turnover_acceleration = recent_hour_turnover / baseline_hour_turnover if baseline_hour_turnover > 0 else 1.0

    prior_window = bars_15m[-21:-1]
    prior_high = max(bar.high for bar in prior_window)
    prior_low = min(bar.low for bar in prior_window)
    weighted_direction = 0.50 * return_1h + 0.30 * return_4h + 0.20 * return_15m
    side = "long" if weighted_direction >= 0 else "short"
    breakout_confirmed = current > prior_high if side == "long" else current < prior_low
    breakout_price = prior_high if side == "long" else prior_low
    distance_to_breakout_atr = abs(breakout_price - current) / atr_15m

    ema20_15m = ema([bar.close for bar in bars_15m], 20)
    extension_atr = abs(current - ema20_15m) / atr_15m

    price_score = clamp(abs(return_1h) / 8.0 * 100.0)
    volume_score = clamp((volume_ratio_5m - 1.0) / 4.0 * 100.0)
    turnover_score = clamp((turnover_acceleration - 1.0) / 4.0 * 100.0)
    acceleration_score = clamp(abs(acceleration) / 5.0 * 100.0)
    breakout_score = 100.0 if breakout_confirmed else clamp(100.0 - distance_to_breakout_atr * 55.0)
    daily_move_score = clamp(abs(instrument.price_change_24h_pct) / 25.0 * 100.0)

    alignment = (
        (return_15m >= 0 and return_1h >= 0 and return_4h >= 0)
        or (return_15m <= 0 and return_1h <= 0 and return_4h <= 0)
    )
    alignment_bonus = 7.0 if alignment else -8.0
    extreme_move_bonus = 10.0 if abs(instrument.price_change_24h_pct) >= 25.0 else 5.0 if abs(instrument.price_change_24h_pct) >= 15.0 else 0.0
    score = clamp(
        0.20 * price_score
        + 0.20 * volume_score
        + 0.15 * turnover_score
        + 0.15 * acceleration_score
        + 0.15 * breakout_score
        + 0.15 * daily_move_score
        + alignment_bonus
        + extreme_move_bonus
    )

    chase_risk = bool(
        abs(instrument.price_change_24h_pct) >= 25.0
        or abs(return_4h) >= 20.0
        or extension_atr >= 3.5
    )
    if chase_risk:
        stage = "EXTENDED_CHASE_RISK"
    elif breakout_confirmed and score >= 70:
        stage = "BREAKOUT_CONFIRMED"
    elif score >= 60 and abs(instrument.price_change_24h_pct) < 15 and extension_atr < 2.5:
        stage = "EARLY_ACCELERATION"
    elif score >= MOMENTUM_MIN_SCORE:
        stage = "MOMENTUM_WATCH"
    else:
        stage = "LOW_SIGNAL"

    return MomentumAnalysis(
        instrument=instrument,
        bars_5m=bars_5m,
        bars_15m=bars_15m,
        score=score,
        side=side,
        return_15m_pct=return_15m,
        return_1h_pct=return_1h,
        return_4h_pct=return_4h,
        previous_1h_return_pct=previous_1h,
        acceleration_pct=acceleration,
        volume_ratio_5m=volume_ratio_5m,
        turnover_acceleration_1h=turnover_acceleration,
        atr_15m=atr_15m,
        extension_atr=extension_atr,
        breakout_price=breakout_price,
        breakout_confirmed=breakout_confirmed,
        distance_to_breakout_atr=distance_to_breakout_atr,
        chase_risk=chase_risk,
        stage=stage,
        missing_data=[],
    )


def build_momentum_item(momentum: MomentumAnalysis, now: datetime) -> dict[str, Any]:
    instrument = momentum.instrument
    side = momentum.side
    recent = momentum.bars_15m[-8:]
    if side == "long":
        invalidation_price = min(bar.low for bar in recent)
        condition = "Closed 15m candle above the previous 20-bar 15m high"
        bullish = "Momentum remains constructive while 15m closes hold above the breakout area with sustained turnover."
        bearish = "Loss of the recent 15m swing low or sharp volume failure invalidates continuation."
    elif side == "short":
        invalidation_price = max(bar.high for bar in recent)
        condition = "Closed 15m candle below the previous 20-bar 15m low"
        bullish = "A fast reclaim of the breakdown area creates squeeze risk and invalidates the bearish impulse."
        bearish = "Momentum remains constructive for the short side while 15m closes hold below the breakdown area."
    else:
        invalidation_price = None
        condition = "No trigger: insufficient 15m price movement"
        bullish = "No bullish momentum thesis is available because the market has no usable short-term range."
        bearish = "No bearish momentum thesis is available because the market has no usable short-term range."

    if momentum.stage == "NO_ACTIVITY":
        execution_status = "NO_ACTIVITY"
    elif momentum.chase_risk and not instrument.tradeable:
        execution_status = "LIQUIDITY_BLOCKED_AND_CHASE_RISK"
    elif momentum.chase_risk:
        execution_status = "NO_TRADE_CHASE_RISK"
    elif not instrument.tradeable:
        execution_status = "LIQUIDITY_BLOCKED"
    else:
        execution_status = "TRADEABLE_MOMENTUM_WATCH"

    if momentum.stage == "NO_ACTIVITY":
        state = "NO_TRADE"
    else:
        state = "TRIGGERED" if momentum.breakout_confirmed and momentum.score >= 70 else "WATCH" if momentum.score >= MOMENTUM_MIN_SCORE else "NO_TRADE"
    reasons = [
        f"{momentum.return_1h_pct:+.2f}% 1H move",
        f"{momentum.volume_ratio_5m:.2f}x 5m relative volume",
        f"{momentum.turnover_acceleration_1h:.2f}x hourly turnover acceleration",
    ]
    if momentum.breakout_confirmed:
        reasons.append("15m range breakout confirmed on a closed candle")
    if momentum.chase_risk:
        reasons.append("Move is extended; chasing is prohibited")
    if not instrument.tradeable:
        reasons.extend(instrument.liquidity_reasons)

    return {
        "symbol": instrument.symbol,
        "base_asset": instrument.base,
        "quote_asset": "USDC",
        "side": side,
        "state": state,
        "stage": momentum.stage,
        "momentum_score": round(momentum.score, 2),
        "last_price": instrument.last_price,
        "price_change_24h_pct": round(instrument.price_change_24h_pct, 3),
        "return_15m_pct": round(momentum.return_15m_pct, 3),
        "return_1h_pct": round(momentum.return_1h_pct, 3),
        "return_4h_pct": round(momentum.return_4h_pct, 3),
        "acceleration_pct": round(momentum.acceleration_pct, 3),
        "volume_ratio_5m": round(momentum.volume_ratio_5m, 3),
        "turnover_acceleration_1h": round(momentum.turnover_acceleration_1h, 3),
        "extension_atr_15m": round(momentum.extension_atr, 3),
        "breakout_confirmed": momentum.breakout_confirmed,
        "chase_risk": momentum.chase_risk,
        "tradeable": instrument.tradeable,
        "execution_status": execution_status,
        "turnover_24h_usdc": round(instrument.turnover_24h, 2),
        "spread_bps": round(instrument.spread_bps, 3),
        "trigger": {
            "timeframe": "15m",
            "condition": condition,
            "price": None if side == "neutral" else round_to_tick(momentum.breakout_price, instrument.tick_size),
            "requires_close": True,
            "volume_confirmation": None if side == "neutral" else "Prefer >=2.0x recent 5m relative volume and rising hourly turnover",
        },
        "invalidation_price": None if invalidation_price is None else round_to_tick(invalidation_price, instrument.tick_size),
        "bullish_scenario": bullish,
        "bearish_scenario": bearish,
        "why_now": reasons,
        "decision": "NO_TRADE" if momentum.stage == "NO_ACTIVITY" or momentum.chase_risk or not instrument.tradeable else "WATCH_FOR_TRIGGER",
        "data_as_of": now.isoformat(),
    }


def rank_momentum(
    momentums: list[MomentumAnalysis],
    now: datetime,
    cache_min_score: float = 0.0,
) -> list[dict[str, Any]]:
    """Cache low-score items too so min_score=0 is a real audit request."""
    ranked = sorted(
        [item for item in momentums if item.score >= cache_min_score],
        key=lambda item: (
            item.stage == "BREAKOUT_CONFIRMED",
            item.stage == "EARLY_ACCELERATION",
            item.stage == "MOMENTUM_WATCH",
            item.score,
            abs(item.instrument.price_change_24h_pct),
            item.instrument.turnover_24h,
        ),
        reverse=True,
    )
    return [build_momentum_item(item, now) for item in ranked[:MOMENTUM_OUTPUT_LIMIT]]


def analyze_market(
    instrument: Instrument,
    bars_1h: list[Bar],
    bars_4h: list[Bar],
    bars_1d: list[Bar],
    btc_return_4h: float,
) -> Analysis:
    closes_4h = [bar.close for bar in bars_4h]
    closes_1d = [bar.close for bar in bars_1d]
    current = closes_4h[-1]
    atr_4h = atr(bars_4h, 14)
    if atr_4h <= 0 or current <= 0:
        raise RuntimeError("Invalid ATR or close")

    ema20_4h = ema(closes_4h, 20)
    ema50_4h = ema(closes_4h, 50)
    ema20_1d = ema(closes_1d, 20)
    ema50_1d = ema(closes_1d, 50)
    range_window = bars_4h[-21:-1]
    range_high = max(bar.high for bar in range_window)
    range_low = min(bar.low for bar in range_window)
    recent_window = bars_4h[-8:]
    recent_high = max(bar.high for bar in recent_window)
    recent_low = min(bar.low for bar in recent_window)

    avg_volume = mean(bar.volume for bar in bars_4h[-21:-1])
    volume_ratio = bars_4h[-1].volume / avg_volume if avg_volume > 0 else 1.0

    widths = rolling_bollinger_widths(closes_4h[-120:], 20)
    current_width = widths[-1] if widths else bollinger_width(closes_4h, 20)
    bb_percentile = percentile_rank(widths[:-1] or widths, current_width)
    atr_values = rolling_atr_values(bars_4h[-120:], 14)
    atr_average = mean(atr_values[-50:])
    atr_ratio = atr_4h / atr_average if atr_average > 0 else 1.0

    compression_score = clamp((1.15 - atr_ratio) / 0.55 * 100.0)
    bb_compression_score = clamp(100.0 - bb_percentile)
    distance_to_boundary_atr = min(abs(range_high - current), abs(current - range_low)) / atr_4h
    boundary_score = clamp(100.0 - distance_to_boundary_atr * 45.0)
    volume_score = clamp((volume_ratio - 0.55) / 1.45 * 100.0)
    range_width_atr = (range_high - range_low) / atr_4h
    maturity_score = clamp((8.0 - abs(range_width_atr - 6.0)) / 8.0 * 100.0)

    expansion_score = (
        0.25 * compression_score
        + 0.25 * bb_compression_score
        + 0.20 * boundary_score
        + 0.15 * volume_score
        + 0.15 * maturity_score
    )

    direction = 0.0
    if current > ema20_4h > ema50_4h:
        direction += 25.0
    elif current < ema20_4h < ema50_4h:
        direction -= 25.0
    else:
        direction += clamp((current / ema50_4h - 1.0) * 400.0, -12.0, 12.0) if ema50_4h > 0 else 0.0

    close_1d = closes_1d[-1]
    if close_1d > ema20_1d > ema50_1d:
        direction += 20.0
    elif close_1d < ema20_1d < ema50_1d:
        direction -= 20.0

    coin_return_4h = return_pct(closes_4h, 20)
    relative_strength = coin_return_4h - btc_return_4h
    direction += clamp(relative_strength * 2.0, -15.0, 15.0)

    range_position = (current - range_low) / max(range_high - range_low, 1e-12)
    direction += clamp((range_position - 0.5) * 30.0, -15.0, 15.0)
    last_bar = bars_4h[-1]
    candle_sign = 1.0 if last_bar.close > last_bar.open else -1.0 if last_bar.close < last_bar.open else 0.0
    direction += candle_sign * clamp((volume_ratio - 0.8) * 5.0, 0.0, 5.0)
    direction = clamp(direction, -100.0, 100.0)

    turnover_component = clamp((math.log10(max(instrument.turnover_24h, 1.0)) - 6.0) / 2.0 * 20.0, 5.0, 20.0)
    spread_component = clamp((MAX_SPREAD_BPS - instrument.spread_bps) / MAX_SPREAD_BPS * 20.0, 5.0, 20.0)
    confluence_component = clamp(abs(direction) / 100.0 * 25.0, 5.0, 25.0)
    trigger_component = clamp(15.0 - max(0.0, distance_to_boundary_atr - 0.5) * 5.0, 5.0, 15.0)
    data_component = 10.0
    rr_component = 25.0
    quality_score = turnover_component + spread_component + confluence_component + trigger_component + data_component + rr_component

    return Analysis(
        instrument=instrument,
        bars_1h=bars_1h,
        bars_4h=bars_4h,
        bars_1d=bars_1d,
        atr_4h=atr_4h,
        ema20_4h=ema20_4h,
        ema50_4h=ema50_4h,
        ema20_1d=ema20_1d,
        ema50_1d=ema50_1d,
        range_high=range_high,
        range_low=range_low,
        recent_high=recent_high,
        recent_low=recent_low,
        volume_ratio=volume_ratio,
        bb_width_percentile=bb_percentile,
        atr_ratio=atr_ratio,
        expansion_score=clamp(expansion_score),
        direction_score=direction,
        quality_score=clamp(quality_score),
        relative_strength_4h=relative_strength,
        structure_4h=structure_label(current, ema20_4h, ema50_4h),
        structure_1d=structure_label(close_1d, ema20_1d, ema50_1d),
        derivatives={},
        missing_data=[],
    )


COINALYZE_REQUIRED_FIELDS = ("current_oi", "funding", "oi_history", "liquidations")


def coinalyze_payload_complete(payload: dict[str, Any]) -> bool:
    if not payload or payload.get("endpoint_errors"):
        return False
    availability = payload.get("availability")
    return bool(
        isinstance(availability, dict)
        and all(bool(availability.get(field)) for field in COINALYZE_REQUIRED_FIELDS)
    )


def select_coinalyze_markets(
    markets: list[dict[str, Any]],
    bases: list[str],
    *,
    exchange_names: dict[str, str] | None = None,
    quote_order: tuple[str, ...] = ("USDC", "USDT", "USD"),
) -> dict[str, dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    priorities = {"bybit": 0, "binance": 1, "okx": 2, "deribit": 3}
    exchange_names = {
        str(code).upper(): str(name)
        for code, name in (exchange_names or {}).items()
        if str(code) and str(name)
    }
    quote_priorities = {quote.upper(): index for index, quote in enumerate(quote_order)}
    for base in bases:
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for market in markets:
            if str(market.get("base_asset", "")).upper() != base:
                continue
            if not bool(market.get("is_perpetual", False)):
                continue
            quote = str(market.get("quote_asset", "")).upper()
            if quote not in quote_priorities:
                continue
            raw_exchange = str(market.get("exchange", "")).strip()
            exchange_name = exchange_names.get(raw_exchange.upper(), raw_exchange)
            exchange_for_rank = exchange_name.lower()
            priority = next(
                (value for key, value in priorities.items() if key in exchange_for_rank),
                9,
            )
            resolved_market = dict(market)
            resolved_market["exchange_code"] = raw_exchange
            resolved_market["exchange_name"] = exchange_name
            candidates.append((priority, quote_priorities[quote], resolved_market))
        if candidates:
            candidates.sort(key=lambda item: (item[0], item[1]))
            selected[base] = candidates[0][2]
    return selected


def select_coinalyze_targets(
    analyses: list[Analysis],
    priority_symbols: list[str] | None = None,
) -> list[Analysis]:
    """Select the rate-budget target set with compact top/watch candidates first."""
    priority_symbols = list(dict.fromkeys(priority_symbols or []))
    if len(priority_symbols) > COINALYZE_ENRICH_LIMIT:
        raise RuntimeError(
            "Coinalyze compact priority set exceeds safe rate budget: "
            f"{len(priority_symbols)}>{COINALYZE_ENRICH_LIMIT}; "
            "refusing to publish silently incomplete top/watch coverage"
        )

    by_symbol = {item.instrument.symbol: item for item in analyses}
    missing_priority = [symbol for symbol in priority_symbols if symbol not in by_symbol]
    if missing_priority:
        raise RuntimeError(
            "Compact priority symbols missing from swing analyses: "
            + ", ".join(missing_priority)
        )

    prioritized = [by_symbol[symbol] for symbol in priority_symbols]
    priority_set = set(priority_symbols)
    secondary = sorted(
        [item for item in analyses if item.instrument.symbol not in priority_set],
        key=lambda item: (
            setup_score(
                item.expansion_score,
                abs(item.direction_score),
                item.quality_score,
            )
            + (5.0 if item.instrument.symbol in DISCOVERY_SYMBOLS else 0.0)
        ),
        reverse=True,
    )
    remaining = COINALYZE_ENRICH_LIMIT - len(prioritized)
    return prioritized + secondary[:remaining]


async def enrich_coinalyze(
    analyses: list[Analysis],
    api: CoinalyzeAPI,
    *,
    mutate_scores: bool = True,
    partial_safe: bool = False,
    target_analyses: list[Analysis] | None = None,
) -> tuple[bool, str | None]:
    """Attach Coinalyze derivatives context.

    Day-trade keeps the legacy all-or-nothing score-enrichment path by
    default. Swing opts into ``partial_safe=True`` and
    ``mutate_scores=False`` so upstream context can never change swing
    strict eligibility and one failed endpoint cannot erase successful
    context from the other endpoints.
    """
    if not COINALYZE_API_KEY or not analyses:
        for analysis in analyses:
            analysis.missing_data.append("Coinalyze derivatives data")
        return False, "COINALYZE_API_KEY missing"

    selected_analyses = (
        list(target_analyses)
        if target_analyses is not None
        else select_coinalyze_targets(analyses)
    )
    if len(selected_analyses) > COINALYZE_ENRICH_LIMIT:
        raise RuntimeError(
            "Coinalyze target set exceeds safe rate budget: "
            f"{len(selected_analyses)}>{COINALYZE_ENRICH_LIMIT}"
        )
    targeted_count = len(selected_analyses)
    selected_symbols = {
        item.instrument.symbol for item in selected_analyses
    }

    try:
        markets = await api.future_markets()
    except Exception as exc:
        for analysis in analyses:
            analysis.missing_data.append("Coinalyze future-markets failed")
        return False, f"future-markets: {type(exc).__name__}: {exc}"

    exchange_names: dict[str, str] = {}
    exchange_metadata_error: str | None = None
    # Derivatives are context-only for execution semantics. Prefer the more
    # complete/liquid Bybit USDT perpetual context for both day and swing,
    # while all executable spot instruments remain strictly USDC-only.
    quote_order = ("USDT", "USDC", "USD")
    # Coinalyze future-markets exposes an exchange code, not a venue name.
    # Resolve that code before venue ranking for both engines.
    try:
        exchange_rows = await api.exchanges()
        exchange_names = {
            str(row.get("code", "")).upper(): str(row.get("name", ""))
            for row in exchange_rows
            if isinstance(row, dict) and row.get("code") and row.get("name")
        }
    except Exception as exc:
        exchange_metadata_error = f"exchanges: {type(exc).__name__}: {exc}"
        if not partial_safe:
            for item in selected_analyses:
                item.missing_data.append(
                    "Coinalyze exchange metadata unavailable; derivatives enrichment skipped"
                )
            return False, exchange_metadata_error

    market_map = select_coinalyze_markets(
        markets,
        [item.instrument.base for item in selected_analyses],
        exchange_names=exchange_names,
        quote_order=quote_order,
    )
    symbols = [
        str(market_map[item.instrument.base]["symbol"])
        for item in selected_analyses
        if item.instrument.base in market_map
    ]
    if not symbols:
        for analysis in analyses:
            analysis.missing_data.append(
                "No matching Coinalyze perpetual market"
            )
        return False, "No matching Coinalyze markets"

    now_ts = int(time.time())
    from_ts = now_ts - 3 * 24 * 60 * 60
    endpoint_errors: list[str] = (
        [exchange_metadata_error] if exchange_metadata_error else []
    )

    if partial_safe:
        async def optional(label: str, awaitable: Any) -> Any:
            try:
                return await awaitable
            except Exception as exc:
                endpoint_errors.append(
                    f"{label}: {type(exc).__name__}: {exc}"
                )
                return []

        # Sequential batches avoid a four-request burst. With the
        # default nine-symbol target, the four symbol endpoints consume
        # 36 symbol-calls, leaving limited headroom below Coinalyze's
        # documented 40 symbol-call/minute API-key limit.
        current_oi = await optional(
            "open-interest",
            api.batch_current(
                "/open-interest", symbols, convert_to_usd=True
            ),
        )
        current_funding = await optional(
            "funding-rate",
            api.batch_current("/funding-rate", symbols),
        )
        oi_history = await optional(
            "open-interest-history",
            api.batch_history(
                "/open-interest-history",
                symbols,
                from_ts,
                now_ts,
                convert_to_usd=True,
                interval="1hour",
            ),
        )
        liquidation_history = await optional(
            "liquidation-history",
            api.batch_history(
                "/liquidation-history",
                symbols,
                from_ts,
                now_ts,
                convert_to_usd=True,
                interval="4hour",
            ),
        )
    else:
        # Backward-compatible day-trade behavior: any endpoint failure
        # aborts the enrichment and leaves scores untouched.
        try:
            (
                current_oi,
                current_funding,
                oi_history,
                liquidation_history,
            ) = await asyncio.gather(
                api.batch_current(
                    "/open-interest", symbols, convert_to_usd=True
                ),
                api.batch_current("/funding-rate", symbols),
                api.batch_history(
                    "/open-interest-history",
                    symbols,
                    from_ts,
                    now_ts,
                    convert_to_usd=True,
                    interval="1hour",
                ),
                api.batch_history(
                    "/liquidation-history",
                    symbols,
                    from_ts,
                    now_ts,
                    convert_to_usd=True,
                    interval="4hour",
                ),
            )
        except Exception as exc:
            for analysis in analyses:
                analysis.missing_data.append(
                    "Coinalyze enrichment failed"
                )
            return False, str(exc)

    oi_map = {
        item["symbol"]: item
        for item in current_oi
        if isinstance(item, dict) and item.get("symbol")
    }
    funding_map = {
        item["symbol"]: item
        for item in current_funding
        if isinstance(item, dict) and item.get("symbol")
    }
    oi_hist_map = {
        item["symbol"]: item.get("history", [])
        for item in oi_history
        if isinstance(item, dict) and item.get("symbol")
    }
    liq_map = {
        item["symbol"]: item.get("history", [])
        for item in liquidation_history
        if isinstance(item, dict) and item.get("symbol")
    }

    enriched_count = 0
    complete_enriched_count = 0
    missing_by_field: dict[str, list[str]] = {
        field: [] for field in COINALYZE_REQUIRED_FIELDS
    }
    for analysis in analyses:
        market = market_map.get(analysis.instrument.base)
        if not market:
            if analysis.instrument.symbol in selected_symbols:
                analysis.missing_data.append(
                    "No matching Coinalyze perpetual market"
                )
            continue

        symbol = str(market["symbol"])
        has_current_oi = symbol in oi_map
        has_funding = symbol in funding_map
        has_oi_history = symbol in oi_hist_map
        has_liquidations = symbol in liq_map
        availability = {
            "current_oi": has_current_oi,
            "funding": has_funding,
            "oi_history": has_oi_history,
            "liquidations": has_liquidations,
        }
        if analysis.instrument.symbol in selected_symbols:
            for field, available in availability.items():
                if not available:
                    missing_by_field[field].append(analysis.instrument.symbol)
        if not any(availability.values()):
            analysis.missing_data.append(
                "Coinalyze data unavailable for matched market"
            )
            continue

        oi_rows = oi_hist_map.get(symbol, [])

        def oi_change(periods: int) -> float | None:
            if len(oi_rows) < periods + 1:
                return None
            latest = safe_float(oi_rows[-1].get("c"))
            prior = safe_float(oi_rows[-periods - 1].get("c"))
            if latest <= 0 or prior <= 0:
                return None
            return (latest / prior - 1.0) * 100.0

        oi_change_1h_pct = oi_change(1)
        oi_change_4h_pct = oi_change(4)
        oi_change_24h_pct = oi_change(24)
        liq_rows = (
            liq_map.get(symbol, [])[-6:] if has_liquidations else []
        )
        long_liq = (
            sum(safe_float(row.get("l")) for row in liq_rows)
            if has_liquidations
            else None
        )
        short_liq = (
            sum(safe_float(row.get("s")) for row in liq_rows)
            if has_liquidations
            else None
        )
        funding = (
            safe_float(funding_map[symbol].get("value"), 0.0)
            if has_funding
            else None
        )
        current_oi_value = (
            safe_float(oi_map[symbol].get("value"), 0.0)
            if has_current_oi
            else None
        )

        direction_delta = 0.0
        expansion_delta = 0.0
        quality_delta = 5.0
        price_direction = (
            1.0 if analysis.direction_score >= 0 else -1.0
        )
        if oi_change_24h_pct is not None:
            oi_effect = clamp(
                abs(oi_change_24h_pct) * 0.8, 0.0, 10.0
            )
            if oi_change_24h_pct > 0:
                direction_delta += price_direction * oi_effect
                expansion_delta += clamp(
                    abs(oi_change_24h_pct) * 0.5, 0.0, 8.0
                )
            else:
                quality_delta -= clamp(
                    abs(oi_change_24h_pct) * 0.3, 0.0, 5.0
                )

        if funding is not None:
            if funding > 0.001:
                direction_delta -= 8.0
            elif funding < -0.001:
                direction_delta += 8.0
            elif (
                0 < funding <= 0.0005
                and analysis.direction_score > 0
            ):
                direction_delta += 2.0
            elif (
                -0.0005 <= funding < 0
                and analysis.direction_score < 0
            ):
                direction_delta -= 2.0

        if has_liquidations:
            liquidation_ratio = (
                (long_liq or 0.0) + (short_liq or 0.0)
            ) / max(analysis.instrument.turnover_24h, 1.0)
            expansion_delta += clamp(
                liquidation_ratio * 300.0, 0.0, 5.0
            )

        analysis.derivatives = {
            "source": "Coinalyze",
            "market_symbol": symbol,
            "exchange": market.get("exchange_name") or market.get("exchange"),
            "exchange_code": market.get("exchange_code") or market.get("exchange"),
            "quote_asset": market.get("quote_asset"),
            "open_interest_usd": current_oi_value,
            "oi_change_1h_pct": oi_change_1h_pct,
            "oi_change_4h_pct": oi_change_4h_pct,
            "oi_change_24h_pct": oi_change_24h_pct,
            "funding_rate": funding,
            "long_liquidations_24h_usd": long_liq,
            "short_liquidations_24h_usd": short_liq,
            "is_bybit_specific": "bybit"
            in str(market.get("exchange_name") or market.get("exchange", "")).lower(),
            "availability": availability,
            "context_score_adjustments": {
                "expansion": round(expansion_delta, 6),
                "direction": round(direction_delta, 6),
                "quality": round(quality_delta, 6),
            },
            "strict_score_mutation_applied": mutate_scores,
            "endpoint_errors": list(endpoint_errors),
        }
        enriched_count += 1
        if coinalyze_payload_complete(analysis.derivatives):
            complete_enriched_count += 1
        else:
            analysis.missing_data.append(
                "Coinalyze derivatives context partial"
            )

        if mutate_scores:
            analysis.expansion_score = clamp(
                analysis.expansion_score + expansion_delta
            )
            analysis.direction_score = clamp(
                analysis.direction_score + direction_delta,
                -100.0,
                100.0,
            )
            analysis.quality_score = clamp(
                analysis.quality_score + quality_delta
            )

    error_parts = list(endpoint_errors)
    if len(symbols) < targeted_count:
        error_parts.append(
            f"market-match coverage {len(symbols)}/{targeted_count}"
        )
    if enriched_count < targeted_count:
        error_parts.append(
            f"any-field enrichment coverage {enriched_count}/{targeted_count}"
        )
    for field, missing_symbols in missing_by_field.items():
        if missing_symbols:
            error_parts.append(
                f"{field} missing for {','.join(sorted(set(missing_symbols)))}"
            )
    if complete_enriched_count < targeted_count:
        error_parts.append(
            f"complete payload coverage {complete_enriched_count}/{targeted_count}"
        )

    complete = complete_enriched_count == targeted_count and not error_parts
    return complete, "; ".join(error_parts) if error_parts else None


async def apply_shortability(analyses: list[Analysis], bybit: BybitAPI) -> tuple[bool, str | None]:
    try:
        margin_data = await bybit.vip_margin_data()
        if not margin_data:
            raise RuntimeError("Bybit public Spot Margin data returned no currencies")
        for analysis in analyses:
            info = margin_data.get(analysis.instrument.base, {})
            borrowable = bool(info.get("borrowable", False))
            max_borrow = safe_float(info.get("maxBorrowingAmount"), 0.0)
            margin_flag = analysis.instrument.margin_trading.strip().lower()
            margin_pair = margin_flag not in {"", "none"}
            analysis.shortable = bool(borrowable and max_borrow > 0 and margin_pair)
            analysis.max_borrowing_amount = max_borrow
            if margin_pair and not analysis.shortable:
                analysis.missing_data.append(
                    "USDC spot-margin base coin is not borrowable in public Bybit margin data"
                )
        return True, None
    except Exception as exc:
        for analysis in analyses:
            analysis.missing_data.append("Bybit public Spot Margin borrowability check failed")
        return False, str(exc)


def setup_score(expansion: float, directional_strength: float, quality: float) -> float:
    return clamp(0.35 * expansion + 0.35 * directional_strength + 0.30 * quality)


def setup_grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "WATCH"
    return "NO_TRADE"


def build_setup(analysis: Analysis, side: str, now: datetime) -> dict[str, Any] | None:
    if not analysis.instrument.tradeable:
        return None
    if side == "long" and analysis.direction_score < 20:
        return None
    if side == "short" and (analysis.direction_score > -20 or not analysis.shortable):
        return None

    directional_strength = analysis.direction_score if side == "long" else -analysis.direction_score
    score = setup_score(analysis.expansion_score, directional_strength, analysis.quality_score)
    if score < 55:
        return None

    instrument = analysis.instrument
    atr_value = analysis.atr_4h
    last_price = instrument.last_price
    if side == "long":
        trigger_price = analysis.range_high
        entry_low = trigger_price
        entry_high = trigger_price + 0.25 * atr_value
        entry_mid = (entry_low + entry_high) / 2.0
        technical_stop = analysis.recent_low - 0.10 * atr_value
        stop = max(technical_stop, entry_mid - 2.0 * atr_value)
        if stop >= entry_mid:
            stop = entry_mid - 1.2 * atr_value
        risk = entry_mid - stop
        targets = [entry_mid + multiple * risk for multiple in (1.5, 2.5, 3.5)]
        breakout = analysis.bars_4h[-1].close > analysis.range_high
        distance_atr = max(trigger_price - last_price, 0.0) / atr_value
        thesis = [
            f"4H structure: {analysis.structure_4h}",
            f"1D structure: {analysis.structure_1d}",
            f"BTC-relative 20x4H strength: {analysis.relative_strength_4h:+.2f}%",
            f"4H volume ratio: {analysis.volume_ratio:.2f}x",
        ]
        bullish = "4H acceptance above the prior 20-bar range high with volume confirmation activates continuation."
        bearish = "Failure at the range high followed by a 4H close below the recent swing low invalidates the long thesis."
        invalidation = f"4H close below {round_to_tick(stop, instrument.tick_size)} or loss of the recent higher-low structure."
        condition = "4H close above the previous 20-bar range high"
    else:
        trigger_price = analysis.range_low
        entry_high = trigger_price
        entry_low = trigger_price - 0.25 * atr_value
        entry_mid = (entry_low + entry_high) / 2.0
        technical_stop = analysis.recent_high + 0.10 * atr_value
        stop = min(technical_stop, entry_mid + 2.0 * atr_value)
        if stop <= entry_mid:
            stop = entry_mid + 1.2 * atr_value
        risk = stop - entry_mid
        targets = [entry_mid - multiple * risk for multiple in (1.5, 2.5, 3.5)]
        breakout = analysis.bars_4h[-1].close < analysis.range_low
        distance_atr = max(last_price - trigger_price, 0.0) / atr_value
        thesis = [
            f"4H structure: {analysis.structure_4h}",
            f"1D structure: {analysis.structure_1d}",
            f"BTC-relative 20x4H strength: {analysis.relative_strength_4h:+.2f}%",
            f"Public Bybit max borrowing amount: {analysis.max_borrowing_amount:g} {instrument.base}",
        ]
        bullish = "Reclaim of the range low and a 4H close back above the recent lower high invalidates short continuation."
        bearish = "4H acceptance below the prior 20-bar range low with expanding volume activates the spot-margin short."
        invalidation = f"4H close above {round_to_tick(stop, instrument.tick_size)} or reclaim of the recent lower-high structure."
        condition = "4H close below the previous 20-bar range low"

    state = "TRIGGERED" if breakout and score >= 70 else "ARMED" if distance_atr <= 1.0 and score >= 70 else "WATCH"
    grade = setup_grade(score)
    data_quality = "GOOD" if analysis.derivatives and not analysis.missing_data else "PARTIAL"
    confidence = "HIGH" if score >= 80 and data_quality == "GOOD" else "MEDIUM" if score >= 70 else "LOW"
    risks = [
        "BTC regime reversal can invalidate altcoin relative-strength signals.",
        "Coinalyze derivatives context may represent another venue or an aggregate market.",
    ]
    if side == "short":
        risks.append("Public borrowability is not an execution guarantee; inventory and borrow cost can change before entry.")
    if analysis.volume_ratio < 1.0:
        risks.append("Current 4H volume has not yet confirmed expansion.")

    metrics = {
        "turnover_24h_usdc": round(instrument.turnover_24h, 2),
        "spread_bps": round(instrument.spread_bps, 3),
        "atr_4h": atr_value,
        "atr_percent_of_price": round(atr_value / last_price * 100.0, 3),
        "atr_ratio_to_50bar_average": round(analysis.atr_ratio, 3),
        "bb_width_percentile": round(analysis.bb_width_percentile, 2),
        "volume_ratio_4h": round(analysis.volume_ratio, 3),
        "relative_strength_vs_btc_20x4h_pct": round(analysis.relative_strength_4h, 3),
        "margin_trading_flag": instrument.margin_trading,
        "max_borrowing_amount_base": analysis.max_borrowing_amount,
        "derivatives": analysis.derivatives,
    }

    execution_modes = ["spot_usdc"] if side == "long" else ["spot_margin_short_usdc"]
    return {
        "symbol": instrument.symbol,
        "base_asset": instrument.base,
        "quote_asset": "USDC",
        "side": side,
        "state": state,
        "grade": grade,
        "confidence": confidence,
        "last_price": last_price,
        "shortable": analysis.shortable,
        "execution_modes": execution_modes,
        "setup_type": "20-bar range expansion / structure continuation",
        "thesis": thesis,
        "expansion_score": round(analysis.expansion_score, 2),
        "direction_score": round(analysis.direction_score, 2),
        "quality_score": round(analysis.quality_score, 2),
        "setup_score": round(score, 2),
        "trigger": {
            "timeframe": "4H",
            "condition": condition,
            "price": round_to_tick(trigger_price, instrument.tick_size),
            "requires_close": True,
            "volume_confirmation": "Prefer >=1.2x the prior 20-bar average 4H volume",
        },
        "entry_zone": {
            "low": round_to_tick(entry_low, instrument.tick_size),
            "high": round_to_tick(entry_high, instrument.tick_size),
        },
        "stop": round_to_tick(stop, instrument.tick_size),
        "invalidation": invalidation,
        "targets": [round_to_tick(value, instrument.tick_size) for value in targets],
        "expected_rr": 2.5,
        "expected_holding_days": "2-10",
        "metrics": metrics,
        "bullish_scenario": bullish,
        "bearish_scenario": bearish,
        "weakest_point": "The setup is heuristic and remains unconfirmed until the 4H trigger closes with volume.",
        "risks": risks,
        "data_quality": data_quality,
        "missing_data": sorted(set(analysis.missing_data)),
        "data_as_of": now.isoformat(),
    }



def build_watch_setup(analysis: Analysis, now: datetime) -> dict[str, Any]:
    """Return a Setup-compatible WATCH record, including low-liquidity coins."""
    instrument = analysis.instrument
    side = "long" if analysis.direction_score >= 10 else "short" if analysis.direction_score <= -10 else "neutral"
    directional_strength = abs(analysis.direction_score)
    score = setup_score(analysis.expansion_score, directional_strength, analysis.quality_score)
    atr_value = analysis.atr_4h
    last_price = instrument.last_price

    if side == "short":
        trigger_price = analysis.range_low
        entry_low = trigger_price - 0.25 * atr_value
        entry_high = trigger_price
        stop = analysis.recent_high + 0.10 * atr_value
        condition = "4H close below the previous 20-bar range low"
        invalidation = f"4H close above {round_to_tick(stop, instrument.tick_size)} or reclaim of the recent lower-high structure."
        targets = [trigger_price - multiple * max(stop - trigger_price, atr_value) for multiple in (1.5, 2.5, 3.5)]
        bullish_scenario = "A reclaim above the recent lower high cancels the bearish watch thesis."
        bearish_scenario = "Acceptance below the range low with volume would strengthen the bearish scenario."
    else:
        trigger_price = analysis.range_high
        entry_low = trigger_price
        entry_high = trigger_price + 0.25 * atr_value
        stop = analysis.recent_low - 0.10 * atr_value
        condition = "4H close above the previous 20-bar range high"
        invalidation = f"4H close below {round_to_tick(stop, instrument.tick_size)} or loss of the recent higher-low structure."
        targets = [trigger_price + multiple * max(trigger_price - stop, atr_value) for multiple in (1.5, 2.5, 3.5)]
        bullish_scenario = "Acceptance above the range high with volume would strengthen the bullish scenario."
        bearish_scenario = "Rejection at the range high and loss of the recent swing low cancels the bullish watch thesis."

    liquidity_blocked = not instrument.tradeable
    reasons = list(instrument.liquidity_reasons)
    if side == "short" and not analysis.shortable:
        reasons.append("USDC spot-margin shortability is not currently verified")
    if directional_strength < 35:
        reasons.append("Directional score below executable threshold")
    if analysis.expansion_score < 55:
        reasons.append("Expansion score below executable threshold")
    if analysis.quality_score < 60:
        reasons.append("Quality score below executable threshold")

    data_quality = "GOOD" if analysis.derivatives and not analysis.missing_data else "PARTIAL"
    execution_modes = ["spot_usdc_watch_only"]
    if side == "short":
        execution_modes = ["spot_margin_short_usdc_watch_only"]

    return {
        "symbol": instrument.symbol,
        "base_asset": instrument.base,
        "quote_asset": "USDC",
        "side": side,
        "state": "WATCH" if score >= 60 else "NO_TRADE",
        "grade": "WATCH" if score >= 60 else "NO_TRADE",
        "confidence": "MEDIUM" if score >= 70 and data_quality == "GOOD" else "LOW",
        "last_price": last_price,
        "shortable": analysis.shortable,
        "execution_modes": execution_modes,
        "setup_type": "Extended discovery watch / 20-bar range expansion",
        "thesis": [
            f"4H structure: {analysis.structure_4h}",
            f"1D structure: {analysis.structure_1d}",
            f"BTC-relative 20x4H strength: {analysis.relative_strength_4h:+.2f}%",
            "WATCH_ONLY: technical interest is separated from execution quality.",
        ],
        "expansion_score": round(analysis.expansion_score, 2),
        "direction_score": round(analysis.direction_score, 2),
        "quality_score": round(analysis.quality_score, 2),
        "setup_score": round(score, 2),
        "trigger": {
            "timeframe": "4H",
            "condition": condition,
            "price": round_to_tick(trigger_price, instrument.tick_size),
            "requires_close": True,
            "volume_confirmation": "Prefer >=1.2x the prior 20-bar average 4H volume",
        },
        "entry_zone": {
            "low": round_to_tick(entry_low, instrument.tick_size),
            "high": round_to_tick(entry_high, instrument.tick_size),
        },
        "stop": round_to_tick(stop, instrument.tick_size),
        "invalidation": invalidation,
        "targets": [round_to_tick(value, instrument.tick_size) for value in targets],
        "expected_rr": 2.5,
        "expected_holding_days": "2-10",
        "metrics": {
            "execution_status": "LIQUIDITY_BLOCKED" if liquidity_blocked else "WATCH_ONLY",
            "liquidity_reasons": reasons,
            "turnover_24h_usdc": round(instrument.turnover_24h, 2),
            "spread_bps": round(instrument.spread_bps, 3),
            "tradeable": instrument.tradeable,
            "discovery_source": instrument.discovery_source,
            "coinalyze_enriched": bool(analysis.derivatives),
            "margin_trading_flag": instrument.margin_trading,
            "max_borrowing_amount_base": analysis.max_borrowing_amount,
            "atr_4h": atr_value,
            "volume_ratio_4h": round(analysis.volume_ratio, 3),
            "relative_strength_vs_btc_20x4h_pct": round(analysis.relative_strength_4h, 3),
            "derivatives": analysis.derivatives,
        },
        "bullish_scenario": bullish_scenario,
        "bearish_scenario": bearish_scenario,
        "weakest_point": "; ".join(reasons) if reasons else "Trigger is not yet confirmed.",
        "risks": [
            "WATCH_ONLY is not an execution signal.",
            "Low USDC turnover or wide spread can cause material slippage.",
            "Coinalyze derivatives context may be aggregated or from another venue.",
        ],
        "data_quality": data_quality,
        "missing_data": sorted(set(analysis.missing_data)),
        "data_as_of": now.isoformat(),
    }


def rank_watchlist(analyses: list[Analysis], now: datetime, excluded_symbols: set[str]) -> list[dict[str, Any]]:
    items = [build_watch_setup(item, now) for item in analyses if item.instrument.symbol not in excluded_symbols]
    items.sort(
        key=lambda item: (
            1 if item["symbol"] in DISCOVERY_SYMBOLS else 0,
            item["setup_score"],
        ),
        reverse=True,
    )
    return items


def build_market_regime(
    analyses: list[Analysis],
    now: datetime,
    coinalyze_ok: bool,
    borrow_ok: bool,
) -> dict[str, Any]:
    btc = next(
        (item for item in analyses if item.instrument.symbol == "BTCUSDC"),
        None,
    )
    if btc:
        if btc.direction_score >= 35:
            btc_regime = "bullish"
        elif btc.direction_score <= -35:
            btc_regime = "bearish"
        else:
            btc_regime = "neutral/range"
        if btc.bb_width_percentile <= 25 or btc.atr_ratio < 0.8:
            volatility = "compressed"
        elif btc.atr_ratio > 1.2:
            volatility = "expanding"
        else:
            volatility = "normal"
        structure_1d = btc.structure_1d
        structure_4h = btc.structure_4h
    else:
        btc_regime = "unavailable"
        volatility = "unknown"
        structure_1d = None
        structure_4h = None

    alt_analyses = [
        item
        for item in analyses
        if item.instrument.symbol != "BTCUSDC"
    ]
    directional = [item.direction_score for item in alt_analyses]
    alt_breadth = (
        100.0
        * sum(1 for value in directional if value > 20)
        / len(directional)
        if directional
        else 0.0
    )
    bearish_breadth = (
        100.0
        * sum(1 for value in directional if value < -20)
        / len(directional)
        if directional
        else 0.0
    )
    if btc_regime == "bullish" and alt_breadth >= 55:
        preferred = "long"
    elif btc_regime == "bearish" and bearish_breadth >= 55:
        preferred = "short"
    else:
        preferred = "neutral"

    enriched_count = sum(
        1 for item in analyses if item.derivatives
    )
    target_count = min(len(analyses), COINALYZE_ENRICH_LIMIT)
    if (
        target_count > 0
        and enriched_count >= target_count
        and coinalyze_ok
    ):
        coinalyze_quality = "GOOD"
    elif enriched_count > 0:
        coinalyze_quality = "PARTIAL"
    else:
        coinalyze_quality = "DEGRADED"

    quality = "GOOD" if (
        len(analyses) >= 10
        and coinalyze_quality == "GOOD"
        and borrow_ok
    ) else "PARTIAL"
    notes = [
        "Universe is strictly Bybit EU spot pairs quoted in USDC.",
        "Breadth is calculated from the broad discovery universe, excluding BTC.",
        "Executable setups and low-liquidity WATCH_ONLY ideas are separated.",
        "Short candidates are strictly USDC spot-margin shorts; derivatives are contextual only.",
        "Swing strict scores are core technical/execution scores; Coinalyze context cannot modify strict eligibility.",
        f"Coinalyze rate-budget enrichment: {enriched_count}/{target_count} selected targets ({len(analyses)} analyzed total); compact top/watch coverage is reported separately.",
        "Shortability uses public Bybit margin eligibility and maximum borrowing data; final inventory must be rechecked before entry.",
    ]
    if not coinalyze_ok:
        notes.append(
            "Coinalyze enrichment is partial or unavailable; available endpoint data is retained."
        )
    if not borrow_ok:
        notes.append(
            "Public Bybit Spot Margin borrowability data is unavailable; executable short list is suppressed."
        )
    return {
        "data_as_of": now.isoformat(),
        "data_quality": quality,
        "btc_regime": btc_regime,
        "btc_structure_1d": structure_1d,
        "btc_structure_4h": structure_4h,
        "alt_breadth": round(alt_breadth, 2),
        "volatility_regime": volatility,
        "preferred_side": preferred,
        "source_quality": {
            "bybit_market_data": "GOOD" if analyses else "DEGRADED",
            "bybit_spot_margin": "GOOD" if borrow_ok else "PARTIAL",
            "coinalyze_derivatives": coinalyze_quality,
        },
        "notes": notes,
    }


async def upsert_cache(connection: asyncpg.Connection, key: str, payload: dict[str, Any]) -> None:
    await connection.execute(
        """
        INSERT INTO radar_cache (cache_key, payload, updated_at)
        VALUES ($1, $2::jsonb, NOW())
        ON CONFLICT (cache_key)
        DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()
        """,
        key,
        json.dumps(payload, ensure_ascii=False),
    )


async def persist_results(
    scan: dict[str, Any],
    regime: dict[str, Any],
    setups: list[dict[str, Any]],
    watchlist: list[dict[str, Any]],
    momentum_radar: dict[str, Any],
    status: dict[str, Any],
) -> None:
    connection = await asyncpg.connect(DATABASE_URL, timeout=30)
    try:
        async with connection.transaction():
            await upsert_cache(connection, "latest_scan", scan)
            await upsert_cache(connection, "market_regime", regime)
            await upsert_cache(connection, "watchlist", {
                "data_as_of": scan["data_as_of"],
                "items": watchlist,
            })
            await upsert_cache(connection, "momentum_radar", momentum_radar)
            await upsert_cache(connection, "data_status", status)
            best_by_symbol: dict[str, dict[str, Any]] = {}
            for setup in setups + watchlist:
                symbol = setup["symbol"]
                existing = best_by_symbol.get(symbol)
                setup_is_executable = setup.get("metrics", {}).get("execution_status") not in {"WATCH_ONLY", "LIQUIDITY_BLOCKED"}
                existing_is_executable = bool(existing) and existing.get("metrics", {}).get("execution_status") not in {"WATCH_ONLY", "LIQUIDITY_BLOCKED"}
                if (
                    existing is None
                    or (setup_is_executable and not existing_is_executable)
                    or (setup_is_executable == existing_is_executable and setup["setup_score"] > existing["setup_score"])
                ):
                    best_by_symbol[symbol] = setup
            for setup in best_by_symbol.values():
                await upsert_cache(connection, f"setup:{setup['symbol']}", setup)
    finally:
        await connection.close()


async def run() -> None:
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    started = datetime.now(timezone.utc)
    timeout = httpx.Timeout(30.0, connect=15.0)
    limits = httpx.Limits(max_connections=10, max_keepalive_connections=10)
    async with httpx.AsyncClient(
        timeout=timeout,
        limits=limits,
        headers={"User-Agent": "Bybit-EU-Swing-Radar/0.4.0"},
    ) as client:
        bybit = BybitAPI(client)
        coinalyze = CoinalyzeAPI(client)

        instruments_raw, tickers_raw = await asyncio.gather(bybit.instruments(), bybit.tickers())
        universe, exclusions, universe_stats, candidate_pool = build_universe(instruments_raw, tickers_raw)
        if not candidate_pool:
            raise RuntimeError("No Bybit EU USDC spot pairs passed the discovery safety filters")

        momentum_semaphore = asyncio.Semaphore(MOMENTUM_HTTP_CONCURRENCY)
        momentum_fetched = await asyncio.gather(
            *(fetch_momentum_bars(bybit, instrument, momentum_semaphore) for instrument in candidate_pool)
        )
        momentum_by_symbol = {
            item[0].symbol: item for item in momentum_fetched if item is not None
        }

        for retry_pass in range(MOMENTUM_SYMBOL_RETRY_PASSES):
            missing_instruments = [
                instrument for instrument in candidate_pool
                if instrument.symbol not in momentum_by_symbol
            ]
            if not missing_instruments:
                break
            await asyncio.sleep(1.5 * (retry_pass + 1))
            retry_semaphore = asyncio.Semaphore(max(1, MOMENTUM_HTTP_CONCURRENCY // 2))
            retried = await asyncio.gather(
                *(fetch_momentum_bars(bybit, instrument, retry_semaphore) for instrument in missing_instruments)
            )
            for item in retried:
                if item is not None:
                    momentum_by_symbol[item[0].symbol] = item

        momentum_valid = list(momentum_by_symbol.values())
        momentum_failed_symbols = sorted(
            instrument.symbol for instrument in candidate_pool
            if instrument.symbol not in momentum_by_symbol
        )
        momentum_analyses: list[MomentumAnalysis] = []
        momentum_calculation_failures: list[dict[str, str]] = []
        for instrument, bars_5m, bars_15m in momentum_valid:
            try:
                momentum_analyses.append(analyze_momentum(instrument, bars_5m, bars_15m))
            except Exception as exc:
                failure = {"symbol": instrument.symbol, "reason": f"Momentum calculation failed: {exc}"}
                momentum_calculation_failures.append(failure)
                exclusions.append(failure)
        momentum_ranked_analyses = sorted(momentum_analyses, key=lambda item: item.score, reverse=True)
        momentum_promoted = [
            item.instrument for item in momentum_ranked_analyses
            if item.score >= MOMENTUM_MIN_SCORE
        ][:MOMENTUM_DEEP_LIMIT]
        seen = {item.symbol for item in universe}
        deep_cap = min(MAX_UNIVERSE + MOMENTUM_DEEP_LIMIT, 50)
        for instrument in momentum_promoted:
            if instrument.symbol not in seen and len(universe) < deep_cap:
                universe.append(instrument)
                seen.add(instrument.symbol)

        if not universe:
            raise RuntimeError("No Bybit EU USDC spot pairs selected for deep analysis")
        universe_stats.update({
            "analysis_universe_size": len(universe),
            "tradeable_universe_size": sum(1 for item in universe if item.tradeable),
            "liquidity_blocked_size": sum(1 for item in universe if not item.tradeable),
            "momentum_eligible_pairs": len(candidate_pool),
            "momentum_scanned_pairs": len(momentum_analyses),
            "momentum_failed_pairs": len(momentum_failed_symbols),
            "momentum_failed_symbols": momentum_failed_symbols,
            "momentum_calculation_failed_pairs": len(momentum_calculation_failures),
            "momentum_calculation_failures": momentum_calculation_failures,
            "momentum_no_activity_pairs": sum(1 for item in momentum_analyses if item.stage == "NO_ACTIVITY"),
            "momentum_candidates": sum(1 for item in momentum_analyses if item.score >= MOMENTUM_MIN_SCORE),
            "momentum_promoted_to_deep_scan": sum(1 for item in momentum_promoted if item.symbol in seen),
        })
        print(
            "USDC analysis universe: "
            f"{len(universe)} symbols; tradeable={universe_stats['tradeable_universe_size']}; "
            f"liquidity_blocked={universe_stats['liquidity_blocked_size']}; "
            f"mandatory_found={universe_stats['mandatory_found']}; "
            f"raw_usdc_records={universe_stats['raw_usdc_instrument_records']}; "
            f"unique_usdc={universe_stats['unique_usdc_instruments']}; "
            f"duplicate_records={universe_stats['duplicate_instrument_records']}; "
            f"momentum_scanned={len(momentum_analyses)}; momentum_candidates={universe_stats['momentum_candidates']}"
        )

        semaphore = asyncio.Semaphore(HTTP_CONCURRENCY)
        fetched = await asyncio.gather(
            *(fetch_analysis_bars(bybit, instrument, semaphore) for instrument in universe)
        )
        valid = [item for item in fetched if item is not None]
        missing_history = len(universe) - len(valid)
        if not valid:
            raise RuntimeError("No symbols had sufficient 1H/4H/1D history")

        btc_tuple = next((item for item in valid if item[0].symbol == "BTCUSDC"), None)
        btc_return_4h = return_pct([bar.close for bar in btc_tuple[2]], 20) if btc_tuple else 0.0
        analyses: list[Analysis] = []
        for instrument, bars_1h, bars_4h, bars_1d in valid:
            try:
                analyses.append(analyze_market(instrument, bars_1h, bars_4h, bars_1d, btc_return_4h))
            except Exception as exc:
                exclusions.append({"symbol": instrument.symbol, "reason": f"Feature calculation failed: {exc}"})

        # Shortability is execution semantics and must be known before deciding
        # which compact short/watch candidates receive the limited Coinalyze budget.
        borrow_ok, borrow_error = await apply_shortability(analyses, bybit)
        priority_now = datetime.now(timezone.utc)
        priority_longs = [
            setup for analysis in analyses
            if (setup := build_setup(analysis, "long", priority_now)) is not None
        ]
        priority_shorts = [
            setup for analysis in analyses
            if (setup := build_setup(analysis, "short", priority_now)) is not None
        ]
        priority_longs.sort(key=lambda item: item["setup_score"], reverse=True)
        priority_shorts.sort(key=lambda item: item["setup_score"], reverse=True)
        priority_executable_symbols = {
            item["symbol"] for item in priority_longs + priority_shorts
        }
        priority_all_watch = rank_watchlist(
            analyses, priority_now, priority_executable_symbols
        )
        priority_watch = priority_all_watch[:20]
        priority_liquidity_blocked = [
            item for item in priority_all_watch
            if item.get("metrics", {}).get("execution_status") == "LIQUIDITY_BLOCKED"
        ]
        compact_priority = select_compact_priority_sections(
            priority_longs[:10],
            priority_shorts[:10],
            priority_watch,
            priority_liquidity_blocked,
            limit=3,
        )
        coinalyze_priority_symbols = compact_priority["priority_symbols"]
        coinalyze_targets = select_coinalyze_targets(
            analyses, coinalyze_priority_symbols
        )
        coinalyze_target_symbols = [
            item.instrument.symbol for item in coinalyze_targets
        ]
        coinalyze_ok, coinalyze_error = await enrich_coinalyze(
            analyses,
            coinalyze,
            mutate_scores=False,
            partial_safe=True,
            target_analyses=coinalyze_targets,
        )
        now = datetime.now(timezone.utc)

        long_setups = [setup for analysis in analyses if (setup := build_setup(analysis, "long", now)) is not None]
        short_setups = [setup for analysis in analyses if (setup := build_setup(analysis, "short", now)) is not None]
        long_setups.sort(key=lambda item: item["setup_score"], reverse=True)
        short_setups.sort(key=lambda item: item["setup_score"], reverse=True)
        executable_symbols = {item["symbol"] for item in long_setups + short_setups}
        all_watchlist = rank_watchlist(analyses, now, executable_symbols)
        watchlist = all_watchlist[:20]
        liquidity_blocked = [
            item for item in all_watchlist
            if item.get("metrics", {}).get("execution_status") == "LIQUIDITY_BLOCKED"
        ]
        momentum_items = rank_momentum(momentum_analyses, now, cache_min_score=0.0)
        momentum_radar = {
            "data_as_of": now.isoformat(),
            "raw_usdc_instrument_records": universe_stats["raw_usdc_instrument_records"],
            "unique_usdc_instruments": universe_stats["unique_usdc_instruments"],
            "duplicate_instrument_records": universe_stats["duplicate_instrument_records"],
            "duplicate_symbols": universe_stats["duplicate_symbols"],
            "eligible_pairs": len(candidate_pool),
            "scanned_pairs": len(momentum_analyses),
            "failed_pairs": len(momentum_failed_symbols),
            "failed_symbols": momentum_failed_symbols,
            "calculation_failed_pairs": len(momentum_calculation_failures),
            "calculation_failures": momentum_calculation_failures,
            "no_activity_pairs": sum(1 for item in momentum_analyses if item.stage == "NO_ACTIVITY"),
            "minimum_score": 0.0,
            "promotion_minimum_score": MOMENTUM_MIN_SCORE,
            "items": momentum_items,
        }
        enriched_count = sum(1 for item in analyses if item.derivatives)
        coinalyze_complete_symbols = [
            item.instrument.symbol
            for item in coinalyze_targets
            if coinalyze_payload_complete(item.derivatives)
        ]
        coinalyze_complete_count = len(coinalyze_complete_symbols)
        coinalyze_target_count = len(coinalyze_targets)
        coinalyze_priority_targeted_symbols = [
            symbol for symbol in coinalyze_priority_symbols
            if symbol in set(coinalyze_target_symbols)
        ]
        coinalyze_priority_enriched_symbols = [
            symbol for symbol in coinalyze_priority_symbols
            if any(
                analysis.instrument.symbol == symbol and bool(analysis.derivatives)
                for analysis in analyses
            )
        ]
        coinalyze_priority_missing_symbols = [
            symbol for symbol in coinalyze_priority_symbols
            if symbol not in set(coinalyze_priority_enriched_symbols)
        ]
        coinalyze_priority_complete_symbols = [
            symbol for symbol in coinalyze_priority_symbols
            if symbol in set(coinalyze_complete_symbols)
        ]
        coinalyze_priority_partial_symbols = [
            symbol for symbol in coinalyze_priority_enriched_symbols
            if symbol not in set(coinalyze_priority_complete_symbols)
        ]
        regime = build_market_regime(analyses, now, coinalyze_ok, borrow_ok)
        regime["notes"].append(
            "Coinalyze compact top/watch priority coverage: "
            f"targeted={len(coinalyze_priority_targeted_symbols)}/"
            f"{len(coinalyze_priority_symbols)}, "
            f"enriched={len(coinalyze_priority_enriched_symbols)}/"
            f"{len(coinalyze_priority_symbols)}, "
            f"missing={coinalyze_priority_missing_symbols}."
        )
        data_quality = regime["data_quality"]
        coverage = {
            "raw_usdc_instrument_records": universe_stats["raw_usdc_instrument_records"],
            "unique_usdc_instruments": universe_stats["unique_usdc_instruments"],
            "duplicate_instrument_records": universe_stats["duplicate_instrument_records"],
            "duplicate_symbols": universe_stats["duplicate_symbols"],
            "analyzed_symbols": len(analyses),
            "coinalyze_enriched_symbols": enriched_count,
            "coinalyze_complete_symbols": coinalyze_complete_count,
            "coinalyze_complete_symbol_list": coinalyze_complete_symbols,
            "coinalyze_target_symbols": coinalyze_target_count,
            "coinalyze_enrichment_limit": COINALYZE_ENRICH_LIMIT,
            "coinalyze_targeted_symbol_list": coinalyze_target_symbols,
            "coinalyze_priority_symbols": coinalyze_priority_symbols,
            "coinalyze_priority_targeted_symbols": coinalyze_priority_targeted_symbols,
            "coinalyze_priority_enriched_symbols": coinalyze_priority_enriched_symbols,
            "coinalyze_priority_complete_symbols": coinalyze_priority_complete_symbols,
            "coinalyze_priority_partial_symbols": coinalyze_priority_partial_symbols,
            "coinalyze_priority_missing_symbols": coinalyze_priority_missing_symbols,
            "coinalyze_priority_full_target_coverage": (
                set(coinalyze_priority_targeted_symbols) == set(coinalyze_priority_symbols)
            ),
            "coinalyze_priority_full_enrichment": (
                set(coinalyze_priority_enriched_symbols) == set(coinalyze_priority_symbols)
            ),
            "borrowability_checked_symbols": len(analyses) if borrow_ok else 0,
            "symbols_missing_history": missing_history,
            "momentum_eligible_pairs": len(candidate_pool),
            "momentum_scanned_pairs": len(momentum_analyses),
            "momentum_failed_pairs": len(momentum_failed_symbols),
            "momentum_failed_symbols": momentum_failed_symbols,
            "momentum_calculation_failed_pairs": len(momentum_calculation_failures),
            "momentum_calculation_failures": momentum_calculation_failures,
            "momentum_no_activity_pairs": sum(1 for item in momentum_analyses if item.stage == "NO_ACTIVITY"),
        }
        scan = {
            "data_as_of": now.isoformat(),
            "data_as_of_budapest": now.astimezone(BUDAPEST).isoformat(),
            "data_quality": data_quality,
            "market_regime": regime,
            "longs": long_setups[:10],
            "shorts": short_setups[:10],
            "extended_watchlist": watchlist,
            "liquidity_blocked": liquidity_blocked,
            "momentum_radar": momentum_items,
            "universe_stats": universe_stats,
            "coverage": coverage,
            "exclusions": exclusions[:100],
        }

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        status = {
            "checked_at": now.isoformat(),
            "worker": {
                "status": "ok",
                "source_commit_sha": os.getenv("RAILWAY_GIT_COMMIT_SHA") or None,
                "duration_seconds": round(elapsed, 2),
                **universe_stats,
                **coverage,
                "long_candidates": len(long_setups),
                "short_candidates": len(short_setups),
                "extended_watchlist_items": len(watchlist),
                "liquidity_blocked_items": len(liquidity_blocked),
                "momentum_eligible_pairs": len(candidate_pool),
                "momentum_scanned_pairs": len(momentum_analyses),
                "momentum_failed_pairs": len(momentum_failed_symbols),
                "momentum_failed_symbols": momentum_failed_symbols,
                "momentum_calculation_failed_pairs": len(momentum_calculation_failures),
                "momentum_calculation_failures": momentum_calculation_failures,
                "momentum_no_activity_pairs": sum(1 for item in momentum_analyses if item.stage == "NO_ACTIVITY"),
                "momentum_output_items": len(momentum_items),
            },
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
                    "status": "ok" if coinalyze_ok else "partial",
                    "data_as_of": now.isoformat() if enriched_count > 0 else None,
                    "latency_seconds": 0 if enriched_count > 0 else None,
                    "coverage": (
                        f"rate-budget targets any-field enriched {enriched_count}/{coinalyze_target_count}, "
                        f"complete {coinalyze_complete_count}/{coinalyze_target_count}; "
                        f"compact priority targeted {len(coinalyze_priority_targeted_symbols)}/"
                        f"{len(coinalyze_priority_symbols)}, any-field enriched "
                        f"{len(coinalyze_priority_enriched_symbols)}/"
                        f"{len(coinalyze_priority_symbols)}, complete "
                        f"{len(coinalyze_priority_complete_symbols)}/"
                        f"{len(coinalyze_priority_symbols)}; "
                        f"{len(analyses)} analyzed total"
                    ),
                    "priority_targeted_symbols": coinalyze_priority_targeted_symbols,
                    "priority_enriched_symbols": coinalyze_priority_enriched_symbols,
                    "priority_complete_symbols": coinalyze_priority_complete_symbols,
                    "priority_partial_symbols": coinalyze_priority_partial_symbols,
                    "priority_missing_symbols": coinalyze_priority_missing_symbols,
                    "missing_fields": [] if coinalyze_ok else [coinalyze_error or "enrichment unavailable"],
                },
                {
                    "source": "Bybit EU Spot Margin",
                    "status": "ok" if borrow_ok else "partial",
                    "data_as_of": now.isoformat() if borrow_ok else None,
                    "latency_seconds": 0 if borrow_ok else None,
                    "missing_fields": [] if borrow_ok else [borrow_error or "public borrowability unavailable"],
                },
            ],
        }

        await persist_results(scan, regime, long_setups + short_setups, watchlist, momentum_radar, status)
        print(
            "Worker complete: "
            f"analyzed={len(analyses)}, tradeable={universe_stats['tradeable_universe_size']}, "
            f"watchlist={len(watchlist)}, liquidity_blocked={len(liquidity_blocked)}, "
            f"coinalyze={enriched_count}/{coinalyze_target_count} rate-budget targets, "
            f"priority={len(coinalyze_priority_enriched_symbols)}/"
            f"{len(coinalyze_priority_symbols)} enriched, "
            f"longs={len(long_setups)}, "
            f"shorts={len(short_setups)}, momentum={len(momentum_items)}, "
            f"momentum_coverage={len(momentum_analyses)}/{len(candidate_pool)}, "
            f"quality={data_quality}, duration={elapsed:.1f}s"
        )


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
