"""Research-only Bybit EU spot microstructure recorder.

The recorder reconstructs an in-memory L50 order book and aggregates public
trades plus visible-book changes into bounded time buckets. It deliberately
persists derived features rather than every raw L2 update.

It never reads or mutates live strategy/scoring/execution state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import asyncpg
import websockets

logger = logging.getLogger(__name__)

DEFAULT_WS_URL = "wss://stream.bybit.eu/v5/public/spot"
DEFAULT_SYMBOLS = ("BTCUSDC", "ETHUSDC", "SOLUSDC")
LOCK_KEY = "trading-radar-microstructure-recorder-v1"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS microstructure_buckets (
    symbol TEXT NOT NULL,
    bucket_start TIMESTAMPTZ NOT NULL,
    bucket_seconds INTEGER NOT NULL,
    source TEXT NOT NULL,
    trade_count INTEGER NOT NULL DEFAULT 0,
    block_trade_count INTEGER NOT NULL DEFAULT 0,
    rpi_trade_count INTEGER NOT NULL DEFAULT 0,
    taker_buy_base DOUBLE PRECISION NOT NULL DEFAULT 0,
    taker_sell_base DOUBLE PRECISION NOT NULL DEFAULT 0,
    taker_buy_quote DOUBLE PRECISION NOT NULL DEFAULT 0,
    taker_sell_quote DOUBLE PRECISION NOT NULL DEFAULT 0,
    signed_quote_flow DOUBLE PRECISION NOT NULL DEFAULT 0,
    total_quote_volume DOUBLE PRECISION NOT NULL DEFAULT 0,
    trade_vwap DOUBLE PRECISION,
    best_bid DOUBLE PRECISION,
    best_ask DOUBLE PRECISION,
    mid DOUBLE PRECISION,
    spread_bps DOUBLE PRECISION,
    microprice DOUBLE PRECISION,
    bid_depth_5_quote DOUBLE PRECISION,
    ask_depth_5_quote DOUBLE PRECISION,
    bid_depth_10_quote DOUBLE PRECISION,
    ask_depth_10_quote DOUBLE PRECISION,
    bid_depth_50_quote DOUBLE PRECISION,
    ask_depth_50_quote DOUBLE PRECISION,
    imbalance_5 DOUBLE PRECISION,
    imbalance_10 DOUBLE PRECISION,
    imbalance_50 DOUBLE PRECISION,
    bid_added_quote DOUBLE PRECISION NOT NULL DEFAULT 0,
    bid_removed_quote DOUBLE PRECISION NOT NULL DEFAULT 0,
    ask_added_quote DOUBLE PRECISION NOT NULL DEFAULT 0,
    ask_removed_quote DOUBLE PRECISION NOT NULL DEFAULT 0,
    book_message_count INTEGER NOT NULL DEFAULT 0,
    last_trade_at TIMESTAMPTZ,
    last_book_at TIMESTAMPTZ,
    book_update_id BIGINT,
    cross_seq BIGINT,
    book_ready BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, bucket_start, bucket_seconds)
);
CREATE INDEX IF NOT EXISTS idx_microstructure_buckets_time
ON microstructure_buckets(bucket_start DESC);
"""

