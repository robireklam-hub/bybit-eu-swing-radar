from __future__ import annotations

import asyncio

import diagnostics_v073
import diagnostics_v073_perf as perf
import sweep_research
from sweep_research import ResearchBar


def _bar(start: int, close: float, high: float | None = None, low: float | None = None) -> ResearchBar:
    return ResearchBar(
        start_ms=start,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        volume=100.0,
        turnover=1000.0,
    )


def test_fast_15m_classifier_matches_reference() -> None:
    interval = sweep_research.FIFTEEN_MIN_MS
    bars = [
        _bar(0 * interval, 100.0, 101.0, 99.0),
        _bar(1 * interval, 101.0, 102.0, 100.0),
        _bar(2 * interval, 102.0, 103.0, 101.0),
        _bar(3 * interval, 104.0, 105.0, 103.0),
        _bar(4 * interval, 100.0, 101.0, 98.0),
        _bar(5 * interval, 99.0, 100.0, 97.0),
    ]
    for close_ms in range(interval, 7 * interval, interval):
        assert perf.fast_classify_15m_structure(bars, close_ms, 3) == (
            sweep_research.classify_15m_structure(bars, close_ms, 3)
        )


def test_install_is_diagnostics_only_and_forces_one_symbol_batch() -> None:
    live_scan = sweep_research.scan_sweep_setups
    perf.install_performance_patch()
    assert diagnostics_v073.DIAGNOSTIC_BATCH_SYMBOLS == 1
    assert diagnostics_v073.scan_sweep_setups is perf.fast_scan_sweep_setups
    assert sweep_research.scan_sweep_setups is live_scan
    assert diagnostics_v073.insert_events is perf.bulk_insert_events


def test_event_record_serializes_json_columns() -> None:
    item = {column: None for column in perf._EVENT_COLUMNS}
    item["sensitivity"] = {"8": {"gross_r": 1.2}}
    item["candidate_payload"] = {"setup": "x"}
    record = perf._event_record(item)
    assert len(record) == len(perf._EVENT_COLUMNS)
    sensitivity_index = perf._EVENT_COLUMNS.index("sensitivity")
    payload_index = perf._EVENT_COLUMNS.index("candidate_payload")
    assert record[sensitivity_index] == '{"8": {"gross_r": 1.2}}'
    assert record[payload_index] == '{"setup": "x"}'


def test_bulk_insert_uses_executemany_not_per_row_fetchrow() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.count_calls = 0
            self.executemany_calls = 0
            self.rows = 0

        async def fetchval(self, sql: str, *args: object) -> int:
            self.count_calls += 1
            return self.rows

        async def executemany(self, sql: str, records: list[tuple[object, ...]]) -> None:
            self.executemany_calls += 1
            self.rows += len(records)

    item = {column: None for column in perf._EVENT_COLUMNS}
    item.update(
        {
            "job_id": 1,
            "event_key": "event-1",
            "strategy_version": "0.7.3",
            "symbol": "BTCUSDC",
            "side": "long",
            "sensitivity": {},
            "candidate_payload": {},
        }
    )
    connection = FakeConnection()
    inserted = asyncio.run(perf.bulk_insert_events(connection, [item, {**item, "event_key": "event-2"}]))
    assert inserted == 2
    assert connection.executemany_calls == 1
    assert connection.count_calls == 2
