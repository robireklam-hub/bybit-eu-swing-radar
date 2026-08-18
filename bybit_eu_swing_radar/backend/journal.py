"""Compatibility facade for the day-trade journal.

The journal implementation lives in ``journal_core`` unchanged. This facade
keeps schema DDL out of the normal worker hot path once all production journal
relations already exist. It does not change signal lifecycle or outcome logic.
"""
from __future__ import annotations

import asyncpg

import journal_core as _core
from journal_core import *  # noqa: F401,F403


async def ensure_journal_schema(connection: asyncpg.Connection) -> None:
    schema_complete = bool(
        await connection.fetchval(
            """
            SELECT
                to_regclass('public.day_trade_signal_journal') IS NOT NULL
                AND to_regclass('public.day_trade_journal_runs') IS NOT NULL
                AND to_regclass('public.idx_day_journal_open') IS NOT NULL
                AND to_regclass('public.idx_day_journal_opened') IS NOT NULL
                AND to_regclass('public.idx_day_journal_symbol_side') IS NOT NULL
                AND to_regclass('public.idx_day_journal_class') IS NOT NULL
                AND to_regclass('public.idx_day_journal_runs_run_at') IS NOT NULL
            """
        )
    )
    if schema_complete:
        return

    async with connection.transaction():
        await connection.execute("SET LOCAL lock_timeout = '5s'")
        await connection.execute("SET LOCAL statement_timeout = '10s'")
        await connection.execute(_core.SCHEMA_SQL)


# ``persist_day_journal`` resolves ``ensure_journal_schema`` in journal_core's
# module globals at runtime. Point that global at the guarded implementation.
_core.ensure_journal_schema = ensure_journal_schema
persist_day_journal = _core.persist_day_journal


def __getattr__(name: str):
    return getattr(_core, name)