UPSERT_SQL = """
INSERT INTO microstructure_buckets (
    symbol,bucket_start,bucket_seconds,source,
    trade_count,block_trade_count,rpi_trade_count,
    taker_buy_base,taker_sell_base,taker_buy_quote,taker_sell_quote,
    signed_quote_flow,total_quote_volume,trade_vwap,
    best_bid,best_ask,mid,spread_bps,microprice,
    bid_depth_5_quote,ask_depth_5_quote,bid_depth_10_quote,ask_depth_10_quote,
    bid_depth_50_quote,ask_depth_50_quote,imbalance_5,imbalance_10,imbalance_50,
    bid_added_quote,bid_removed_quote,ask_added_quote,ask_removed_quote,
    book_message_count,last_trade_at,last_book_at,book_update_id,cross_seq,book_ready,
    updated_at
) VALUES (
    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
    $20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36,$37,$38,NOW()
)
ON CONFLICT (symbol,bucket_start,bucket_seconds) DO UPDATE SET
    source=EXCLUDED.source,
    trade_count=EXCLUDED.trade_count,
    block_trade_count=EXCLUDED.block_trade_count,
    rpi_trade_count=EXCLUDED.rpi_trade_count,
    taker_buy_base=EXCLUDED.taker_buy_base,
    taker_sell_base=EXCLUDED.taker_sell_base,
    taker_buy_quote=EXCLUDED.taker_buy_quote,
    taker_sell_quote=EXCLUDED.taker_sell_quote,
    signed_quote_flow=EXCLUDED.signed_quote_flow,
    total_quote_volume=EXCLUDED.total_quote_volume,
    trade_vwap=EXCLUDED.trade_vwap,
    best_bid=EXCLUDED.best_bid,best_ask=EXCLUDED.best_ask,mid=EXCLUDED.mid,
    spread_bps=EXCLUDED.spread_bps,microprice=EXCLUDED.microprice,
    bid_depth_5_quote=EXCLUDED.bid_depth_5_quote,
    ask_depth_5_quote=EXCLUDED.ask_depth_5_quote,
    bid_depth_10_quote=EXCLUDED.bid_depth_10_quote,
    ask_depth_10_quote=EXCLUDED.ask_depth_10_quote,
    bid_depth_50_quote=EXCLUDED.bid_depth_50_quote,
    ask_depth_50_quote=EXCLUDED.ask_depth_50_quote,
    imbalance_5=EXCLUDED.imbalance_5,imbalance_10=EXCLUDED.imbalance_10,
    imbalance_50=EXCLUDED.imbalance_50,
    bid_added_quote=EXCLUDED.bid_added_quote,bid_removed_quote=EXCLUDED.bid_removed_quote,
    ask_added_quote=EXCLUDED.ask_added_quote,ask_removed_quote=EXCLUDED.ask_removed_quote,
    book_message_count=EXCLUDED.book_message_count,
    last_trade_at=EXCLUDED.last_trade_at,last_book_at=EXCLUDED.last_book_at,
    book_update_id=EXCLUDED.book_update_id,cross_seq=EXCLUDED.cross_seq,
    book_ready=EXCLUDED.book_ready,updated_at=NOW()
"""


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _utc_from_ms(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc) if value else None


def _bucket_start_ms(timestamp_ms: int, bucket_seconds: int) -> int:
    width = bucket_seconds * 1000
    return timestamp_ms - (timestamp_ms % width)


def _imbalance(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    total = bid + ask
    return (bid - ask) / total if total > 0 else None


@dataclass(frozen=True)
class MicrostructureConfig:
    enabled: bool
    database_url: str
    ws_url: str
    symbols: tuple[str, ...]
    bucket_seconds: int = 5
    depth: int = 50
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "MicrostructureConfig":
        symbols = tuple(
            dict.fromkeys(
                item.strip().upper()
                for item in os.getenv(
                    "MICROSTRUCTURE_SYMBOLS", ",".join(DEFAULT_SYMBOLS)
                ).split(",")
                if item.strip()
            )
        )
        bucket_seconds = max(1, min(int(os.getenv("MICROSTRUCTURE_BUCKET_SECONDS", "5")), 60))
        depth = int(os.getenv("MICROSTRUCTURE_DEPTH", "50"))
        config = cls(
            enabled=_truthy(os.getenv("MICROSTRUCTURE_RECORDER_ENABLED"), default=True),
            database_url=os.getenv("DATABASE_URL", ""),
            ws_url=os.getenv("MICROSTRUCTURE_WS_URL", DEFAULT_WS_URL).strip(),
            symbols=symbols,
            bucket_seconds=bucket_seconds,
            depth=depth,
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.depth != 50:
            raise ValueError("microstructure v1 requires L50 order book")
        if not self.symbols:
            raise ValueError("at least one microstructure symbol is required")
        if any(not symbol.endswith("USDC") for symbol in self.symbols):
            raise ValueError("microstructure recorder is USDC-only")
        if len(self.symbols) > 12:
            raise ValueError("microstructure v1 is capped at 12 symbols for bounded storage/load")
        if not self.ws_url.startswith("wss://"):
            raise ValueError("MICROSTRUCTURE_WS_URL must be wss://")


@dataclass
class OrderBookState:
    symbol: str
    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    ready: bool = False
    update_id: int | None = None
    cross_seq: int | None = None
    last_cts_ms: int | None = None

    def apply(self, message_type: str, data: dict[str, Any]) -> dict[str, float]:
        if message_type == "snapshot" or int(data.get("u") or 0) == 1:
            self.bids.clear()
            self.asks.clear()
            self.ready = True
            changes = {"bid_added": 0.0, "bid_removed": 0.0, "ask_added": 0.0, "ask_removed": 0.0}
            for price, size in data.get("b") or []:
                p, q = _finite(price), _finite(size)
                if p is not None and q is not None and q > 0:
                    self.bids[p] = q
            for price, size in data.get("a") or []:
                p, q = _finite(price), _finite(size)
                if p is not None and q is not None and q > 0:
                    self.asks[p] = q
        else:
            changes = self._apply_delta(data)
        self.update_id = int(data["u"]) if data.get("u") is not None else self.update_id
        self.cross_seq = int(data["seq"]) if data.get("seq") is not None else self.cross_seq
        return changes

    def _apply_delta(self, data: dict[str, Any]) -> dict[str, float]:
        changes = {"bid_added": 0.0, "bid_removed": 0.0, "ask_added": 0.0, "ask_removed": 0.0}
        for side_name, levels, book in (
            ("bid", data.get("b") or [], self.bids),
            ("ask", data.get("a") or [], self.asks),
        ):
            for raw_price, raw_size in levels:
                price, size = _finite(raw_price), _finite(raw_size)
                if price is None or size is None:
                    continue
                previous = book.get(price, 0.0)
                delta = size - previous
                if delta > 0:
                    changes[f"{side_name}_added"] += price * delta
                elif delta < 0:
                    changes[f"{side_name}_removed"] += price * abs(delta)
                if size <= 0:
                    book.pop(price, None)
                else:
                    book[price] = size
        return changes


def depth_metrics(book: OrderBookState) -> dict[str, float | None]:
    if not book.ready or not book.bids or not book.asks:
        return {}
    bids = sorted(book.bids.items(), reverse=True)[:50]
    asks = sorted(book.asks.items())[:50]
    best_bid, best_bid_size = bids[0]
    best_ask, best_ask_size = asks[0]
    if best_bid <= 0 or best_ask <= best_bid:
        return {}
    mid = (best_bid + best_ask) / 2.0
    spread_bps = (best_ask - best_bid) / mid * 10_000.0
    denom = best_bid_size + best_ask_size
    microprice = (
        (best_ask * best_bid_size + best_bid * best_ask_size) / denom
        if denom > 0
        else mid
    )

    def qdepth(levels: list[tuple[float, float]], n: int) -> float:
        return sum(price * size for price, size in levels[:n])

    bid5, ask5 = qdepth(bids, 5), qdepth(asks, 5)
    bid10, ask10 = qdepth(bids, 10), qdepth(asks, 10)
    bid50, ask50 = qdepth(bids, 50), qdepth(asks, 50)
    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "mid": mid,
        "spread_bps": spread_bps,
        "microprice": microprice,
        "bid_depth_5_quote": bid5,
        "ask_depth_5_quote": ask5,
        "bid_depth_10_quote": bid10,
        "ask_depth_10_quote": ask10,
        "bid_depth_50_quote": bid50,
        "ask_depth_50_quote": ask50,
        "imbalance_5": _imbalance(bid5, ask5),
        "imbalance_10": _imbalance(bid10, ask10),
        "imbalance_50": _imbalance(bid50, ask50),
    }


@dataclass
class ResearchBucket:
    symbol: str
    start_ms: int
    bucket_seconds: int
    trade_count: int = 0
    block_trade_count: int = 0
    rpi_trade_count: int = 0
    taker_buy_base: float = 0.0
    taker_sell_base: float = 0.0
    taker_buy_quote: float = 0.0
    taker_sell_quote: float = 0.0
    total_quote_volume: float = 0.0
    weighted_price_numerator: float = 0.0
    bid_added_quote: float = 0.0
    bid_removed_quote: float = 0.0
    ask_added_quote: float = 0.0
    ask_removed_quote: float = 0.0
    book_message_count: int = 0
    last_trade_ms: int | None = None
    last_book_ms: int | None = None
    book_update_id: int | None = None
    cross_seq: int | None = None
    book_ready: bool = False
    book_metrics: dict[str, float | None] = field(default_factory=dict)

    def add_trade(self, trade: dict[str, Any]) -> None:
        price, size = _finite(trade.get("p")), _finite(trade.get("v"))
        if price is None or size is None or price <= 0 or size <= 0:
            return
        quote = price * size
        self.trade_count += 1
        self.total_quote_volume += quote
        self.weighted_price_numerator += quote * price
        side = str(trade.get("S") or "").upper()
        if side == "BUY":
            self.taker_buy_base += size
            self.taker_buy_quote += quote
        elif side == "SELL":
            self.taker_sell_base += size
            self.taker_sell_quote += quote
        if bool(trade.get("BT")):
            self.block_trade_count += 1
        if bool(trade.get("RPI")):
            self.rpi_trade_count += 1
        timestamp = trade.get("T")
        if timestamp is not None:
            self.last_trade_ms = max(self.last_trade_ms or 0, int(timestamp))

    def add_book(
        self,
        metrics: dict[str, float | None],
        changes: dict[str, float],
        *,
        cts_ms: int | None,
        update_id: int | None,
        cross_seq: int | None,
        ready: bool,
    ) -> None:
        self.book_message_count += 1
        self.bid_added_quote += changes.get("bid_added", 0.0)
        self.bid_removed_quote += changes.get("bid_removed", 0.0)
        self.ask_added_quote += changes.get("ask_added", 0.0)
        self.ask_removed_quote += changes.get("ask_removed", 0.0)
        if metrics:
            self.book_metrics = dict(metrics)
        self.book_ready = ready and bool(metrics)
        self.last_book_ms = max(self.last_book_ms or 0, cts_ms or 0) or self.last_book_ms
        self.book_update_id = update_id
        self.cross_seq = cross_seq

    def db_values(self, source: str) -> tuple[Any, ...]:
        trade_vwap = (
            self.weighted_price_numerator / self.total_quote_volume
            if self.total_quote_volume > 0
            else None
        )
        m = self.book_metrics
        return (
            self.symbol,
            _utc_from_ms(self.start_ms),
            self.bucket_seconds,
            source,
            self.trade_count,
            self.block_trade_count,
            self.rpi_trade_count,
            self.taker_buy_base,
            self.taker_sell_base,
            self.taker_buy_quote,
            self.taker_sell_quote,
            self.taker_buy_quote - self.taker_sell_quote,
            self.total_quote_volume,
            trade_vwap,
            m.get("best_bid"),m.get("best_ask"),m.get("mid"),m.get("spread_bps"),m.get("microprice"),
            m.get("bid_depth_5_quote"),m.get("ask_depth_5_quote"),
            m.get("bid_depth_10_quote"),m.get("ask_depth_10_quote"),
            m.get("bid_depth_50_quote"),m.get("ask_depth_50_quote"),
            m.get("imbalance_5"),m.get("imbalance_10"),m.get("imbalance_50"),
            self.bid_added_quote,self.bid_removed_quote,self.ask_added_quote,self.ask_removed_quote,
            self.book_message_count,
            _utc_from_ms(self.last_trade_ms),_utc_from_ms(self.last_book_ms),
            self.book_update_id,self.cross_seq,self.book_ready,
        )


@dataclass
class RecorderRuntime:
    running: bool = False
    singleton_acquired: bool = False
    connected: bool = False
    started_at: str | None = None
    last_message_at: str | None = None
    last_write_at: str | None = None
    last_error_at: str | None = None
    last_error: str | None = None
    reconnects: int = 0
    messages: int = 0
    rows_written: int = 0

    def payload(self, config: MicrostructureConfig) -> dict[str, Any]:
        return {
            "research_only": True,
            "live_strategy_mutated": False,
            "enabled": config.enabled,
            "running": self.running,
            "singleton_acquired": self.singleton_acquired,
            "connected": self.connected,
            "source": "BYBIT_EU_SPOT_PUBLIC_WS",
            "ws_url": config.ws_url,
            "symbols": list(config.symbols),
            "depth": config.depth,
            "bucket_seconds": config.bucket_seconds,
            "started_at": self.started_at,
            "last_message_at": self.last_message_at,
            "last_write_at": self.last_write_at,
            "last_error_at": self.last_error_at,
            "last_error": self.last_error,
            "reconnects": self.reconnects,
            "messages": self.messages,
            "rows_written": self.rows_written,
        }


class MicrostructureRecorder:
    def __init__(self, config: MicrostructureConfig):
        self.config = config
        self.runtime = RecorderRuntime()
        self.books = {symbol: OrderBookState(symbol) for symbol in config.symbols}
        self.buckets: dict[tuple[str, int], ResearchBucket] = {}
        self._stop = asyncio.Event()
        self._db: asyncpg.Connection | None = None

    @property
    def source(self) -> str:
        return "BYBIT_EU_SPOT_PUBLIC_WS"

    def status(self) -> dict[str, Any]:
        return self.runtime.payload(self.config)

    async def stop(self) -> None:
        self._stop.set()

    def _bucket(self, symbol: str, timestamp_ms: int) -> ResearchBucket:
        start = _bucket_start_ms(timestamp_ms, self.config.bucket_seconds)
        key = (symbol, start)
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = ResearchBucket(symbol=symbol, start_ms=start, bucket_seconds=self.config.bucket_seconds)
            self.buckets[key] = bucket
        return bucket

    async def run(self) -> None:
        if not self.config.enabled:
            return
        if not self.config.database_url:
            self._record_error("DATABASE_URL is not configured")
            return
        self.runtime.running = True
        self.runtime.started_at = datetime.now(timezone.utc).isoformat()
        try:
            self._db = await asyncpg.connect(self.config.database_url)
            acquired = await self._db.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", LOCK_KEY)
            self.runtime.singleton_acquired = bool(acquired)
            if not acquired:
                logger.info("microstructure recorder singleton already active")
                return
            await self._db.execute(SCHEMA_SQL)
            flush_task = asyncio.create_task(self._flush_loop(), name="microstructure-flush")
            try:
                await self._connection_loop()
            finally:
                self._stop.set()
                await flush_task
                await self._flush_closed(force=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record_error(exc)
            logger.exception("microstructure recorder stopped unexpectedly")
        finally:
            self.runtime.connected = False
            self.runtime.running = False
            if self._db is not None:
                try:
                    if self.runtime.singleton_acquired:
                        await self._db.execute("SELECT pg_advisory_unlock(hashtext($1))", LOCK_KEY)
                    await self._db.close()
                except Exception:
                    logger.exception("microstructure recorder DB cleanup failed")
                self._db = None

    async def _connection_loop(self) -> None:
        delay = self.config.reconnect_min_seconds
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.config.ws_url,
                    ping_interval=None,
                    close_timeout=5,
                    max_queue=4096,
                ) as ws:
                    self.runtime.connected = True
                    await self._subscribe(ws)
                    delay = self.config.reconnect_min_seconds
                    heartbeat = asyncio.create_task(self._heartbeat(ws), name="microstructure-heartbeat")
                    try:
                        async for raw in ws:
                            if self._stop.is_set():
                                break
                            self._handle_message(raw)
                    finally:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)
                        self.runtime.connected = False
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.runtime.connected = False
                self.runtime.reconnects += 1
                self._record_error(exc)
                logger.warning("microstructure websocket reconnect: %s", exc)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2.0, self.config.reconnect_max_seconds)

    async def _subscribe(self, ws: Any) -> None:
        topics: list[str] = []
        for symbol in self.config.symbols:
            topics.extend((f"orderbook.{self.config.depth}.{symbol}", f"publicTrade.{symbol}"))
        # Bybit spot accepts at most 10 args in one subscription request.
        for index in range(0, len(topics), 10):
            await ws.send(json.dumps({"op": "subscribe", "args": topics[index:index + 10]}))

    async def _heartbeat(self, ws: Any) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(20)
            await ws.send(json.dumps({"op": "ping"}))

    def _handle_message(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return
        topic = str(message.get("topic") or "")
        if not topic:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        self.runtime.last_message_at = now_iso
        self.runtime.messages += 1
        if topic.startswith("publicTrade."):
            self._handle_trades(message)
        elif topic.startswith("orderbook."):
            self._handle_book(message)

    def _handle_trades(self, message: dict[str, Any]) -> None:
        for trade in message.get("data") or []:
            symbol = str(trade.get("s") or "").upper()
            if symbol not in self.books:
                continue
            timestamp_ms = int(trade.get("T") or message.get("ts") or int(time.time() * 1000))
            self._bucket(symbol, timestamp_ms).add_trade(trade)

    def _handle_book(self, message: dict[str, Any]) -> None:
        data = message.get("data") or {}
        symbol = str(data.get("s") or "").upper()
        book = self.books.get(symbol)
        if book is None:
            return
        cts_ms = int(data.get("cts") or message.get("cts") or message.get("ts") or int(time.time() * 1000))
        book.last_cts_ms = cts_ms
        changes = book.apply(str(message.get("type") or "delta"), data)
        metrics = depth_metrics(book)
        self._bucket(symbol, cts_ms).add_book(
            metrics,
            changes,
            cts_ms=cts_ms,
            update_id=book.update_id,
            cross_seq=book.cross_seq,
            ready=book.ready,
        )

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.config.bucket_seconds)
            except asyncio.TimeoutError:
                pass
            await self._flush_closed(force=False)

    async def _flush_closed(self, *, force: bool) -> None:
        if self._db is None:
            return
        now_ms = int(time.time() * 1000)
        current_start = _bucket_start_ms(now_ms, self.config.bucket_seconds)
        keys = [key for key in self.buckets if force or key[1] < current_start]
        if not keys:
            return
        rows = [self.buckets[key].db_values(self.source) for key in sorted(keys)]
        async with self._db.transaction():
            await self._db.executemany(UPSERT_SQL, rows)
        for key in keys:
            self.buckets.pop(key, None)
        self.runtime.rows_written += len(rows)
        self.runtime.last_write_at = datetime.now(timezone.utc).isoformat()

    def _record_error(self, exc: Exception | str) -> None:
        self.runtime.last_error = str(exc)[:1000]
        self.runtime.last_error_at = datetime.now(timezone.utc).isoformat()


def subscription_topics(config: MicrostructureConfig) -> list[list[str]]:
    topics: list[str] = []
    for symbol in config.symbols:
        topics.extend((f"orderbook.{config.depth}.{symbol}", f"publicTrade.{symbol}"))
    return [topics[index:index + 10] for index in range(0, len(topics), 10)]
